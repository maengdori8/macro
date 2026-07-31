"""Live, click-free soak runner for strictly inactive FC Online A/S SKIP.

The runner never executes normal manager-mode targets.  It only captures the FC
Online window, detects the A/S SKIP templates, tries the configured safe input
candidates, and records whether the foreground HWND remained unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from macroapp import input_message  # noqa: E402
from macroapp import ocr as rank_ocr  # noqa: E402
from macroapp.config import (  # noqa: E402
    SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS,
    SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
    SKIP_EXPERIMENT_CONTROL_SECONDS,
    SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
    SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
    SKIP_INACTIVE_EXPERIMENT_CANDIDATES,
    SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES,
    SKIP_OCR_BOTTOM_FRACTION,
    SKIP_OCR_LEFT_FRACTION,
    SKIP_OCR_RIGHT_FRACTION,
    SKIP_OCR_TOP_FRACTION,
    WINDOW_TITLE,
)
from macroapp.gui import AutomationApp  # noqa: E402
from macroapp.license_client import get_hwid  # noqa: E402
from macroapp.skip_experiment import (  # noqa: E402
    SkipExperimentTracker,
    SkipOutcome,
    load_device_learning,
    run_guarded_inactive_action,
    save_device_learning,
)
from macroapp.window import InactiveManager  # noqa: E402


GONE_CONFIRM_FRAMES = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="seconds to run; 0 keeps running until stopped",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "logs" / "skip_inactive_soak.jsonl",
    )
    return parser.parse_args()


class EventWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.device_id = get_hwid()
        except Exception:
            self.device_id = "unknown"

    def write(
        self,
        event: str,
        *,
        outcome: SkipOutcome | None = None,
        guard=None,
        **fields,
    ) -> None:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "unix_time": time.time(),
            "pid": os.getpid(),
            "device_id": self.device_id,
            "event": event,
            **fields,
        }
        if outcome is not None:
            payload["outcome"] = {
                "status": outcome.status,
                "candidate": outcome.candidate,
                "latency_seconds": outcome.latency_seconds,
                "learned": outcome.learned,
                "detail": outcome.detail,
            }
        if guard is not None:
            payload["guard"] = {
                "attempted": guard.attempted,
                "action_ok": guard.action_ok,
                "foreground_before": guard.foreground_before,
                "foreground_after": guard.foreground_after,
                "foreground_samples": list(guard.foreground_samples),
                "invariant_ok": guard.invariant_ok,
                "reason": guard.reason,
                "elapsed_seconds": guard.elapsed_seconds,
            }
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def crop_skip_region(frame):
    height, width = frame.shape[:2]
    x1 = max(0, int(width * SKIP_OCR_LEFT_FRACTION))
    x2 = min(width, int(width * SKIP_OCR_RIGHT_FRACTION))
    y1 = max(0, int(height * SKIP_OCR_TOP_FRACTION))
    y2 = min(height, int(height * SKIP_OCR_BOTTOM_FRACTION))
    return frame[y1:y2, x1:x2]


def main() -> int:
    args = parse_args()
    writer = EventWriter(args.output.resolve())
    manager = InactiveManager(WINDOW_TITLE, logger=lambda message: writer.write(
        "runtime_log", message=str(message)
    ))
    learning_path = ROOT / "logs" / "skip_learning.json"
    learned = load_device_learning(learning_path, writer.device_id)
    trackers = {
        "a": SkipExperimentTracker(
        SKIP_INACTIVE_EXPERIMENT_CANDIDATES,
        result_window_seconds=SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
        attempt_gap_seconds=SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS,
        confirm_successes=SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
            control_seconds=SKIP_EXPERIMENT_CONTROL_SECONDS,
            learned=learned.get("a"),
        ),
        "s": SkipExperimentTracker(
            SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES,
            result_window_seconds=SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
            attempt_gap_seconds=SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS,
            confirm_successes=SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
            control_seconds=SKIP_EXPERIMENT_CONTROL_SECONDS,
            learned=learned.get("s"),
        ),
    }
    rank_ocr.reset_skip_a_template()

    if not manager.find_window() or not manager.hwnd:
        writer.write("startup_failed", reason="fc_window_not_found")
        return 2

    writer.write(
        "started",
        hwnd=int(manager.hwnd),
        candidates={
            "a": list(SKIP_INACTIVE_EXPERIMENT_CANDIDATES),
            "s": list(SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES),
        },
        restored=learned,
        duration_seconds=float(args.duration),
    )
    started = time.monotonic()
    episode = 0
    prompt_active = False
    prompt_variant = None
    gone_streak = 0

    try:
        while args.duration <= 0 or time.monotonic() - started < args.duration:
            if not manager.is_valid_window():
                writer.write("window_lost", hwnd=int(manager.hwnd or 0))
                manager.stop_capture()
                if not manager.find_window():
                    time.sleep(1.0)
                    continue

            frame = manager.capture_client_area(window_validated=True)
            if frame is None:
                continue

            now = time.monotonic()
            crop = crop_skip_region(frame)
            is_a, a_score, _a_center = rank_ocr.match_skip_a(crop, ROOT)
            is_s, s_score, _s_center = rank_ocr.match_skip_s(crop, ROOT)
            if is_a or is_s:
                gone_streak = 0
                if not prompt_active:
                    prompt_active = True
                    prompt_variant = "a" if is_a else "s"
                    episode += 1
                    writer.write(
                        "prompt_detected",
                        episode=episode,
                        variant=prompt_variant,
                        match_score=float(
                            a_score if prompt_variant == "a" else s_score
                        ),
                        hwnd=int(manager.hwnd or 0),
                        foreground_hwnd=input_message.get_foreground_hwnd(),
                    )

                tracker = trackers[prompt_variant]
                score = a_score if prompt_variant == "a" else s_score
                candidate, expired = tracker.choose(now)
                if expired is not None:
                    writer.write(
                        "outcome",
                        episode=episode,
                        variant=prompt_variant,
                        outcome=expired,
                        match_score=float(score),
                    )
                if candidate is None:
                    continue

                guard = run_guarded_inactive_action(
                    lambda: AutomationApp._press_skip_candidate(candidate, manager),
                    input_message.get_foreground_hwnd,
                    int(manager.hwnd or 0),
                    poll_seconds=SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
                )
                outcome = tracker.record_attempt(
                    candidate,
                    time.monotonic(),
                    guard,
                )
                writer.write(
                    "attempt",
                    episode=episode,
                    variant=prompt_variant,
                    outcome=outcome,
                    guard=guard,
                    match_score=float(score),
                )
                continue

            if not prompt_active:
                continue
            gone_streak += 1
            if gone_streak < GONE_CONFIRM_FRAMES:
                continue

            tracker = trackers[prompt_variant]
            outcome = tracker.prompt_disappeared(now)
            writer.write(
                "prompt_disappeared",
                episode=episode,
                variant=prompt_variant,
                outcome=outcome,
                foreground_hwnd=input_message.get_foreground_hwnd(),
            )
            if outcome.learned:
                save_device_learning(
                    learning_path,
                    writer.device_id,
                    prompt_variant,
                    outcome.learned,
                )
            prompt_active = False
            prompt_variant = None
            gone_streak = 0
    except KeyboardInterrupt:
        writer.write("stopped", reason="keyboard_interrupt")
    except Exception as exc:  # noqa: BLE001 - the audit log must survive live failures
        writer.write(
            "crashed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1
    finally:
        manager.stop_capture()
        writer.write(
            "finished",
            elapsed_seconds=time.monotonic() - started,
            episodes=episode,
            learned={
                variant: tracker.learned
                for variant, tracker in trackers.items()
            },
            quarantined={
                variant: sorted(tracker.quarantined)
                for variant, tracker in trackers.items()
            },
            success_counts={
                variant: tracker.success_counts
                for variant, tracker in trackers.items()
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Replay a screen-recorded FC popup sequence through the v12 transition path."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import (  # noqa: E402
    PriceState,
    RenewalCalibratedPriceClassifier,
    RenewalModalGuard,
    RenewalTransitionGuard,
    crop_price_from_guard,
    decode_gray_png,
    dynamic_limit_price_boxes,
    gate_transition_classification,
    load_renewal_profile,
    price_box_in_guard,
)


def percentile(values: list[float], rank: float) -> float:
    return (
        float(np.percentile(np.asarray(values, dtype=np.float64), rank))
        if values
        else 0.0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--minimum-events", type=int, default=10)
    args = parser.parse_args()

    profile = load_renewal_profile()
    side = profile.side(args.side)
    calibrated_size = side.calibrated_frame_size()
    if calibrated_size is None:
        raise RuntimeError("저장된 갱신 보정 크기가 없습니다.")
    frame_width, frame_height = calibrated_size
    if side.price_rect is None or side.guard_rect is None:
        raise RuntimeError("저장된 갱신 가격/가드 영역이 없습니다.")

    price_box = price_box_in_guard(
        side.price_rect,
        side.guard_rect,
        frame_width,
        frame_height,
    )
    dynamic_boxes = dynamic_limit_price_boxes(
        price_box,
        args.side,
        frame_height,
    )
    opened = RenewalModalGuard(
        decode_gray_png(side.guard_png),
        price_box,
        side.registration_shift_limit,
        dynamic_boxes,
    )
    closed = RenewalModalGuard(
        decode_gray_png(side.closed_guard_png),
        price_box,
        side.registration_shift_limit,
        dynamic_boxes,
    )
    transition = RenewalTransitionGuard(opened, closed)
    classifier = RenewalCalibratedPriceClassifier(
        decode_gray_png(side.baseline_png),
        [
            decode_gray_png(encoded)
            for encoded in side.baseline_variants_png
        ],
        side.unchanged_limit,
        side.stability_limit,
    )
    gx1, gy1, gx2, gy2 = side.guard_rect.to_pixels(
        frame_width,
        frame_height,
    )

    capture = cv2.VideoCapture(str(args.video))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps <= 0:
        raise RuntimeError("영상 FPS를 읽지 못했습니다.")

    events: list[dict[str, object]] = []
    event_start: int | None = None
    last_result = None
    stable_count = 0
    first_complete: int | None = None
    blocked_early_changes = 0
    maximum_shift = 0
    processing_ms: list[float] = []
    frame_index = 0

    def finish_event(end_frame: int) -> None:
        nonlocal event_start, last_result, stable_count, first_complete
        if event_start is not None:
            events.append(
                {
                    "start_frame": event_start,
                    "end_frame": end_frame,
                    "decision_frame": None,
                    "decision": None,
                    "first_complete_frame": first_complete,
                    "decision_ms": None,
                }
            )
        event_start = None
        last_result = None
        stable_count = 0
        first_complete = None

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame.shape[1] < frame_width or frame.shape[0] < frame_height:
            frame_index += 1
            continue
        gray = cv2.cvtColor(
            frame[:frame_height, :frame_width],
            cv2.COLOR_BGR2GRAY,
        )
        guard = gray[gy1:gy2, gx1:gx2]
        processing_started = time.perf_counter()
        registration = transition.register(guard)
        if not registration.valid:
            if event_start is not None:
                finish_event(frame_index - 1)
            frame_index += 1
            continue

        if event_start is None:
            event_start = frame_index
        result = classifier.classify_transition(
            crop_price_from_guard(guard, price_box)
        )
        gated = gate_transition_classification(
            result,
            registration.progress,
        )
        processing_ms.append(
            (time.perf_counter() - processing_started) * 1000.0
        )
        current_processing_ms = processing_ms[-1]
        if (
            result.state is PriceState.CHANGED
            and gated.state is PriceState.AMBIGUOUS
        ):
            blocked_early_changes += 1
        if gated.state is PriceState.AMBIGUOUS:
            last_result = gated
            stable_count = 0
            frame_index += 1
            continue

        if first_complete is None:
            first_complete = frame_index
        maximum_shift = max(
            maximum_shift,
            abs(gated.shift_x),
            abs(gated.shift_y),
        )
        if (
            last_result is not None
            and classifier.same_candidate(last_result, gated)
        ):
            stable_count += 1
        else:
            stable_count = 1
        last_result = gated
        if stable_count >= 2:
            events.append(
                {
                    "start_frame": event_start,
                    "end_frame": frame_index,
                    "decision_frame": frame_index,
                    "decision": gated.state.value,
                    "first_complete_frame": first_complete,
                    "decision_ms": (
                        (frame_index - event_start) / fps * 1000.0
                    ),
                    "processing_ms": current_processing_ms,
                }
            )
            event_start = None
            last_result = None
            stable_count = 0
            first_complete = None
            # Ignore the remainder of this popup until the transition guard
            # becomes invalid again.
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_index += 1
                gray = cv2.cvtColor(
                    frame[:frame_height, :frame_width],
                    cv2.COLOR_BGR2GRAY,
                )
                guard = gray[gy1:gy2, gx1:gx2]
                if not transition.register(guard).valid:
                    break
            frame_index += 1
            continue
        frame_index += 1
    capture.release()
    if event_start is not None:
        finish_event(frame_index - 1)

    decided = [event for event in events if event["decision"] is not None]
    decision_ms = [
        float(event["decision_ms"])
        for event in decided
        if event["decision_ms"] is not None
    ]
    decision_processing_ms = [
        float(event["processing_ms"])
        for event in decided
        if event.get("processing_ms") is not None
    ]
    non_unchanged = [
        event for event in decided if event["decision"] != "unchanged"
    ]
    report = {
        "schema_version": 1,
        "video": str(args.video),
        "side": args.side,
        "fps": fps,
        "events": events,
        "decided_events": len(decided),
        "non_unchanged_decisions": non_unchanged,
        "blocked_early_change_frames": blocked_early_changes,
        "maximum_price_shift": maximum_shift,
        "decision_ms": {
            "p50": percentile(decision_ms, 50),
            "p95": percentile(decision_ms, 95),
            "maximum": max(decision_ms, default=0.0),
        },
        "processing_ms": {
            "p50": percentile(processing_ms, 50),
            "p95": percentile(processing_ms, 95),
            "p99": percentile(processing_ms, 99),
            "maximum": max(processing_ms, default=0.0),
        },
        "decision_processing_ms": {
            "p50": percentile(decision_processing_ms, 50),
            "p95": percentile(decision_processing_ms, 95),
            "p99": percentile(decision_processing_ms, 99),
            "maximum": max(decision_processing_ms, default=0.0),
        },
    }
    report["passed"] = bool(
        len(decided) >= max(1, int(args.minimum_events))
        and not non_unchanged
        and maximum_shift
        <= 12
        and report["decision_ms"]["p95"] <= 175.0
        and report["decision_processing_ms"]["p99"] <= 2.0
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

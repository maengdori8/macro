"""Follow strict inactive-SKIP evidence and summarize the active classifier run."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:
    import win32gui
except ImportError:  # pragma: no cover - optional outside Windows
    win32gui = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--fc-hwnd", type=int, required=True)
    parser.add_argument("--state-seconds", type=float, default=45.0)
    parser.add_argument("--since", default="")
    parser.add_argument(
        "--method",
        default="",
        help="candidate action whose strict cross-prompt streak is audited",
    )
    parser.add_argument(
        "--candidate",
        default="",
        help="exact candidate name whose strict streak is audited",
    )
    args = parser.parse_args()

    state = {
        "real": 0,
        "ok": 0,
        "fail": 0,
        "streak": 0,
        "sham": 0,
        "sham_hold": 0,
        "sham_exit": 0,
        "A": 0,
        "S": 0,
        "G": 0,
        "focus": 0,
        "contam": 0,
        "unattributed": 0,
        "hint_mismatch": 0,
        "fc_foreground_samples": 0,
        "unsafe_scope": 0,
        "method_real": 0,
        "method_ok": 0,
        "method_fail": 0,
        "method_streak": 0,
        "method_any_key": 0,
        "method_escape": 0,
        "method_A": 0,
        "method_S": 0,
    }
    pending: dict[tuple[str, str], dict[str, object]] = {}

    def process(event: dict[str, object], *, announce: bool) -> None:
        if int(event.get("classifier_generation", 0) or 0) != args.generation:
            return
        status = str(event.get("status", ""))
        variant = str(event.get("variant", ""))
        candidate_value = event.get("candidate")
        candidate = None if candidate_value is None else str(candidate_value)
        hint = event.get("prompt_hint")
        timestamp = event.get("timestamp")
        if args.since and str(timestamp or "") < args.since:
            return
        key = (variant, candidate or "")

        if status == "pending" and candidate:
            candidate_meta = event.get("candidate_meta") or {}
            pending[key] = {
                "hint": hint,
                "guard": event.get("guard") or {},
                "scope": candidate_meta.get("input_scope")
                if isinstance(candidate_meta, dict) else None,
                "action": candidate_meta.get("action")
                if isinstance(candidate_meta, dict) else None,
            }
            return
        if status == "unattributed":
            state["unattributed"] += 1
            if str(event.get("detail", "")).startswith("non_skip_action"):
                state["contam"] += 1
            return
        if status not in {"success", "failed", "quarantined"} or not candidate:
            return

        pending_event = pending.pop(
            key,
            {
                "hint": hint,
                "guard": event.get("guard") or {},
                "scope": None,
                "action": None,
            },
        )
        pending_hint = pending_event.get("hint")
        if pending_hint != hint:
            state["hint_mismatch"] += 1
            print(
                "HINT_MISMATCH",
                timestamp,
                variant,
                candidate,
                pending_hint,
                "->",
                hint,
                flush=True,
            )

        guard = event.get("guard") or pending_event.get("guard") or {}
        if isinstance(guard, dict):
            before = guard.get("foreground_before")
            after = guard.get("foreground_after")
            if guard and (
                guard.get("invariant_ok") is False
                or before == args.fc_hwnd
                or after == args.fc_hwnd
                or (
                    before is not None
                    and after is not None
                    and before != after
                )
            ):
                state["focus"] += 1
        scope = pending_event.get("scope")
        if scope not in {None, "none", "target_window", "virtual_gamepad"}:
            state["unsafe_scope"] += 1

        detail = str(event.get("detail", ""))
        if detail.startswith("non_skip_action"):
            state["contam"] += 1

        if candidate == "control_noop":
            state["sham"] += 1
            if status == "success":
                state["sham_exit"] += 1
            else:
                state["sham_hold"] += 1
        else:
            state["real"] += 1
            if status == "success":
                state["ok"] += 1
                state["streak"] += 1
                if variant == "a":
                    state["A"] += 1
                elif variant == "s":
                    state["S"] += 1
                elif variant == "generic":
                    state["G"] += 1
            else:
                state["fail"] += 1
                state["streak"] = 0

            action = str(pending_event.get("action") or candidate)
            method_match = bool(
                (args.candidate and candidate == args.candidate)
                or (
                    not args.candidate
                    and args.method
                    and action == args.method
                )
            )
            if method_match:
                state["method_real"] += 1
                if status == "success":
                    state["method_ok"] += 1
                    state["method_streak"] += 1
                    if hint == "any_key":
                        state["method_any_key"] += 1
                    elif hint == "escape":
                        state["method_escape"] += 1
                    if variant == "a":
                        state["method_A"] += 1
                    elif variant == "s":
                        state["method_S"] += 1
                else:
                    state["method_fail"] += 1
                    state["method_streak"] = 0

        if announce:
            print(
                "EVENT",
                timestamp,
                variant,
                hint,
                candidate,
                status,
                "lat",
                event.get("latency_seconds"),
                "detail",
                detail,
                flush=True,
            )

    def snapshot() -> None:
        foreground = win32gui.GetForegroundWindow() if win32gui else None
        if foreground == args.fc_hwnd:
            state["fc_foreground_samples"] += 1
            state["streak"] = 0
        print(
            "STATE",
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            dict(state, foreground_hwnd=foreground),
            flush=True,
        )

    while not args.path.exists():
        time.sleep(0.5)
    with args.path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                process(json.loads(line), announce=False)
            except (TypeError, ValueError):
                pass
        snapshot()
        last_snapshot = time.monotonic()
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line:
                stream.seek(position)
                if time.monotonic() - last_snapshot >= args.state_seconds:
                    snapshot()
                    last_snapshot = time.monotonic()
                time.sleep(0.25)
                continue
            if not line.endswith("\n"):
                stream.seek(position)
                time.sleep(0.1)
                continue
            try:
                process(json.loads(line), announce=True)
            except (TypeError, ValueError) as exc:
                print("PARSE_ERROR", repr(exc), flush=True)


if __name__ == "__main__":
    main()

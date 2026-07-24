"""Measure the complete speed-10 decision-to-input path without live orders."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_renewal_safety import _FakeEngine, _guard, _price, _profile, _run


def _percentile(values: list[float], percentile: float) -> float:
    return float(
        np.percentile(np.asarray(values, dtype=np.float64), percentile)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    baseline = _price("82400", 40, 20)
    changed = _price("82401", 40, 20)
    guard, _box = _guard(baseline)
    changed_guard, _box = _guard(changed)
    profile = _profile(baseline, guard)
    second_frame_ms: list[float] = []
    first_frame_ms: list[float] = []
    click_ms: list[float] = []
    failures: list[dict[str, object]] = []
    started = time.perf_counter()

    for index in range(max(1, int(args.runs))):
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [guard, guard],
                [guard, guard],
                [changed_guard, changed_guard],
            ],
        )
        ordered: list[dict[str, object]] = []

        def collect(
            _side,
            reason,
            _baseline,
            _candidates,
            metadata,
            **_kwargs,
        ) -> None:
            if reason == "ordered":
                ordered.append(metadata)

        completed = _run(
            profile,
            engine,
            diagnostic_sink=collect,
        )
        if not completed or len(ordered) != 1:
            if len(failures) < 20:
                failures.append(
                    {
                        "run": index,
                        "completed": completed,
                        "ordered_diagnostics": len(ordered),
                        "clicks": engine.clicks,
                    }
                )
            continue
        metrics = ordered[0]
        second_frame_ms.append(
            float(metrics["second_frame_to_input_ms"])
        )
        first_frame_ms.append(float(metrics["first_frame_to_input_ms"]))
        click_ms.append(float(metrics["click_ms"]))

    report = {
        "schema_version": 1,
        "requested_runs": max(1, int(args.runs)),
        "completed_runs": len(second_frame_ms),
        "failures": failures,
        "elapsed_seconds": time.perf_counter() - started,
        "second_frame_to_input_ms": {
            "p50": _percentile(second_frame_ms, 50),
            "p95": _percentile(second_frame_ms, 95),
            "p99": _percentile(second_frame_ms, 99),
            "maximum": max(second_frame_ms, default=0.0),
        },
        "first_frame_to_input_ms": {
            "p50": _percentile(first_frame_ms, 50),
            "p95": _percentile(first_frame_ms, 95),
            "p99": _percentile(first_frame_ms, 99),
            "maximum": max(first_frame_ms, default=0.0),
        },
        "click_ms": {
            "p99": _percentile(click_ms, 99),
            "maximum": max(click_ms, default=0.0),
        },
    }
    report["passed"] = bool(
        report["completed_runs"] == report["requested_runs"]
        and not failures
        and report["second_frame_to_input_ms"]["p99"] <= 2.0
        and report["second_frame_to_input_ms"]["maximum"] <= 4.0
        and report["first_frame_to_input_ms"]["p95"] <= 12.0
        and report["first_frame_to_input_ms"]["p99"] <= 20.0
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

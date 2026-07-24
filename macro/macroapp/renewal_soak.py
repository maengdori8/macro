"""Local-only 120 Hz, zero-input endurance verifier for the renewal engine."""

from __future__ import annotations

import json
import math
import os
import argparse
import time
from pathlib import Path
from typing import Iterable

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np
import psutil

from macroapp.renewal import (
    PriceState,
    RenewalModalGuard,
    RenewalPriceClassifier,
    _translate_image,
    crop_price_from_guard,
)


class _Histogram:
    def __init__(self, resolution: float, maximum: float):
        self.resolution = float(resolution)
        self.maximum = float(maximum)
        self.buckets = [0] * (int(math.ceil(maximum / resolution)) + 1)
        self.count = 0
        self.total = 0.0
        self.maximum_seen = 0.0

    def add(self, value: float) -> None:
        numeric = max(0.0, float(value))
        index = min(
            len(self.buckets) - 1,
            int(numeric / self.resolution),
        )
        self.buckets[index] += 1
        self.count += 1
        self.total += numeric
        self.maximum_seen = max(self.maximum_seen, numeric)

    def percentile(self, percentile: float) -> float:
        if self.count <= 0:
            return 0.0
        target = max(
            1,
            int(math.ceil(self.count * float(percentile) / 100.0)),
        )
        cumulative = 0
        for index, count in enumerate(self.buckets):
            cumulative += count
            if cumulative >= target:
                return min(self.maximum, index * self.resolution)
        return self.maximum

    def summary(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "average": self.total / self.count if self.count else 0.0,
            "p50": self.percentile(50),
            "p95": self.percentile(95),
            "p99": self.percentile(99),
            "p99_9": self.percentile(99.9),
            "maximum": self.maximum_seen,
        }


def _price(text: str) -> np.ndarray:
    image = np.full((40, 150), 251, dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (66, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        79,
        1,
        cv2.LINE_AA,
    )
    return image


def _same_price_corpus(baseline: np.ndarray) -> list[np.ndarray]:
    variants = [baseline]
    variants.extend(
        _translate_image(baseline, dx, dy)
        for dx, dy in (
            (-2, 0),
            (-1, 0),
            (1, 0),
            (2, 0),
            (0, -1),
            (0, 1),
        )
    )
    variants.extend(
        np.clip(
            baseline.astype(np.int16) + delta,
            0,
            255,
        ).astype(np.uint8)
        for delta in (-12, -8, -4, 4, 8, 12)
    )
    variants.append(cv2.GaussianBlur(baseline, (3, 3), 0))
    return variants


def _changed_corpus(changed: np.ndarray) -> list[np.ndarray]:
    return [
        _translate_image(changed, dx, dy)
        for dx, dy in (
            (0, 0),
            (-2, 0),
            (-1, 0),
            (1, 0),
            (2, 0),
            (0, -1),
            (0, 1),
        )
    ]


def _guard(price: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    guard = np.full((120, 342), 224, dtype=np.uint8)
    cv2.rectangle(guard, (1, 1), (340, 118), 90, 1)
    cv2.putText(
        guard,
        "LIMIT PRICE",
        (12, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        70,
        1,
        cv2.LINE_AA,
    )
    price_box = (96, 40, 246, 80)
    x1, y1, x2, y2 = price_box
    guard[y1:y2, x1:x2] = price
    return guard, price_box


def _guard_corpus(
    prices: list[np.ndarray],
    base_guard: np.ndarray,
    price_box: tuple[int, int, int, int],
) -> list[np.ndarray]:
    x1, y1, x2, y2 = price_box
    result: list[np.ndarray] = []
    for price in prices:
        candidate = base_guard.copy()
        candidate[y1:y2, x1:x2] = price
        result.append(candidate)
    return result


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _all_state(
    classifier: RenewalPriceClassifier,
    images: Iterable[np.ndarray],
    expected: PriceState,
) -> bool:
    return all(classifier.classify(image).state is expected for image in images)


def run_verification_soak(
    duration_seconds: float,
    report_path: Path,
    *,
    target_hz: float = 120.0,
) -> int:
    duration = max(1.0, float(duration_seconds))
    target_hz = min(240.0, max(30.0, float(target_hz)))
    period = 1.0 / target_hz
    baseline = _price("777")
    changed = _price("635")
    classifier = RenewalPriceClassifier(
        baseline,
        unchanged_limit=0.035,
        stability_limit=0.015,
    )
    same_corpus = _same_price_corpus(baseline)
    changed_corpus = _changed_corpus(changed)
    if not _all_state(classifier, same_corpus, PriceState.UNCHANGED):
        raise RuntimeError("soak unchanged corpus is not stable")
    if not _all_state(classifier, changed_corpus, PriceState.CHANGED):
        raise RuntimeError("soak changed corpus is not detectable")
    base_guard, price_box = _guard(baseline)
    modal_guard = RenewalModalGuard(
        base_guard,
        price_box,
        shift_limit=4,
    )
    same_guard_corpus = _guard_corpus(
        same_corpus,
        base_guard,
        price_box,
    )
    changed_guard_corpus = _guard_corpus(
        changed_corpus,
        base_guard,
        price_box,
    )
    popup_shifts = tuple(
        (dx, dy)
        for dy in range(-4, 5)
        for dx in range(-4, 5)
    )
    process = psutil.Process()
    logical_cpus = max(1, int(psutil.cpu_count(logical=True) or 1))
    started = time.perf_counter()
    started_unix = time.time()
    deadline = started + duration
    next_tick = started
    last_tick: float | None = None
    last_sample_at = started
    started_cpu = time.process_time()
    start_memory = process.memory_info()
    frame_intervals = _Histogram(0.05, 100.0)
    classify_latency = _Histogram(0.01, 10.0)
    guard_latency = _Histogram(0.01, 25.0)
    cpu_percent = _Histogram(0.01, 100.0)
    working_set_mb = _Histogram(0.05, 512.0)
    private_mb = _Histogram(0.05, 512.0)
    frames = 0
    unchanged_frames = 0
    changed_frames = 0
    false_changes = 0
    false_order_candidates = 0
    guard_rejections = 0
    changed_misses = 0
    changed_pair_mismatches = 0
    stable_changed_detections = 0
    previous_changed = None
    previous_unexpected_change = None
    busy_seconds_total = 0.0
    busy_seconds_since_sample = 0.0
    changed_pair_period = max(120, int(round(target_hz * 10.0)))
    next_report_at = started + 10.0

    def build_report(*, finished: bool) -> dict[str, object]:
        now = time.perf_counter()
        memory = process.memory_info()
        elapsed = max(0.0, now - started)
        report: dict[str, object] = {
            "schema_version": 1,
            "pid": os.getpid(),
            "finished": bool(finished),
            "started_at_unix": started_unix,
            "updated_at_unix": time.time(),
            "requested_seconds": duration,
            "elapsed_seconds": elapsed,
            "target_hz": target_hz,
            "frames": frames,
            "unchanged_frames": unchanged_frames,
            "changed_frames": changed_frames,
            "false_changes": false_changes,
            "false_order_candidates": false_order_candidates,
            "guard_rejections": guard_rejections,
            "changed_misses": changed_misses,
            "changed_pair_mismatches": changed_pair_mismatches,
            "stable_changed_detections": stable_changed_detections,
            "input_calls": 0,
            "frame_interval_ms": frame_intervals.summary(),
            "classify_ms": classify_latency.summary(),
            "guard_ms": guard_latency.summary(),
            "cpu_total_percent": cpu_percent.summary(),
            "working_set_mb": working_set_mb.summary(),
            "private_mb": private_mb.summary(),
            "memory_growth_mb": (
                memory.rss - start_memory.rss
            )
            / 1024**2,
            "private_growth_mb": (
                memory.private - start_memory.private
            )
            / 1024**2,
        }
        current_cpu = time.process_time()
        report["os_cpu_seconds"] = current_cpu - started_cpu
        report["cpu_total_percent"]["overall_average"] = (
            busy_seconds_total
            / max(1e-9, elapsed)
            * 100.0
            / logical_cpus
        )
        passed = bool(
            finished
            and false_changes == 0
            and false_order_candidates == 0
            and guard_rejections == 0
            and changed_misses == 0
            and changed_pair_mismatches == 0
            and stable_changed_detections > 0
            and report["frame_interval_ms"]["p95"] <= 10.0
            and report["classify_ms"]["p99"] <= 1.0
            and report["classify_ms"]["p99_9"] <= 2.0
            and report["cpu_total_percent"]["overall_average"] <= 3.0
            and report["cpu_total_percent"]["p95"] <= 8.0
            and report["working_set_mb"]["p95"] <= 120.0
            and report["memory_growth_mb"] <= 10.0
            and report["private_growth_mb"] <= 10.0
        )
        report["passed"] = passed
        return report

    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        if now < next_tick:
            time.sleep(next_tick - now)
        tick = time.perf_counter()
        if last_tick is not None:
            frame_intervals.add((tick - last_tick) * 1000.0)
        last_tick = tick
        frame_work_started = time.perf_counter()
        frame_index = frames
        pair_offset = frame_index % changed_pair_period
        is_changed = pair_offset in (0, 1)
        popup_shift_index = (
            frame_index // 2
        ) % len(popup_shifts)
        popup_dx, popup_dy = popup_shifts[popup_shift_index]
        if is_changed:
            corpus_index = (
                frame_index // changed_pair_period
            ) % len(changed_corpus)
            guard_frame = _translate_image(
                changed_guard_corpus[corpus_index],
                popup_dx,
                popup_dy,
            )
        else:
            # Keep every unchanged render for two distinct frames.  This
            # exercises the exact confirmation path that could otherwise
            # turn two identical false CHANGED results into a real order.
            corpus_index = (frame_index // 2) % len(same_corpus)
            guard_frame = _translate_image(
                same_guard_corpus[corpus_index],
                popup_dx,
                popup_dy,
            )
        guard_started = time.perf_counter()
        registration = modal_guard.register(guard_frame, 0.0, 0.0)
        guard_latency.add(
            (time.perf_counter() - guard_started) * 1000.0
        )
        if not registration.valid:
            guard_rejections += 1
            candidate = baseline
        else:
            candidate = crop_price_from_guard(
                registration.aligned,
                price_box,
            )
        classify_started = time.perf_counter()
        result = classifier.classify(candidate)
        classify_latency.add(
            (time.perf_counter() - classify_started) * 1000.0
        )
        if is_changed:
            changed_frames += 1
            previous_unexpected_change = None
            if result.state is not PriceState.CHANGED:
                changed_misses += 1
                previous_changed = None
            elif (
                previous_changed is not None
                and classifier.same_candidate(previous_changed, result)
            ):
                stable_changed_detections += 1
                previous_changed = None
            else:
                if pair_offset == 1:
                    changed_pair_mismatches += 1
                previous_changed = result
        else:
            unchanged_frames += 1
            previous_changed = None
            if result.state is PriceState.CHANGED:
                false_changes += 1
                if (
                    previous_unexpected_change is not None
                    and classifier.same_candidate(
                        previous_unexpected_change,
                        result,
                    )
                ):
                    false_order_candidates += 1
                    previous_unexpected_change = None
                else:
                    previous_unexpected_change = result
            else:
                previous_unexpected_change = None
        frame_busy_seconds = time.perf_counter() - frame_work_started
        busy_seconds_total += frame_busy_seconds
        busy_seconds_since_sample += frame_busy_seconds
        frames += 1
        next_tick += period
        if tick - last_sample_at >= 1.0:
            sample_now = time.perf_counter()
            wall = max(1e-9, sample_now - last_sample_at)
            cpu_percent.add(
                busy_seconds_since_sample
                / wall
                * 100.0
                / logical_cpus
            )
            memory = process.memory_info()
            working_set_mb.add(memory.rss / 1024**2)
            private_mb.add(memory.private / 1024**2)
            last_sample_at = sample_now
            busy_seconds_since_sample = 0.0
        if tick >= next_report_at:
            _write_report(report_path, build_report(finished=False))
            next_report_at = tick + 10.0

    report = build_report(finished=True)
    _write_report(report_path, report)
    return 0 if bool(report["passed"]) else 1


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local renewal safety endurance verifier.",
    )
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hz", type=float, default=120.0)
    arguments = parser.parse_args()
    return run_verification_soak(
        arguments.seconds,
        arguments.report,
        target_hz=arguments.hz,
    )


if __name__ == "__main__":
    raise SystemExit(_main())

"""Manual speed-10 soak test for unchanged two-frame cycles."""

from __future__ import annotations

import time
import sys
import argparse
from pathlib import Path

import cv2
import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import PriceState, RenewalPriceClassifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=100_000)
    args = parser.parse_args()
    cycles = max(1, int(args.cycles))
    image = np.full((20, 40), 238, dtype=np.uint8)
    cv2.putText(
        image,
        "82400",
        (2, 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        30,
        1,
        cv2.LINE_AA,
    )
    classifier = RenewalPriceClassifier(
        image,
        unchanged_limit=0.035,
        stability_limit=0.015,
    )
    orders = 0
    ambiguous = 0
    process = psutil.Process()
    start_rss = process.memory_info().rss
    max_rss = start_rss
    started = time.perf_counter()
    for cycle in range(cycles):
        first = classifier.classify(image)
        second = classifier.classify(image)
        if (
            first.state is PriceState.CHANGED
            and classifier.same_candidate(first, second)
        ):
            orders += 1
        if (
            first.state is PriceState.AMBIGUOUS
            or second.state is PriceState.AMBIGUOUS
        ):
            ambiguous += 1
        if cycle % 10_000 == 0:
            max_rss = max(max_rss, process.memory_info().rss)
    elapsed = time.perf_counter() - started
    end_rss = process.memory_info().rss
    print(
        f"cycles={cycles} "
        f"frames={cycles * 2} "
        f"orders={orders} "
        f"ambiguous={ambiguous} "
        f"seconds={elapsed:.3f} "
        f"rss_start_mb={start_rss / 1024**2:.1f} "
        f"rss_end_mb={end_rss / 1024**2:.1f} "
        f"rss_peak_mb={max_rss / 1024**2:.1f}"
    )
    if orders or ambiguous:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

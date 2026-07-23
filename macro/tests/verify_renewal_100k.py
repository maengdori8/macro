"""Manual speed-10 soak test: 100,000 unchanged two-frame cycles."""

from __future__ import annotations

import time
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import PriceState, RenewalPriceClassifier


def main() -> None:
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
    started = time.perf_counter()
    for _cycle in range(100_000):
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
    elapsed = time.perf_counter() - started
    print(
        "cycles=100000 "
        "frames=200000 "
        f"orders={orders} "
        f"ambiguous={ambiguous} "
        f"seconds={elapsed:.3f}"
    )
    if orders or ambiguous:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

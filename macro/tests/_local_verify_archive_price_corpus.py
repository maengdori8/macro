from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import (  # noqa: E402
    PriceState,
    RenewalPriceClassifier,
    validate_price_region,
)


# These are manually read from the right-side FC ONLINE price rows. The first
# value is the sell lower limit, which is the row exercised by this corpus.
PRICE_LABELS = {
    "2025-08-16-14-32-01": ("27.5", "33.7"),
    "2025-08-16-16-28-00": ("26.4", "33.7"),
    "2025-08-16-20-24-02": ("30.1", "36.7"),
    "2025-08-16-22-31-32": ("28.1", "36.3"),
    "2025-08-17-02-24-05": ("27.4", "33.4"),
    "2025-08-17-04-28-04": ("24.1", "31.2"),
    "2025-08-17-20-29-01": ("28.1", "34.3"),
    "2025-08-18-00-32-02": ("28.4", "36.7"),
    "2025-08-18-02-22-00": ("25.7", "35.3"),
    "2025-08-18-04-32-01": ("22.8", "35.3"),
    "2025-08-18-20-40-03": ("26.5", "34.3"),
    "2025-08-18-20-45-29": ("26.5", "34.3"),
    "2025-08-18-20-45-39": ("26.5", "34.3"),
    "2025-08-18-20-56-05": ("30.7", "37.5"),
    "2025-08-19-00-27-01": ("26.4", "34.1"),
    "2025-08-19-08-38-02": ("22.6", "33.1"),
    "2025-08-19-16-22-02": ("23.1", "33.9"),
    "2025-08-19-18-33-59": ("25.4", "34.9"),
    "2025-08-19-20-21-01": ("29.1", "35.5"),
    "2025-08-19-20-26-58": ("24.1", "35.3"),
    "2025-08-19-20-57-04": ("32", "39.2"),
}

LOWER_LIMIT_CROP = (1740, 580, 2130, 635)


def _decode_disguised_bmp(path: Path) -> np.ndarray:
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    if image is None:
        raise RuntimeError(f"cannot decode {path}")
    return image


def verify(archive_dir: Path) -> dict:
    started = time.perf_counter()
    rows: dict[str, np.ndarray] = {}
    missing: list[str] = []
    invalid_regions: list[dict] = []
    x1, y1, x2, y2 = LOWER_LIMIT_CROP

    for stem in PRICE_LABELS:
        path = archive_dir / f"{stem}.png"
        if not path.is_file():
            missing.append(stem)
            continue
        screen = _decode_disguised_bmp(path)
        row = screen[y1:y2, x1:x2]
        validation = validate_price_region(row)
        if not validation.valid:
            invalid_regions.append(
                {"stem": stem, "message": validation.message}
            )
        rows[stem] = row

    outcomes: Counter[str] = Counter()
    failures: list[dict] = []
    minimum_different_score = 1.0
    maximum_same_score = 0.0

    for baseline_name, candidate_name in itertools.permutations(rows, 2):
        baseline_label = PRICE_LABELS[baseline_name][0]
        candidate_label = PRICE_LABELS[candidate_name][0]
        same_price = baseline_label == candidate_label
        same_format = len(baseline_label) == len(candidate_label)
        classifier = RenewalPriceClassifier(
            rows[baseline_name],
            unchanged_limit=0.040,
            stability_limit=0.030,
        )
        first = classifier.classify(rows[candidate_name])
        second = classifier.classify(rows[candidate_name].copy())
        stable = classifier.same_candidate(first, second)
        authorized = first.state is PriceState.CHANGED and stable
        key = (
            f"{'same' if same_price else 'different'}:"
            f"{'same_format' if same_format else 'format_change'}:"
            f"{first.state.value}:{first.reason}"
        )
        outcomes[key] += 1

        if same_price:
            maximum_same_score = max(maximum_same_score, first.global_score)
            passed = first.state is PriceState.UNCHANGED and not authorized
        elif same_format:
            minimum_different_score = min(
                minimum_different_score,
                first.global_score,
            )
            passed = first.state is PriceState.CHANGED and authorized
        else:
            # A different character count can be visually identical to a
            # partially rendered price. It must never be called unchanged;
            # fail-closed ambiguity is deliberately accepted.
            passed = first.state is not PriceState.UNCHANGED

        if not passed:
            failures.append(
                {
                    "baseline": baseline_name,
                    "baseline_price": baseline_label,
                    "candidate": candidate_name,
                    "candidate_price": candidate_label,
                    "state": first.state.value,
                    "reason": first.reason,
                    "global_score": first.global_score,
                    "slice_score": first.slice_score,
                    "shift": [first.shift_x, first.shift_y],
                    "geometry_valid": first.geometry_valid,
                    "stable_two_frames": stable,
                    "authorized": authorized,
                }
            )

    expected_files = len(PRICE_LABELS)
    pair_count = len(rows) * max(0, len(rows) - 1)
    result = {
        "passed": bool(
            len(rows) == expected_files
            and not missing
            and not invalid_regions
            and not failures
        ),
        "archive_dir": str(archive_dir),
        "expected_files": expected_files,
        "loaded_files": len(rows),
        "pair_count": pair_count,
        "same_price_max_global_score": maximum_same_score,
        "same_format_different_min_global_score": minimum_different_score,
        "outcomes": dict(sorted(outcomes.items())),
        "missing": missing,
        "invalid_regions": invalid_regions,
        "failures": failures[:50],
        "failure_count": len(failures),
        "elapsed_seconds": time.perf_counter() - started,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        required=True,
        help="Directory containing the historical FC screenshots.",
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    result = verify(args.archive.resolve())
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

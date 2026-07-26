"""Audit the speed-10 first-frame ESC gate against captured FC popups.

The first image of each independent popup is held out.  Frames 2-4 train the
same-price render variants, so a successful result does not merely memorize
the frame being tested.  Legacy/incomplete captures must fail closed.
"""

from __future__ import annotations

import argparse
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
    RENEWAL_BASELINE_VARIANT_GLOBAL_LIMIT,
    RENEWAL_MAX_BASELINE_VARIANTS,
    PriceState,
    RenewalCalibratedPriceClassifier,
    RenewalModalGuard,
    RenewalPriceClassifier,
    crop_price_from_guard,
    dynamic_limit_price_boxes,
)


GUARD_SHAPE = (144, 412)
PRICE_BOX = (96, 48, 316, 96)
FRAME_HEIGHT = 1048


def _load_frames(folder: Path, prefix: str) -> dict[tuple[int, int], np.ndarray]:
    frames: dict[tuple[int, int], np.ndarray] = {}
    for path in sorted(folder.glob(f"{prefix}_*_*.png")):
        parts = path.stem.split("_")
        if len(parts) != 3:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        frames[(int(parts[1]), int(parts[2]))] = image
    return frames


def _build_holdout_classifier(
    open_frames: dict[tuple[int, int], np.ndarray],
    side: str,
) -> tuple[
    RenewalModalGuard,
    RenewalCalibratedPriceClassifier,
]:
    training_keys = [
        (opening, frame)
        for opening in range(1, 6)
        for frame in (2, 3, 4)
    ]
    if any(key not in open_frames for key in training_keys):
        raise ValueError("missing_training_frames")
    reference_guard = np.median(
        np.stack([open_frames[(1, frame)] for frame in (2, 3, 4)]),
        axis=0,
    ).astype(np.uint8)
    guard = RenewalModalGuard(
        reference_guard,
        PRICE_BOX,
        shift_limit=4,
        dynamic_boxes=dynamic_limit_price_boxes(
            PRICE_BOX,
            side,
            FRAME_HEIGHT,
        ),
    )
    training_prices: list[np.ndarray] = []
    for key in training_keys:
        registration = guard.register(open_frames[key], 0.0, 0.0)
        if not registration.valid:
            raise ValueError("training_guard_rejected")
        training_prices.append(
            crop_price_from_guard(registration.aligned, PRICE_BOX)
        )

    best: tuple[
        tuple[int, float, int],
        np.ndarray,
        list,
    ] | None = None
    for index, candidate in enumerate(training_prices):
        classifier = RenewalPriceClassifier(
            candidate,
            unchanged_limit=0.040,
            stability_limit=0.030,
        )
        results = [classifier.classify(price) for price in training_prices]
        key = (
            sum(result.state is not PriceState.UNCHANGED for result in results),
            sum(float(result.global_score) for result in results),
            index,
        )
        if best is None or key < best[0]:
            best = (key, candidate, results)
    if best is None or best[0][0] != 0:
        raise ValueError("training_price_changed_or_ambiguous")

    baseline = best[1]
    variants = [baseline]
    for result in best[2]:
        if (
            result.reason == "aligned_same_price"
            and result.global_score
            <= RENEWAL_BASELINE_VARIANT_GLOBAL_LIMIT
            and not any(
                np.array_equal(result.aligned, known)
                for known in variants
            )
        ):
            variants.append(result.aligned.copy())
        if len(variants) >= RENEWAL_MAX_BASELINE_VARIANTS:
            break
    return (
        guard,
        RenewalCalibratedPriceClassifier(
            baseline,
            variants,
            unchanged_limit=0.040,
            stability_limit=0.030,
        ),
    )


def audit(corpus_root: Path, expected_frames: int) -> dict[str, object]:
    started = time.perf_counter()
    folders = sorted(path for path in corpus_root.iterdir() if path.is_dir())
    total_frames = 0
    accepted_sets = 0
    blocked_sets: list[dict[str, str]] = []
    first_frames = 0
    fast_exits = 0
    unsafe_states = 0
    misses: list[dict[str, object]] = []
    guard_shifts: Counter[str] = Counter()
    price_shifts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    maximum_fast_score = 0.0

    for folder in folders:
        open_frames = _load_frames(folder, "open")
        closed_frames = _load_frames(folder, "closed")
        total_frames += len(open_frames) + len(closed_frames)
        if not open_frames:
            blocked_sets.append(
                {"folder": folder.name, "reason": "no_open_frames"}
            )
            continue
        shape = next(iter(open_frames.values())).shape
        if shape != GUARD_SHAPE:
            blocked_sets.append(
                {
                    "folder": folder.name,
                    "reason": f"unsupported_guard_shape:{shape}",
                }
            )
            continue
        side = "sell" if "_sell_" in folder.name else "buy"
        try:
            guard, classifier = _build_holdout_classifier(
                open_frames,
                side,
            )
        except Exception as exc:
            blocked_sets.append(
                {"folder": folder.name, "reason": str(exc)}
            )
            continue

        accepted_sets += 1
        for opening in range(1, 6):
            first_frames += 1
            frame = open_frames.get((opening, 1))
            if frame is None:
                unsafe_states += 1
                misses.append(
                    {
                        "folder": folder.name,
                        "opening": opening,
                        "reason": "missing_first_frame",
                    }
                )
                continue
            registration = guard.register(frame, 0.0, 0.0)
            if not registration.valid:
                unsafe_states += 1
                misses.append(
                    {
                        "folder": folder.name,
                        "opening": opening,
                        "reason": "first_guard_rejected",
                    }
                )
                continue
            result = classifier.classify(
                crop_price_from_guard(registration.aligned, PRICE_BOX)
            )
            guard_shifts[
                f"{registration.shift_x},{registration.shift_y}"
            ] += 1
            price_shifts[f"{result.shift_x},{result.shift_y}"] += 1
            reasons[result.reason] += 1
            if result.state is not PriceState.UNCHANGED:
                unsafe_states += 1
            if classifier.fast_unchanged(result):
                fast_exits += 1
                maximum_fast_score = max(
                    maximum_fast_score,
                    float(result.global_score),
                )
            else:
                misses.append(
                    {
                        "folder": folder.name,
                        "opening": opening,
                        "state": result.state.value,
                        "reason": result.reason,
                        "global_score": result.global_score,
                        "slice_score": result.slice_score,
                        "guard_shift": [
                            registration.shift_x,
                            registration.shift_y,
                        ],
                        "price_shift": [
                            result.shift_x,
                            result.shift_y,
                        ],
                    }
                )

    passed = bool(
        total_frames == expected_frames
        and accepted_sets > 0
        and first_frames > 0
        and unsafe_states == 0
        and fast_exits == first_frames
        and not misses
    )
    return {
        "passed": passed,
        "corpus_root": str(corpus_root),
        "expected_frames": expected_frames,
        "total_frames": total_frames,
        "folders": len(folders),
        "accepted_sets": accepted_sets,
        "blocked_sets": blocked_sets,
        "held_out_first_frames": first_frames,
        "fast_exits": fast_exits,
        "unsafe_states": unsafe_states,
        "maximum_fast_global_score": maximum_fast_score,
        "guard_shifts": dict(sorted(guard_shifts.items())),
        "price_shifts": dict(sorted(price_shifts.items())),
        "reasons": dict(sorted(reasons.items())),
        "misses": misses,
        "elapsed_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", "."))
        / "mAuto"
        / "renewal_corpus",
    )
    parser.add_argument("--expected-frames", type=int, default=520)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    result = audit(args.corpus.resolve(), max(1, args.expected_frames))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.json.with_suffix(".tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(args.json)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

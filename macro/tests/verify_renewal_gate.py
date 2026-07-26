"""Local speed-10 accuracy and latency gate.

This is intentionally not part of the short unit-test suite.  The full default
run executes the fixed release gates against a captured FC ONLINE glyph pair:

* 10,000,000 unchanged two-frame cycles
* 1,000,000 adversarial/partial-render two-frame cases
* 100,000 real-font changed two-frame cases
"""

from __future__ import annotations

import argparse
import base64
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
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import (
    PriceState,
    RenewalCalibratedPriceClassifier,
    RenewalPriceClassifier,
    _translate_image,
)


# Same 1928x1048 WGC frame, right-side upper/lower rows:
#   baseline 777조 -> changed 635조
_REAL_BASELINE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAJYAAAAoCAAAAAA5qlw7AAADkklEQVRYCc3Bb0zUdRzA"
    "8ffn4DjkhkExLDtpGjYpb0W2ZWz9cWzyhJFzwpZraFuEaOW0NBXyTxOT+BMEPUAxo+XG"
    "GiurFWvOLWGNXNRwFSS4ARbDSujG38Ovd984fr97cNc96MHvwe/1EoUdicKORGFHorAj"
    "UdiRKOxIFHYkCjsShR2Jwo5EYUeisML86C3C3J7bo37CElYG/5gjzLFamJ8nSmICUURh"
    "haG6McK8FePVQ4R5avxV/YS5W5y0txGlJI8oorDCUN0YYd6K8eohwjw1/qp+wtwtTtr"
    "biFKSRxRRWOF6058s8gfwVkw0jrDIH8BT428YYJE/gLvFSe9PhPzTDd4VhOSsIYoorDS"
    "7c5qCYkxzuybZsAuT/2Ufjx0g7IuPoKCY2ERhIX3qAslV6Rj0ufO4T96DQZ8/R9IJD6"
    "bZN0dgzespxCQKC107NsfWzZiun7jJpucx3agZJq8EU7DjQw2u4o1CLKKwTuD0RZ3+r"
    "gtD8JNPg3ceT8egO1oDdxzMxKB73/fFLbsRTKxYHUcMorDOSM2YFG5xYPi7aljytjkx"
    "zFZelZwdSwjRvu7Px1lX/PEPJD77VLrwH6KwztetgdTdazF1NQXcZesxXXn7tvPFXEI"
    "mu7oHA3j23Odr7tHO5Q/nrHARRRSW0YcGefANNwZ9pA/PW0sx6MpekutSgZEzfYDDu98"
    "FM2e/U0DSPi+RRGGZ/sOarZsxXTuoKSjGNLxPk1vGAl/9L8SvXL9h6Sjc5ejpHJjSq46"
    "4iSQKyxzvRZqWYWroQurvxXT2K6Q2gwX62y/XZmWmCXuhJIuZ34d+e+RpB5FEYRXfS0"
    "FWvYNp6rUJMuowzRwdYvl7RCjUlGcTmyis0tmo2Z6PqadhjqIiTH31E+RvJ0Khpjyb2E"
    "RhkWDzReKb0jDots+CcZWZGHRHayBu/zoiFGrKs4lNFBaZqL3K/ZXxGKYbfyTjcAqG+V"
    "OXuPuAhxCNqUhzKBuDEEkUFhmsvUluqQPD2MlRnti5BMPUsWG8u1NYMP0Bpk7wpmLIfY"
    "gIorDI5XrFtnzBMHD0Fpuei8Pw1555nil1smC8lBjKcokgCou0t+F69XFMF5pxvrAR0/"
    "c1OLYUETJeSgxluUQQhUWqL5O29wFMjZdIfuVRTKe/wbXjSUL0HDEkxBNBFBa5MklSV"
    "hKmn324spIx/TpBfFYK/58o7EgUdiQKOxKFHYnCjkRhR6Kwo38BlFs/cGyWaK0AAAAAS"
    "UVORK5CYII="
)
_REAL_CHANGED_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAJYAAAAoCAAAAAA5qlw7AAAEV0lEQVRYCc3Ba0zVdRzA"
    "4c/3HFBEULl4R4Y2nJqmmDpIPWUOba3IWCwyad4YOiU1G2BkpWmXyQHxvjSdlzlfGOUL"
    "qealzEi0Vl7BqXg5eEJBmShC8DuHX5wdWDun88IX/xf/5xGFGYnCjERhRqIwI1GYkSjM"
    "SBRmJAozEoUZicKMRGEUrTUigpfWGhHBQ9NJeDKiMMj9M3/W6ehnknoL7e6Wn79H37H"
    "jogQ4VYlX5Ayg6ip+nh6EH1EYo7GoQgHWoe/3BJwbb7iBoNELIoDNP+EVVwAcPICfzOn"
    "4EYUh9OoLeI3PEVryqvFKzgLWncYrrgA4eAA/mdPxIwpD/LUWYpO7HL0KK57l5LYWRr1"
    "U990Dgrf2glUXGB5Pu4hXgTs1eFSWwItJeAyKxo8ojKC/OkLomlhqP6xneqZ7T6mOWhm"
    "j9x2CvHHoFdeY+zJ+th6DyYutBCQKIzy2nydxucDGX/rbZrTu/tmduDiE37+ErGRa82"
    "+waAq+apa6od97QwhIFEaoLbhOSobAA2s47Vy10k8oK4KcCTR+5CBnglss/OfhzjKCW8"
    "U2tzuBiMIITruDma8Lvrb/SNCWSOpX3+bj+rM6zhZJh8e7f22NSj7UbE1M708AojCCw"
    "+4kI+X4MavN1pUO+lzxI6YsgjtraywTf3NjiV/SBw9dUdSgSU0vK0aiMpIs/I8ojOCw"
    "O0m1fKORMZl98DhW8s9DbR2Z3QuqP68FREPiMivN1c6yiy7CpqR35YeSeohNjBseji9"
    "RGMFhd9L7XqhuglG5IbQ7vNcFQclvh8C1gnvETnUfqcGal4BzQxUQmzLZCupi6bk2CF8"
    "zEF+iMILD7oSJWS77JSTbRrvDe12ANeMVaDpd/nd2PCc2wYQc3FtO0D11WogcgYQoV8"
    "X+69q2oAu+RGEEh93J4C+sNGU38NxSC9DmVlX7qggu7kO7NhHUggbCdgRRVTZqWDcgT"
    "ZOfALquctAQ/IjCCI7C27w2S6CgnGEfhOJ1LQ9mptLp03OEbupBpzRNfgKBicIIzsJbZ"
    "KQI7DrMU3kReOk5jSQtp1NBOd2KoumUpslPIDBRGKG+4AqpbwkUn2RoXlB5Va1tksD8"
    "B4xe6a64VN03rRt6uYPQLWF0StPkJxCYKIyg1p9mxCcWWpbVMib3UeFlJi3sSs2SNp7"
    "PVnu+Z0DuQO4sU8QUui/TYRXMjMcrJgIfojBE6U4sqW+6NpRr3khX248TPHva4/VnIS"
    "uZo18rSXy3ddspmDGrfgkdmqGLFa95L+BDFIZ4mFsH4boRenzWjzObmiCsrQmiC0Ops"
    "d+EkOBGTeS6nvezCGDhVHyIwhiVG2vxiJibBK0l37rxiJo/HvQf2xrwiHhnMs2lBDB2"
    "MD5EYZCqXZeBuDkjBHCd2l8HDJk9zALomzuuaBgwb6QVNIEIPkRhFH3rblvfOAteLTf"
    "qJSZG8HLfrmnrHyc8KVGYkSjMSBRmJAozEoUZicKMRGFG/wL8E6FwijU1HQAAAABJRU5"
    "ErkJggg=="
)


def _decode_png(encoded: str) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise RuntimeError("embedded FC glyph fixture decode failed")
    return image


def _subpixel(image: np.ndarray, x: float, y: float = 0.0) -> np.ndarray:
    matrix = np.float32([[1.0, 0.0, x], [0.0, 1.0, y]])
    return cv2.warpAffine(
        image,
        matrix,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _adversarial_variants(baseline: np.ndarray) -> list[tuple[str, np.ndarray]]:
    variants: list[tuple[str, np.ndarray]] = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            variants.append(
                (f"integer_shift_{dx}_{dy}", _translate_image(baseline, dx, dy))
            )
    for delta in (-30, -20, -12, -8, -4, 4, 8, 12, 20, 30):
        variants.append(
            (
                f"brightness_{delta}",
                np.clip(
                    baseline.astype(np.int16) + delta,
                    0,
                    255,
                ).astype(np.uint8),
            )
        )
    for kernel in (3, 5, 7):
        variants.append(
            (
                f"blur_{kernel}",
                cv2.GaussianBlur(baseline, (kernel, kernel), 0),
            )
        )
    for fraction in (0.25, 0.50, 0.75):
        variants.append(
            (f"subpixel_x_{fraction}", _subpixel(baseline, fraction))
        )

    background = int(np.median(baseline[0]))
    for edge in ("left", "right", "top", "bottom"):
        for pixels in (2, 4, 6, 8, 10, 12, 15, 20):
            clipped = baseline.copy()
            if edge == "left":
                clipped[:, :pixels] = background
            elif edge == "right":
                clipped[:, -pixels:] = background
            elif edge == "top":
                clipped[:pixels, :] = background
            else:
                clipped[-pixels:, :] = background
            variants.append((f"partial_{edge}_{pixels}", clipped))

    for top, bottom in ((8, 12), (12, 16), (16, 20), (20, 24)):
        occluded = baseline.copy()
        occluded[top:bottom, :] = background
        variants.append((f"horizontal_gap_{top}_{bottom}", occluded))
    for left, right in ((60, 70), (75, 85), (90, 100), (105, 115)):
        occluded = baseline.copy()
        occluded[:, left:right] = background
        variants.append((f"vertical_gap_{left}_{right}", occluded))

    blank = np.full_like(baseline, background)
    variants.append(("blank", blank))
    for alpha in (0.20, 0.35, 0.50, 0.65, 0.80):
        faded = cv2.addWeighted(
            baseline,
            alpha,
            blank,
            1.0 - alpha,
            0.0,
        )
        variants.append((f"transition_alpha_{alpha:.2f}", faded))
    return variants


def _changed_variants(changed: np.ndarray) -> list[tuple[str, np.ndarray]]:
    variants: list[tuple[str, np.ndarray]] = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            variants.append(
                (f"integer_shift_{dx}_{dy}", _translate_image(changed, dx, dy))
            )
    for delta in (-12, -8, -4, 4, 8, 12):
        variants.append(
            (
                f"brightness_{delta}",
                np.clip(
                    changed.astype(np.int16) + delta,
                    0,
                    255,
                ).astype(np.uint8),
            )
        )
    for kernel in (3, 5):
        variants.append(
            (
                f"blur_{kernel}",
                cv2.GaussianBlur(changed, (kernel, kernel), 0),
            )
        )
    for fraction in (0.25, 0.50, 0.75):
        variants.append(
            (f"subpixel_x_{fraction}", _subpixel(changed, fraction))
        )
    return variants


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--same-cycles", type=int, default=10_000_000)
    parser.add_argument("--adversarial-cases", type=int, default=1_000_000)
    parser.add_argument("--changed-cases", type=int, default=100_000)
    parser.add_argument("--latency-samples", type=int, default=20_000)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if args.quick:
        args.same_cycles = 10_000
        args.adversarial_cases = 10_000
        args.changed_cases = 10_000
        args.latency_samples = 2_000

    baseline = _decode_png(_REAL_BASELINE_PNG)
    changed = _decode_png(_REAL_CHANGED_PNG)
    classifier = RenewalPriceClassifier(
        baseline,
        unchanged_limit=0.035,
        stability_limit=0.015,
    )
    adversarial = _adversarial_variants(baseline)
    changed_corpus = _changed_variants(changed)
    process = psutil.Process()
    start_memory = process.memory_info()
    start_rss = start_memory.rss
    start_private = start_memory.private
    peak_rss = start_rss
    peak_private = start_private
    memory_samples: list[dict[str, object]] = []
    report: dict[str, object] = {
        "schema_version": 1,
        "fixture": {
            "baseline": "FC WGC 1928x1048 right upper row 777조",
            "changed": "same frame right lower row 635조",
            "shape": list(baseline.shape),
        },
        "requested": {
            "same_cycles": max(1, int(args.same_cycles)),
            "adversarial_cases": max(1, int(args.adversarial_cases)),
            "changed_cases": max(1, int(args.changed_cases)),
        },
    }

    same_orders = 0
    same_ambiguous = 0
    same_fast_exit_misses = 0
    started = time.perf_counter()
    for cycle in range(max(1, int(args.same_cycles))):
        first = classifier.classify(baseline)
        second = classifier.classify(baseline)
        if not RenewalCalibratedPriceClassifier.fast_unchanged(first):
            same_fast_exit_misses += 1
        if (
            first.state is PriceState.CHANGED
            and classifier.same_candidate(first, second)
        ):
            same_orders += 1
        if (
            first.state is PriceState.AMBIGUOUS
            or second.state is PriceState.AMBIGUOUS
        ):
            same_ambiguous += 1
        if cycle % 100_000 == 0:
            current_memory = process.memory_info()
            current_rss = current_memory.rss
            peak_rss = max(peak_rss, current_rss)
            peak_private = max(peak_private, current_memory.private)
            memory_samples.append(
                {
                    "phase": "same",
                    "case": cycle,
                    "rss_mb": current_rss / 1024**2,
                    "private_mb": current_memory.private / 1024**2,
                }
            )
    same_seconds = time.perf_counter() - started

    adversarial_orders = 0
    adversarial_failures: list[dict[str, object]] = []
    started = time.perf_counter()
    for index in range(max(1, int(args.adversarial_cases))):
        name, candidate = adversarial[index % len(adversarial)]
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        would_order = (
            first.state is PriceState.CHANGED
            and classifier.same_candidate(first, second)
        )
        if would_order:
            adversarial_orders += 1
            if len(adversarial_failures) < 20:
                adversarial_failures.append(
                    {
                        "case": index,
                        "variant": name,
                        "state": first.state.value,
                        "reason": first.reason,
                        "global": first.global_score,
                        "slice": first.slice_score,
                    }
                )
        if index % 100_000 == 0:
            current_memory = process.memory_info()
            current_rss = current_memory.rss
            peak_rss = max(peak_rss, current_rss)
            peak_private = max(peak_private, current_memory.private)
            memory_samples.append(
                {
                    "phase": "adversarial",
                    "case": index,
                    "rss_mb": current_rss / 1024**2,
                    "private_mb": current_memory.private / 1024**2,
                }
            )
    adversarial_seconds = time.perf_counter() - started

    changed_misses = 0
    changed_fast_exits = 0
    changed_failures: list[dict[str, object]] = []
    started = time.perf_counter()
    for index in range(max(1, int(args.changed_cases))):
        name, candidate = changed_corpus[index % len(changed_corpus)]
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        if RenewalCalibratedPriceClassifier.fast_unchanged(first):
            changed_fast_exits += 1
        detected = (
            first.state is PriceState.CHANGED
            and second.state is PriceState.CHANGED
            and classifier.same_candidate(first, second)
        )
        if not detected:
            changed_misses += 1
            if len(changed_failures) < 20:
                changed_failures.append(
                    {
                        "case": index,
                        "variant": name,
                        "first_state": first.state.value,
                        "second_state": second.state.value,
                        "reason": first.reason,
                        "global": first.global_score,
                        "slice": first.slice_score,
                    }
                )
        if index % 10_000 == 0:
            current_memory = process.memory_info()
            current_rss = current_memory.rss
            peak_rss = max(peak_rss, current_rss)
            peak_private = max(peak_private, current_memory.private)
            memory_samples.append(
                {
                    "phase": "changed",
                    "case": index,
                    "rss_mb": current_rss / 1024**2,
                    "private_mb": current_memory.private / 1024**2,
                }
            )
    changed_seconds = time.perf_counter() - started

    latency_ms: list[float] = []
    changed_latency_ms: list[float] = []
    latency_classifier = RenewalPriceClassifier(
        baseline,
        unchanged_limit=0.035,
        stability_limit=0.015,
    )
    for index in range(max(1, int(args.latency_samples))):
        _name, candidate = adversarial[index % len(adversarial)]
        tick = time.perf_counter_ns()
        latency_classifier.classify(candidate)
        latency_ms.append((time.perf_counter_ns() - tick) / 1_000_000.0)
    changed_latency_classifier = RenewalPriceClassifier(
        baseline,
        unchanged_limit=0.035,
        stability_limit=0.015,
    )
    for index in range(max(1, int(args.latency_samples))):
        _name, candidate = changed_corpus[index % len(changed_corpus)]
        tick = time.perf_counter_ns()
        changed_latency_classifier.classify(candidate)
        changed_latency_ms.append(
            (time.perf_counter_ns() - tick) / 1_000_000.0
        )

    end_memory = process.memory_info()
    end_rss = end_memory.rss
    end_private = end_memory.private
    peak_rss = max(peak_rss, end_rss)
    peak_private = max(peak_private, end_private)
    latency = {
        "adversarial": {
            "p50_ms": _percentile(latency_ms, 50),
            "p95_ms": _percentile(latency_ms, 95),
            "p99_ms": _percentile(latency_ms, 99),
            "p99_9_ms": _percentile(latency_ms, 99.9),
            "maximum_ms": max(latency_ms, default=0.0),
        },
        "changed": {
            "p50_ms": _percentile(changed_latency_ms, 50),
            "p95_ms": _percentile(changed_latency_ms, 95),
            "p99_ms": _percentile(changed_latency_ms, 99),
            "p99_9_ms": _percentile(changed_latency_ms, 99.9),
            "maximum_ms": max(changed_latency_ms, default=0.0),
        },
    }
    passed = bool(
        same_orders == 0
        and same_ambiguous == 0
        and same_fast_exit_misses == 0
        and adversarial_orders == 0
        and changed_misses == 0
        and changed_fast_exits == 0
        and latency["adversarial"]["p99_ms"] <= 1.0
        and latency["adversarial"]["p99_9_ms"] <= 2.0
        and latency["changed"]["p99_ms"] <= 1.0
        and latency["changed"]["p99_9_ms"] <= 2.0
        and peak_rss <= 120 * 1024 * 1024
        and peak_private <= 64 * 1024 * 1024
        and end_private - start_private <= 5 * 1024 * 1024
    )
    report.update(
        {
            "same": {
                "orders": same_orders,
                "ambiguous": same_ambiguous,
                "fast_exit_misses": same_fast_exit_misses,
                "seconds": same_seconds,
            },
            "adversarial": {
                "variants": len(adversarial),
                "orders": adversarial_orders,
                "failures": adversarial_failures,
                "seconds": adversarial_seconds,
            },
            "changed": {
                "variants": len(changed_corpus),
                "misses": changed_misses,
                "fast_exits": changed_fast_exits,
                "failures": changed_failures,
                "seconds": changed_seconds,
            },
            "latency": latency,
            "memory": {
                "start_mb": start_rss / 1024**2,
                "end_mb": end_rss / 1024**2,
                "peak_mb": peak_rss / 1024**2,
                "growth_mb": (end_rss - start_rss) / 1024**2,
                "private_start_mb": start_private / 1024**2,
                "private_end_mb": end_private / 1024**2,
                "private_peak_mb": peak_private / 1024**2,
                "private_growth_mb": (
                    end_private - start_private
                )
                / 1024**2,
                "samples": memory_samples,
            },
            "passed": passed,
            "finished_at_unix": time.time(),
        }
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.json.with_suffix(".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.json)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

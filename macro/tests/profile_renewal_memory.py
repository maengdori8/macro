"""Local-only memory profiler for the renewal price classifier."""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.renewal import RenewalPriceClassifier
from tests.verify_renewal_gate import (
    _REAL_BASELINE_PNG,
    _adversarial_variants,
    _decode_png,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=500_000)
    parser.add_argument("--sample-every", type=int, default=10_000)
    args = parser.parse_args()

    baseline = _decode_png(_REAL_BASELINE_PNG)
    variants = _adversarial_variants(baseline)
    classifier = RenewalPriceClassifier(baseline, 0.035, 0.015)
    process = psutil.Process()
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(max(1, args.cycles)):
        _name, candidate = variants[index % len(variants)]
        classifier.classify(candidate)
        classifier.classify(candidate.copy())
        if index % max(1, args.sample_every) == 0:
            current, peak = tracemalloc.get_traced_memory()
            memory = process.memory_info()
            print(
                f"{index},{variants[index % len(variants)][0]},"
                f"rss={memory.rss / 1024**2:.3f},"
                f"private={memory.private / 1024**2:.3f},"
                f"py={current / 1024**2:.3f},"
                f"py_peak={peak / 1024**2:.3f},"
                f"seconds={time.perf_counter() - started:.3f}",
                flush=True,
            )
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    memory = process.memory_info()
    print(
        f"done,rss={memory.rss / 1024**2:.3f},"
        f"private={memory.private / 1024**2:.3f},"
        f"py={current / 1024**2:.3f},py_peak={peak / 1024**2:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Extract the stable ``S SKIP`` glyph from a captured lower-screen diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "target_skip_s.png",
    )
    args = parser.parse_args()

    image = cv2.imread(str(args.source), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"cannot read {args.source}")
    height, width = image.shape[:2]
    # FC Online 1928x1048 capture: the diagnostic is the lower 22% (1928x231).
    # Ratios keep the extraction reproducible if the diagnostic dimensions differ.
    x1, x2 = int(width * 0.918), int(width * 0.967)
    y1, y2 = int(height * 0.690), int(height * 0.870)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 20:
        raise SystemExit(f"invalid crop {crop.shape} from {image.shape}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), crop):
        raise SystemExit(f"cannot write {args.output}")
    print(f"{args.output}: {crop.shape[1]}x{crop.shape[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

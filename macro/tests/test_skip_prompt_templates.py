from __future__ import annotations

from pathlib import Path
import unittest

import cv2
import numpy as np

from macroapp import ocr


ROOT = Path(__file__).resolve().parents[1]


class SkipPromptTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        ocr.reset_skip_a_template()

    def test_legacy_1_0_25_bundle_has_no_s_skip_templates(self) -> None:
        for filename in ("target_skip_s.png", "target_skip_s_dark.png"):
            with self.subTest(filename=filename):
                self.assertFalse((ROOT / filename).exists())

    def test_rejects_flat_background_as_s_skip(self) -> None:
        canvas = np.full((220, 1920), 90, dtype=np.uint8)
        matched, score, center = ocr.match_skip_s(canvas, ROOT)
        self.assertFalse(matched)
        self.assertLess(score, 0.80)
        self.assertIsNone(center)

    def test_a_template_is_not_misclassified_without_s_templates(self) -> None:
        a_template = cv2.imread(
            str(ROOT / "target_skip_a.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        self.assertIsNotNone(a_template)

        def canvas_with(template):
            canvas = np.full((220, 1920), 90, dtype=np.uint8)
            height, width = template.shape[:2]
            canvas[150:150 + height, 1760:1760 + width] = template
            return canvas

        a_canvas = canvas_with(a_template)
        self.assertTrue(ocr.match_skip_a(a_canvas, ROOT)[0])
        self.assertFalse(ocr.match_skip_s(a_canvas, ROOT)[0])


if __name__ == "__main__":
    unittest.main()

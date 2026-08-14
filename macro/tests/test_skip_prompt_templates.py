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

    def test_matches_s_skip_glyph_inside_lower_screen_crop(self) -> None:
        for filename in ("target_skip_s.png", "target_skip_s_dark.png"):
            with self.subTest(filename=filename):
                template = cv2.imread(
                    str(ROOT / filename),
                    cv2.IMREAD_GRAYSCALE,
                )
                self.assertIsNotNone(template)
                canvas = np.full((220, 1920), 90, dtype=np.uint8)
                height, width = template.shape[:2]
                canvas[150:150 + height, 1760:1760 + width] = template

                matched, score, center = ocr.match_skip_s(canvas, ROOT)

                self.assertTrue(matched)
                self.assertGreaterEqual(score, 0.99)
                self.assertEqual(
                    center,
                    (1760 + width // 2, 150 + height // 2),
                )

    def test_rejects_flat_background_as_s_skip(self) -> None:
        canvas = np.full((220, 1920), 90, dtype=np.uint8)
        matched, score, center = ocr.match_skip_s(canvas, ROOT)
        self.assertFalse(matched)
        self.assertLess(score, 0.80)
        self.assertIsNotNone(center)

    def test_a_and_s_templates_do_not_cross_match(self) -> None:
        a_template = cv2.imread(
            str(ROOT / "target_skip_a.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        s_template = cv2.imread(
            str(ROOT / "target_skip_s.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        self.assertIsNotNone(a_template)
        self.assertIsNotNone(s_template)

        def canvas_with(template):
            canvas = np.full((220, 1920), 90, dtype=np.uint8)
            height, width = template.shape[:2]
            canvas[150:150 + height, 1760:1760 + width] = template
            return canvas

        a_canvas = canvas_with(a_template)
        s_canvas = canvas_with(s_template)
        self.assertTrue(ocr.match_skip_a(a_canvas, ROOT)[0])
        self.assertFalse(ocr.match_skip_s(a_canvas, ROOT)[0])
        self.assertTrue(ocr.match_skip_s(s_canvas, ROOT)[0])
        self.assertFalse(ocr.match_skip_a(s_canvas, ROOT)[0])

    def test_rejects_shared_skip_text_without_a_icon(self) -> None:
        template = cv2.imread(
            str(ROOT / "target_skip_a.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        self.assertIsNotNone(template)
        canvas = np.full((220, 1920), 90, dtype=np.uint8)
        height, width = template.shape[:2]
        icon_width = int(round(width * 0.32))
        x, y = 1500, 150
        # The old full-template-only check scored this synthetic generic SKIP
        # fragment above 0.85 even though no A glyph exists.
        canvas[
            y:y + height,
            x + icon_width:x + width,
        ] = template[:, icon_width:]

        matched, score, _center = ocr.match_skip_a(canvas, ROOT)

        self.assertFalse(matched)
        self.assertGreater(score, 0.82)


if __name__ == "__main__":
    unittest.main()

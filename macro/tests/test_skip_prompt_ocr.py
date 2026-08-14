from unittest.mock import patch

import numpy as np

from macroapp import ocr


def test_classify_skip_prompt_returns_explicit_escape_hint() -> None:
    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(ocr, "_recognize_text", return_value="ESC  SKIP"),
    ):
        assert ocr.classify_skip_prompt(image) == (True, "escape")


def test_classify_skip_prompt_does_not_guess_escape_without_ocr_evidence() -> None:
    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(ocr, "_recognize_text", return_value="START SKIP"),
    ):
        assert ocr.classify_skip_prompt(image) == (True, "start")


def test_classify_skip_prompt_recognizes_any_key_enter_form() -> None:
    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(
            ocr,
            "_recognize_text",
            return_value="SKIP 하려면 아무키나 누르세요. (Enter 키 제외)",
        ),
    ):
        assert ocr.classify_skip_prompt(image) == (True, "any_key")


def test_classify_skip_prompt_requires_skip_text() -> None:
    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(ocr, "_recognize_text", return_value="ESC"),
    ):
        assert ocr.classify_skip_prompt(image) == (False, None)

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


def test_classify_skip_prompt_recognizes_any_key_from_korean_phrase_alone() -> None:
    """Enter 가 안 읽힌 프레임도 한글 문구만으로 any_key 다(실측: 한쪽만 읽히는 프레임이 흔함)."""

    image = np.zeros((20, 100), dtype=np.uint8)
    for raw in (
        "SKIP 하려면 아무 키나 누르세요.",
        "SKIP\n하려면 아무키나 누르세요",
        "스킵 하려면 아무 키나",
        "口그레고르코벨 SKIP 아무키나",
        # winocr 실측 오독 — '키'를 '기'로 읽고 Enter 도 놓친 프레임.
        "skip하려면아무기나누르세32",
    ):
        with (
            patch.object(ocr, "winocr", object()),
            patch.object(ocr, "_recognize_text", return_value=raw),
        ):
            assert ocr.classify_skip_prompt(image) == (True, "any_key"), raw


def test_classify_skip_prompt_any_key_still_requires_skip_token() -> None:
    """'계속하려면 아무 키나' 같은 다른 화면 문구는 SKIP 이 없으면 아무것도 아니다."""

    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(ocr, "_recognize_text", return_value="계속하려면 아무 키나 누르세요"),
    ):
        assert ocr.classify_skip_prompt(image) == (False, None)


def test_classify_skip_prompt_requires_skip_text() -> None:
    image = np.zeros((20, 100), dtype=np.uint8)
    with (
        patch.object(ocr, "winocr", object()),
        patch.object(ocr, "_recognize_text", return_value="ESC"),
    ):
        assert ocr.classify_skip_prompt(image) == (False, None)

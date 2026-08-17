"""자판기 발급 도구의 제품 지정 검증.

--product 를 생략하면 기존과 같이 mAuto("macro") 키가 나와야 한다(하위 호환).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import license_generator as lg


def test_default_product_is_macro():
    assert lg.DEFAULT_PRODUCT == "macro"


@pytest.mark.parametrize("product", ["macro", "mpause", "m-pause_2", "a" * 32])
def test_valid_products_accepted(product):
    lg._validate_args(30, 1, "", "", product)


@pytest.mark.parametrize(
    "product",
    ["", "MPAUSE", "a|b", "a b", "a" * 33, "제품", "mpause "],
)
def test_invalid_products_rejected(product):
    # 서명 메시지의 구분자 주입을 막는 제약을 클라이언트에서 먼저 강제한다.
    with pytest.raises(ValueError, match="제품 식별자"):
        lg._validate_args(30, 1, "", "", product)


def fake_server(monkeypatch, body: bytes, captured: dict | None = None):
    """서버 응답을 흉내 낸다. captured 를 주면 보낸 요청 본문을 담아 준다."""

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body

    def fake_urlopen(request, timeout=None):
        import json

        if captured is not None:
            captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(lg.urllib.request, "urlopen", fake_urlopen)


ONE_KEY = '"keys": ["ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2"]'


def test_payload_carries_product(monkeypatch):
    """issue_batch 가 실제로 product 를 서버에 보내는지 확인한다."""
    captured = {}
    fake_server(
        monkeypatch,
        ('{"success": true, %s, "product": "mpause"}' % ONE_KEY).encode(),
        captured,
    )

    lg.issue_batch(
        api_base="https://example.invalid",
        admin_key="k",
        days=30,
        count=1,
        batch="",
        memo="",
        product="mpause",
    )
    assert captured["body"]["product"] == "mpause"


def test_old_server_silently_issuing_macro_is_caught(monkeypatch):
    """⚠️ 제품 바인딩이 배포되지 않은 서버는 product 를 무시하고 macro 키를 만든다.

    그걸 모른 채 mPause 자판기에 넣으면 구매자 전원이 인증 실패만 겪고, 관리
    화면에서는 평범한 mAuto 재고로 보여 어느 배치가 오염됐는지 추적할 수 없다.
    """
    # 구버전 응답에는 product 필드 자체가 없다.
    fake_server(monkeypatch, ('{"success": true, %s}' % ONE_KEY).encode())

    with pytest.raises(RuntimeError, match="서버를 먼저 배포"):
        lg.issue_batch(
            api_base="https://example.invalid",
            admin_key="k",
            days=30,
            count=1,
            batch="",
            memo="",
            product="mpause",
        )


def test_server_answering_a_different_product_is_caught(monkeypatch):
    fake_server(
        monkeypatch, ('{"success": true, %s, "product": "macro"}' % ONE_KEY).encode()
    )
    with pytest.raises(RuntimeError, match="macro"):
        lg.issue_batch(
            api_base="https://example.invalid", admin_key="k", days=30, count=1,
            batch="", memo="", product="macro_pro",
        )


def test_old_server_is_fine_for_the_default_product(monkeypatch):
    """기본값(macro)일 때는 구버전 응답도 정상이다 — 오탐이 없어야 한다."""
    fake_server(monkeypatch, ('{"success": true, %s}' % ONE_KEY).encode())
    result = lg.issue_batch(
        api_base="https://example.invalid", admin_key="k", days=30, count=1,
        batch="", memo="",
    )
    assert result["keys"] == ["ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2"]


def test_payload_defaults_to_macro(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"success": true, "keys": ["ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2"]}'

    def fake_urlopen(request, timeout=None):
        import json

        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(lg.urllib.request, "urlopen", fake_urlopen)

    lg.issue_batch(
        api_base="https://example.invalid", admin_key="k", days=30, count=1, batch="", memo=""
    )
    assert captured["body"]["product"] == "macro"

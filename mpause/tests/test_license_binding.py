"""라이센스 제품 바인딩 검증 — **진짜 서버 코드**로 서명해 왕복시킨다.

핵심 질문 두 개에 답한다.
  1) mAuto 서버가 내주는 v1 서명을 mPause 클라이언트가 거부하는가?
  2) 서버가 mpause 로 서명한 응답만 통과하는가?

가짜 서명 함수를 쓰면 이 질문에 답이 안 되므로, 실제
license-server/lib/licenseSign.js 를 node 로 불러 서명한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import ed25519_tiny
from mpauseapp import license_client
from mpauseapp.config import PRODUCT_ID

SERVER_DIR = (
    Path(__file__).resolve().parent.parent.parent / "macro" / "license-server"
)

HWID = "0123456789abcdef0123456789abcdef"
NONCE = "beefcafebeefcafe0011223344556677"
SEED = bytes(range(32))  # 테스트 전용 고정 시드
PUBKEY_HEX = ed25519_tiny.public_key(SEED).hex()

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not SERVER_DIR.exists(),
    reason="node 또는 license-server 소스가 없습니다.",
)


def server_sign(*, verdict: str, exp: int, product: str | None) -> dict:
    """실제 서버 모듈로 응답을 서명한다(sendSignedVerdict 와 같은 경로)."""
    script = """
const path = require("path");
const crypto = require("crypto");
// node -e 는 argv[1] 부터가 사용자 인자다(스크립트 경로가 없으므로 한 칸 앞당겨진다).
const { buildSignedMessage } = require(path.join(process.argv[1], "lib", "licenseSign.js"));
const input = JSON.parse(process.argv[2]);
const PKCS8 = Buffer.from("302e020100300506032b657004220420", "hex");
const der = Buffer.concat([PKCS8, Buffer.from(input.seed, "hex")]);
const key = crypto.createPrivateKey({ key: der, format: "der", type: "pkcs8" });
const msg = buildSignedMessage({
  product: input.product || undefined,
  verdict: input.verdict,
  hwid: input.hwid,
  nonce: input.nonce,
  exp: input.exp,
});
const sig = crypto.sign(null, Buffer.from(msg, "utf8"), key);
process.stdout.write(JSON.stringify({
  valid: input.verdict === "valid",
  verdict: input.verdict,
  hwid: input.hwid,
  nonce: input.nonce,
  exp: input.exp,
  product: input.product || "",
  message: "테스트 응답",
  sig: sig.toString("hex"),
  _msg: msg,
}));
"""
    payload = json.dumps({
        "seed": SEED.hex(),
        "verdict": verdict,
        "hwid": HWID,
        "nonce": NONCE,
        "exp": exp,
        "product": product,
    })
    result = subprocess.run(
        ["node", "-e", script, str(SERVER_DIR), payload],
        capture_output=True,
        text=True,
        # 한국어 message 가 섞이므로 UTF-8 을 명시한다. 기본값은 이 PC 에서 cp949 라
        # 응답을 읽는 순간 UnicodeDecodeError 가 난다.
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def verify(response: dict, *, product: str | None = None, now: float | None = None) -> dict:
    return license_client.verify_signed_response(
        response,
        NONCE,
        HWID,
        product=product,
        pubkey_hex=PUBKEY_HEX,
        now=now,
    )


FUTURE = lambda: int(time.time()) + 3600  # noqa: E731


# ─── 핵심: 제품 교차 사용 차단 ─────────────────────────────────────────────

@node_required
def test_correct_product_is_accepted():
    response = server_sign(verdict="valid", exp=FUTURE(), product="mpause")
    assert response["_msg"].startswith("license-v2|mpause|")
    result = verify(response, product="mpause")
    assert result["valid"] is True
    assert result["remaining_seconds"] > 0


@node_required
def test_mauto_v1_signature_is_rejected():
    """mAuto 서버 응답(v1)을 그대로 흘려도 mPause 는 열리지 않는다."""
    response = server_sign(verdict="valid", exp=FUTURE(), product=None)
    assert response["_msg"].startswith("license-v1|")
    result = verify(response, product="mpause")
    assert result["valid"] is False
    assert "서명" in result["message"]


@node_required
def test_other_product_v2_signature_is_rejected():
    """다른 제품(macro)으로 서명된 v2 응답도 거부한다."""
    response = server_sign(verdict="valid", exp=FUTURE(), product="macro")
    result = verify(response, product="mpause")
    assert result["valid"] is False


@node_required
def test_response_product_field_cannot_override_client_constant():
    """응답의 product 필드를 조작해도 판정이 바뀌지 않는다.

    클라이언트는 자기 상수로 검증 메시지를 만들기 때문에, 응답 필드는 참고용일 뿐이다.
    """
    response = server_sign(verdict="valid", exp=FUTURE(), product="macro")
    response["product"] = "mpause"  # 공격자가 바꿔치기
    assert verify(response, product="mpause")["valid"] is False


# ─── 기존 방어가 그대로 살아 있는지 ────────────────────────────────────────

@node_required
def test_replayed_nonce_is_rejected():
    response = server_sign(verdict="valid", exp=FUTURE(), product="mpause")
    result = license_client.verify_signed_response(
        response, "0" * 32, HWID, product="mpause", pubkey_hex=PUBKEY_HEX
    )
    assert result["valid"] is False
    assert "nonce" in result["message"]


@node_required
def test_other_machine_response_is_rejected():
    response = server_sign(verdict="valid", exp=FUTURE(), product="mpause")
    result = license_client.verify_signed_response(
        response, NONCE, "f" * 32, product="mpause", pubkey_hex=PUBKEY_HEX
    )
    assert result["valid"] is False
    assert "기기" in result["message"]


@node_required
def test_expired_exp_is_rejected():
    response = server_sign(verdict="valid", exp=int(time.time()) - 10, product="mpause")
    result = verify(response, product="mpause")
    assert result["valid"] is False
    assert "만료" in result["message"]


@node_required
def test_exp_tampering_breaks_signature():
    response = server_sign(verdict="valid", exp=FUTURE(), product="mpause")
    response["exp"] = int(response["exp"]) + 86400 * 365
    assert verify(response, product="mpause")["valid"] is False


@node_required
def test_signed_invalid_verdict_is_rejected():
    response = server_sign(verdict="invalid", exp=0, product="mpause")
    response["valid"] = True  # MITM 이 valid 를 뒤집어도 소용없다
    assert verify(response, product="mpause")["valid"] is False


# ─── 서명 없이 되는 순수 검사 ──────────────────────────────────────────────

def test_fake_server_without_signature_is_rejected():
    """{"valid": true} 만 뱉는 가짜 로컬 서버는 통과하지 못한다."""
    result = verify({"valid": True, "verdict": "valid", "exp": FUTURE()})
    assert result["valid"] is False


def test_missing_pubkey_fails_closed():
    result = license_client.verify_signed_response(
        {"verdict": "valid"}, NONCE, HWID, pubkey_hex=""
    )
    assert result["valid"] is False
    assert "빌드 설정" in result["message"]


@pytest.mark.parametrize("bad", ["a|b", "MPAUSE", "", "x" * 33, "má"])
def test_invalid_product_fails_closed(bad):
    result = license_client.verify_signed_response(
        {"verdict": "valid"}, NONCE, HWID, product=bad, pubkey_hex=PUBKEY_HEX
    )
    assert result["valid"] is False


def test_message_format_matches_server_contract():
    built = license_client._license_message("mpause", "valid", HWID, NONCE, 42)
    assert built == f"license-v2|mpause|valid|{HWID}|{NONCE}|42".encode()


def test_product_id_is_wellformed():
    # 서명 메시지의 구분자 주입을 막는 형식 제약을 제품 상수 자체가 지켜야 한다.
    assert license_client._PRODUCT_RE.match(PRODUCT_ID)

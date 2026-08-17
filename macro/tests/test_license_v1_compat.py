"""mAuto 하위 호환 회귀 방지 — **실제 서버 JS** 가 만든 v1 서명을 mAuto 가 받아들이는가.

왜 별도 파일인가: test_license_signature.py 는 서버 서명을 Python 으로 '재현'해서
검증한다. 그건 메시지 규칙이 맞는지는 보지만, license-server/lib/licenseSign.js 를
고쳤을 때 **진짜로 v1 이 유지되는지**는 못 본다.

제품 바인딩(license-v2)을 넣으면서 licenseSign.js 를 고쳤다. 여기서 v1 이 조금이라도
달라지면 **이미 팔린 mAuto 사용자 전원이 인증에 실패한다.** 그 회귀를 이 파일이 막는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ed25519_tiny
from macroapp import license_client as lc

SERVER_DIR = Path(__file__).resolve().parents[1] / "license-server"
SEED = bytes.fromhex("11" * 32)
PUB = ed25519_tiny.public_key(SEED).hex()
HWID = "a" * 32
NONCE = "0123456789abcdef0123456789abcdef"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not SERVER_DIR.exists(),
    reason="node 또는 license-server 소스가 없습니다.",
)

_NODE_SCRIPT = """
const path = require("path");
const crypto = require("crypto");
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
  message: "정상",
  sig: sig.toString("hex"),
  _msg: msg,
}));
"""


def server_response(*, verdict="valid", exp=None, product=None):
    """진짜 서버 모듈로 응답을 서명한다."""
    payload = json.dumps({
        "seed": SEED.hex(),
        "verdict": verdict,
        "hwid": HWID,
        "nonce": NONCE,
        "exp": int(time.time()) + 3600 if exp is None else exp,
        "product": product,
    })
    result = subprocess.run(
        ["node", "-e", _NODE_SCRIPT, str(SERVER_DIR), payload],
        capture_output=True,
        text=True,
        encoding="utf-8",  # 기본값(cp949)으로는 한국어 message 를 읽다가 터진다
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_v1_signature_still_accepted_by_unchanged_mauto_client():
    """핵심: product 없는 요청 → v1 서명 → 기존 mAuto 클라이언트가 통과시킨다."""
    response = server_response(product=None)
    assert response["_msg"].startswith("license-v1|"), "v1 서명이 유지되지 않았다"
    result = lc.verify_signed_response(response, NONCE, HWID, pubkey_hex=PUB)
    assert result["valid"] is True, result["message"]
    assert result["remaining_seconds"] > 0


def test_v1_message_bytes_match_client_expectation():
    """서버가 만든 문자열이 mAuto 클라이언트가 만드는 바이트와 정확히 같아야 한다."""
    response = server_response(product=None, exp=1234567890)
    expected = lc._license_message("valid", HWID, NONCE, 1234567890)
    assert response["_msg"].encode("utf-8") == expected


def test_extra_product_field_does_not_break_old_client():
    """응답에 product 필드가 새로 붙었는데 구버전 클라이언트가 무시하는가."""
    response = server_response(product=None)
    assert "product" in response
    assert lc.verify_signed_response(response, NONCE, HWID, pubkey_hex=PUB)["valid"] is True


def test_v2_signature_is_rejected_by_mauto_client():
    """반대 방향: mPause 용 v2 응답으로는 mAuto 가 열리지 않는다."""
    response = server_response(product="mpause")
    assert response["_msg"].startswith("license-v2|")
    assert lc.verify_signed_response(response, NONCE, HWID, pubkey_hex=PUB)["valid"] is False


def test_signed_invalid_verdict_still_rejected():
    response = server_response(verdict="invalid", exp=0, product=None)
    response["valid"] = True
    assert lc.verify_signed_response(response, NONCE, HWID, pubkey_hex=PUB)["valid"] is False

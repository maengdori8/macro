"""티어(일반 / 프로) 판정 — 진짜 서버 서명 코드와 왕복시켜 검증한다.

가짜로 만든 서명이 아니라 `license-server/lib/licenseSign.js` 를 node 로 불러
실제로 서명한 응답을 mAuto 클라이언트에 넣는다. 그래야 두 구현이 같은 메시지를
만들고 있는지가 진짜로 확인된다(한쪽만 고치면 정품 사용자가 전부 막힌다).

여기서 지켜야 하는 것:
  1. 프로 키 → pro=True, 일반 키 → pro=False (티어가 서명에 묶여 있다)
  2. 서버가 옛 버전(v1)으로 서명해도 정품 사용자는 그대로 동작한다(롤백 대비)
  3. 다른 제품(mPause) 서명은 거부된다
  4. 티어를 응답 필드로 올려도 안 먹는다(서명만 신뢰)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp import license_client as lc  # noqa: E402

SIGN_JS = ROOT / "license-server" / "lib" / "licenseSign.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not SIGN_JS.exists(),
    reason="node 또는 서버 서명 모듈이 없으면 왕복 검증을 할 수 없다",
)

SEED = "11" * 32
HWID = "a" * 32
NONCE = "b" * 32


def sign(product: str, verdict: str = "valid", exp: int = 4102444800) -> dict:
    """진짜 서버 코드로 서명한 응답을 만든다."""
    script = f"""
      process.env.LICENSE_SIGNING_SEED = {json.dumps(SEED)};
      const crypto = require("crypto");
      const {{ buildSignedMessage }} = require({json.dumps(str(SIGN_JS).replace(chr(92), "/"))});
      const message = buildSignedMessage({{
        product: {json.dumps(product)}, verdict: {json.dumps(verdict)},
        hwid: {json.dumps(HWID)}, nonce: {json.dumps(NONCE)}, exp: {exp},
      }});
      const der = Buffer.concat([
        Buffer.from("302e020100300506032b657004220420", "hex"),
        Buffer.from(process.env.LICENSE_SIGNING_SEED, "hex"),
      ]);
      const key = crypto.createPrivateKey({{ key: der, format: "der", type: "pkcs8" }});
      const sig = crypto.sign(null, Buffer.from(message, "utf8"), key);
      const pub = crypto.createPublicKey(key).export({{ format: "der", type: "spki" }});
      console.log(JSON.stringify({{
        verdict: {json.dumps(verdict)}, hwid: {json.dumps(HWID)}, nonce: {json.dumps(NONCE)},
        exp: {exp}, product: {json.dumps(product)}, message: "서버 문구",
        sig: sig.toString("hex"),
        pubkey: Buffer.from(pub.subarray(pub.length - 32)).toString("hex"),
      }}));
    """
    out = subprocess.run(
        [NODE, "-e", script],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        env={**os.environ, "LICENSE_SIGNING_SEED": SEED},
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def check(response: dict, **over):
    payload = {**response, **over}
    return lc.verify_signed_response(
        payload, NONCE, HWID, pubkey_hex=response["pubkey"]
    )


def test_pro_key_is_recognised_as_pro():
    result = check(sign("macro_pro"))
    assert result["valid"] is True
    assert result["pro"] is True
    assert result["product"] == "macro_pro"


def test_standard_key_is_not_pro():
    result = check(sign("macro"))
    assert result["valid"] is True
    assert result["pro"] is False
    assert result["product"] == "macro"


def test_old_server_signature_still_works():
    """서버를 롤백해 v1 로 서명해도 정품 사용자는 막히지 않는다(일반 티어)."""
    result = check(sign(""))
    assert result["valid"] is True
    assert result["pro"] is False


def test_other_product_signature_is_rejected():
    result = check(sign("mpause"))
    assert result["valid"] is False
    assert "서명" in result["message"]


def test_response_field_cannot_upgrade_the_tier():
    """응답의 product 필드를 프로로 바꿔도 서명이 일반이면 일반이다."""
    result = check(sign("macro"), product="macro_pro")
    assert result["valid"] is True
    assert result["pro"] is False, "응답 필드로 티어가 올라갔다"


def test_pro_signature_cannot_be_replayed_with_another_nonce():
    response = sign("macro_pro")
    result = lc.verify_signed_response(
        response, "c" * 32, HWID, pubkey_hex=response["pubkey"]
    )
    assert result["valid"] is False


def test_signed_invalid_keeps_the_server_message_not_the_signed_bytes():
    """거부 문구 자리에 서명 바이트가 새어 나오면 안 된다."""
    result = check(sign("macro", verdict="invalid"))
    assert result["valid"] is False
    assert result["message"] == "서버 문구"
    assert result["pro"] is False


def test_expired_pro_key_is_not_pro_access():
    result = check(sign("macro_pro", exp=1))
    assert result["valid"] is False
    assert result["pro"] is False


# ─── 서버 운영 설정(0:2 자동 종료 비율) 수신 ───────────────────────────────
# 이 필드는 서명 밖 운영값이다. 규칙: 프로 티어(서명으로 확인)에서만 읽고,
# 범위 밖 값은 버린다(None → 클라이언트 내장 기본값 유지).


def test_pro_response_carries_auto_exit_ratio():
    result = check(sign("macro_pro"), auto_exit_ratio=0.25)
    assert result["valid"] is True
    assert result["auto_exit_ratio"] == 0.25


def test_missing_ratio_is_none_not_zero():
    """필드가 없으면(구버전 서버·읽기 실패) None — 0 으로 읽히면 종료가 꺼진다."""
    result = check(sign("macro_pro"))
    assert result["auto_exit_ratio"] is None


def test_bad_ratio_values_are_dropped():
    for bad in (1.5, -0.1, "abc", float("nan"), float("inf"), None, [0.4]):
        result = check(sign("macro_pro"), auto_exit_ratio=bad)
        assert result["valid"] is True, "운영값 불량이 인증을 막았다"
        assert result["auto_exit_ratio"] is None, f"통과되면 안 되는 값: {bad!r}"


def test_standard_tier_never_reads_the_ratio():
    """일반 티어 응답에 비율을 끼워 넣어도 무시된다(프로 전용 운영값)."""
    result = check(sign("macro"), auto_exit_ratio=0.9)
    assert result["valid"] is True
    assert result["auto_exit_ratio"] is None


def test_pro_response_passes_settings_dict_through():
    result = check(sign("macro_pro"), auto_exit_settings={"hard_deficit": 4, "late_minute": 75})
    assert result["pro"] is True
    assert result["auto_exit_settings"] == {"hard_deficit": 4, "late_minute": 75}


def test_settings_must_be_a_dict_and_pro_only():
    assert check(sign("macro_pro"), auto_exit_settings="junk")["auto_exit_settings"] is None
    assert check(sign("macro_pro"), auto_exit_settings=[1, 2])["auto_exit_settings"] is None
    assert check(sign("macro_pro"))["auto_exit_settings"] is None
    # 일반 티어에는 실려 와도 읽지 않는다.
    assert check(sign("macro"), auto_exit_settings={"hard_deficit": 9})["auto_exit_settings"] is None



def test_boolean_ratio_is_rejected_like_the_settings_parser():
    """True→1.0 같은 조용한 변환 금지 — 두 파서(비율 단독/규칙 전부)가 같은 규칙을 쓴다."""
    assert lc._parse_auto_exit_ratio(True) is None
    assert lc._parse_auto_exit_ratio(False) is None
    assert lc._parse_auto_exit_ratio(0.5) == 0.5

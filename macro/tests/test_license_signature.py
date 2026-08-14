"""라이센스 응답 서명 검증의 순수 로직 단위 테스트(Mac 검증 가능).

서버가 만들 서명을 ed25519_tiny로 재현해 전체 왕복을 검증한다. 서버(Node)와
클라이언트(Python)의 Ed25519 호환은 별도로 Node 서명→Python 검증으로 확인했다.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ed25519_tiny
from macroapp import license_client as lc

SEED = bytes.fromhex("11" * 32)
PUB = ed25519_tiny.public_key(SEED).hex()
HWID = "a" * 32


def _server_response(verdict, hwid, nonce, exp, *, human="ok"):
    """서버가 만들 정상 응답을 그대로 재현한다(같은 메시지 규칙 + 같은 개인키)."""
    msg = lc._license_message(verdict, hwid, nonce, exp)
    return {
        "valid": verdict == "valid",
        "verdict": verdict,
        "hwid": hwid,
        "nonce": nonce,
        "exp": exp,
        "message": human,
        "sig": ed25519_tiny.sign(msg, SEED).hex(),
    }


def _verify(resp, sent_nonce, hwid=HWID, now=1_700_000_000.0):
    return lc.verify_signed_response(resp, sent_nonce, hwid, pubkey_hex=PUB, now=now)


class LicenseSignatureTests(unittest.TestCase):
    def test_valid_signed_response_accepted(self):
        nonce = secrets.token_hex(16)
        exp = 1_700_000_000 + 30 * 86400
        r = _verify(_server_response("valid", HWID, nonce, exp), nonce)
        self.assertTrue(r["valid"])
        self.assertEqual(r["days"], 30)
        self.assertGreater(r["remaining_seconds"], 0)

    def test_signed_invalid_verdict_rejected(self):
        nonce = secrets.token_hex(16)
        r = _verify(_server_response("invalid", HWID, nonce, 0, human="만료됨"), nonce)
        self.assertFalse(r["valid"])
        self.assertEqual(r["message"], "만료됨")

    def test_fake_server_without_key_cannot_forge(self):
        """가짜 서버: valid=true라고 우겨도 서명이 없으면 거부(핵심 시나리오)."""
        nonce = secrets.token_hex(16)
        fake = {"valid": True, "verdict": "valid", "hwid": HWID, "nonce": nonce,
                "exp": 1_700_000_000 + 999999, "message": "hacked", "sig": ""}
        self.assertFalse(_verify(fake, nonce)["valid"])

    def test_fake_server_with_wrong_key_rejected(self):
        """다른 키로 서명한 응답은 거부된다(공개키가 안 맞음)."""
        nonce = secrets.token_hex(16)
        other = bytes.fromhex("22" * 32)
        exp = 1_700_000_000 + 999999
        msg = lc._license_message("valid", HWID, nonce, exp)
        resp = {"valid": True, "verdict": "valid", "hwid": HWID, "nonce": nonce,
                "exp": exp, "message": "x", "sig": ed25519_tiny.sign(msg, other).hex()}
        self.assertFalse(_verify(resp, nonce)["valid"])

    def test_replay_with_stale_nonce_rejected(self):
        """한 번 캡처한 정상 응답을 다음 요청에 재전송 → nonce 불일치로 거부."""
        captured_nonce = secrets.token_hex(16)
        exp = 1_700_000_000 + 999999
        captured = _server_response("valid", HWID, captured_nonce, exp)
        new_nonce = secrets.token_hex(16)   # 이번 요청에 클라이언트가 새로 만든 nonce
        self.assertFalse(_verify(captured, new_nonce)["valid"])

    def test_cross_account_hwid_rejected(self):
        """남의 정상 응답(다른 hwid)을 재사용 → 내 hwid와 불일치로 거부."""
        nonce = secrets.token_hex(16)
        exp = 1_700_000_000 + 999999
        other_hwid = "b" * 32
        resp = _server_response("valid", other_hwid, nonce, exp)
        self.assertFalse(_verify(resp, nonce, hwid="a" * 32)["valid"])

    def test_tampered_exp_rejected(self):
        """서명 후 exp만 늘려도 서명이 안 맞아 거부(만료 연장 시도 차단)."""
        nonce = secrets.token_hex(16)
        resp = _server_response("valid", HWID, nonce, 1_700_000_000 + 100)
        resp["exp"] = 1_700_000_000 + 10 * 86400   # 서명은 그대로, exp만 조작
        self.assertFalse(_verify(resp, nonce)["valid"])

    def test_verdict_flip_rejected(self):
        """서명은 invalid인데 valid 필드/verdict만 true로 바꿔치기 → 서명 불일치로 거부."""
        nonce = secrets.token_hex(16)
        resp = _server_response("invalid", HWID, nonce, 0)
        resp["valid"] = True
        resp["verdict"] = "valid"
        self.assertFalse(_verify(resp, nonce)["valid"])

    def test_expired_valid_signature_rejected(self):
        """서명은 정상이지만 exp가 과거면 만료 처리."""
        nonce = secrets.token_hex(16)
        resp = _server_response("valid", HWID, nonce, 1_700_000_000 - 10)
        self.assertFalse(_verify(resp, nonce)["valid"])

    def test_missing_pubkey_fails_closed(self):
        """공개키가 안 박힌 빌드는 검증 불가 → 무조건 거부(오작동 시 fail-closed)."""
        nonce = secrets.token_hex(16)
        resp = _server_response("valid", HWID, nonce, 1_700_000_000 + 999999)
        r = lc.verify_signed_response(resp, nonce, HWID, pubkey_hex="", now=1_700_000_000.0)
        self.assertFalse(r["valid"])

    def test_malformed_response_rejected(self):
        for bad in ({}, {"sig": "zzzz"}, {"verdict": "valid"}, {"nonce": "x", "sig": "1"}):
            self.assertFalse(_verify(bad, "whatever")["valid"])

    def test_deadline_snapshot_matches_signed_exp(self):
        """세션 만료 강제(gui._license_deadline)가 쓰는 값이 서명된 exp와 일치하는지.

        gui._after_verify는 remaining_seconds로 deadline = now + remaining을 만들고
        루프가 now > deadline이면 정지한다. 여기서는 그 remaining이 서명된 exp에서
        정확히 나오는지, 즉 만료 후에는 반드시 정지 판정이 나는지 검증한다.
        """
        nonce = secrets.token_hex(16)
        start = 1_700_000_000.0
        exp = int(start) + 3600           # 1시간짜리
        r = _verify(_server_response("valid", HWID, nonce, exp), nonce, now=start)
        self.assertTrue(r["valid"])
        deadline = start + r["remaining_seconds"]   # gui가 만드는 것과 동일한 산식
        # 서명된 exp를 넘지 않는다(로컬 강제가 유효기간을 임의로 늘리지 않음).
        self.assertLessEqual(deadline, exp + 1)
        # 만료 전에는 계속, 만료 후에는 정지.
        self.assertFalse(start + 1800 > deadline, "만료 30분 전엔 계속 돌아야 함")
        self.assertTrue(start + 3601 > deadline, "만료 후엔 정지 판정이어야 함")

    def test_days_rounding_is_ceiling(self):
        """남은 시간이 하루 조금 넘으면 2일이 아니라 올림 규칙대로 계산."""
        nonce = secrets.token_hex(16)
        exp = 1_700_000_000 + 86400 + 100   # 1일 + 100초
        r = _verify(_server_response("valid", HWID, nonce, exp), nonce)
        self.assertEqual(r["days"], 2)


if __name__ == "__main__":
    unittest.main()

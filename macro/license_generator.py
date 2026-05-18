"""
라이센스 키 생성기.

사용법:
    python license_generator.py --days 30
    python license_generator.py --days 7 --count 5
    python license_generator.py --days 99999

기간 옵션: 1, 7, 30, 99999
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import struct
import time

SECRET_KEY = b"macro-automation-license-key-2026-xK9mP2vL"

VALID_DAYS = {1, 7, 30, 99999}


def generate_license_key(days: int) -> str:
    if days not in VALID_DAYS:
        raise ValueError(f"허용되지 않는 기간입니다: {days}일. 허용: {sorted(VALID_DAYS)}")

    created = int(time.time())
    payload = struct.pack(">II", created, days)
    signature = hmac.new(SECRET_KEY, payload, hashlib.sha256).digest()[:8]
    raw = payload + signature
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=")
    parts = [encoded[i : i + 5] for i in range(0, len(encoded), 5)]
    return "-".join(parts)


def decode_license_key(key: str) -> dict:
    clean = key.replace("-", "").replace(" ", "").upper()
    padding = (8 - len(clean) % 8) % 8
    clean += "=" * padding
    raw = base64.b32decode(clean)

    if len(raw) != 16:
        raise ValueError("잘못된 키 형식입니다.")

    payload = raw[:8]
    stored_sig = raw[8:]
    expected_sig = hmac.new(SECRET_KEY, payload, hashlib.sha256).digest()[:8]

    if not hmac.compare_digest(stored_sig, expected_sig):
        raise ValueError("키 서명이 올바르지 않습니다.")

    created, days = struct.unpack(">II", payload)
    return {"created": created, "days": days}


def main() -> None:
    parser = argparse.ArgumentParser(description="라이센스 키 생성기")
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        choices=sorted(VALID_DAYS),
        help="라이센스 유효 기간 (일)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="생성할 키 개수 (기본: 1)",
    )
    args = parser.parse_args()

    print(f"\n{'=' * 50}")
    print(f"  라이센스 키 생성 - {args.days}일권 x {args.count}개")
    print(f"{'=' * 50}\n")

    for i in range(args.count):
        key = generate_license_key(args.days)
        info = decode_license_key(key)
        created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["created"]))
        print(f"  [{i + 1}] {key}")
        print(f"      생성: {created_str} | 유효기간: {info['days']}일")

        if i < args.count - 1:
            time.sleep(1)

    print(f"\n{'=' * 50}\n")


if __name__ == "__main__":
    main()

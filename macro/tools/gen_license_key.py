"""라이센스 응답 서명용 Ed25519 키쌍을 생성한다(개발자 전용 — 배포 금지).

사용:
    python tools/gen_license_key.py

출력:
  - 개인키(seed, hex)  → Vercel 환경변수 LICENSE_SIGNING_SEED 에 등록
  - 공개키(hex)        → macroapp/license_client.py 의 LICENSE_PUBKEY_HEX 에 박기

주의:
  - 개인키는 절대 git·클라이언트·설치본에 넣지 않는다(서버 환경변수에만).
  - 이 키는 업데이트 서명 키(release_signing_key.txt)와 별개다. 섞지 말 것.
  - 키를 바꾸면 그 순간부터 옛 서버가 만든 서명이 새 클라이언트에서 거부되므로,
    반드시 '서버 배포 → 클라이언트 빌드' 순서를 지킨다.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ed25519_tiny


def main() -> int:
    seed = secrets.token_bytes(32)
    pub = ed25519_tiny.public_key(seed)

    # 생성 즉시 자체 서명·검증으로 키쌍이 정상인지 확인한다(깨진 키 배포 방지).
    probe = b"license-v1|valid|deadbeef|cafe|1799999999"
    if not ed25519_tiny.verify(probe, ed25519_tiny.sign(probe, seed), pub):
        print("[오류] 생성된 키쌍이 자체 검증에 실패했습니다. 다시 실행하세요.")
        return 1

    print("=" * 64)
    print("라이센스 응답 서명 키쌍 (한 번만 생성, 안전하게 보관)")
    print("=" * 64)
    print()
    print("[1] 서버(Vercel) 환경변수 — 개인키 (절대 유출 금지)")
    print("    이름 : LICENSE_SIGNING_SEED")
    print(f"    값   : {seed.hex()}")
    print()
    print("[2] 클라이언트 — macroapp/license_client.py 에 박기")
    print(f'    LICENSE_PUBKEY_HEX = "{pub.hex()}"')
    print()
    print("=" * 64)
    print("배포 순서: (1) Vercel 환경변수 등록·재배포 → (2) 공개키 박고 클라이언트 빌드")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

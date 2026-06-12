"""릴리스 서명/등록 도구 (개발자 전용 — 배포 금지).

빌드 후 GitHub Release에 dist/macro_setup.exe를 업로드한 다음 실행하세요:

    python sign_release.py <다운로드_URL>            # 값 출력만 (admin 패널에 수동 입력)
    python sign_release.py <다운로드_URL> --post     # 서버에 자동 등록 (ADMIN_KEY 환경변수 필요)

하는 일:
  1. dist/macro_setup.exe의 SHA256 계산
  2. "macro-update-v1|버전|URL|해시" 매니페스트를 Ed25519 개인키로 서명
  3. (--post) /api/version에 버전·URL·해시·서명 등록

개인키는 release_signing_key.txt(.gitignore 등록됨)에 저장됩니다.
이 파일을 잃어버리면 기존 배포본이 업데이트를 받지 못하니 안전한 곳에 백업하세요.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import ed25519_tiny

ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / "release_signing_key.txt"
SETUP_FILE = ROOT / "dist" / "macro_setup.exe"
VERSION_FILE = ROOT / "version.txt"
LAUNCHER_FILE = ROOT / "launcher.py"
MANIFEST_PREFIX = "macro-update-v1"
VERSION_API = "https://license-server-flame-eta.vercel.app/api/version"


def load_or_create_seed() -> bytes:
    if KEY_FILE.exists():
        seed = bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
        if len(seed) != 32:
            raise SystemExit(f"[오류] {KEY_FILE.name} 손상: 32바이트 hex가 아닙니다.")
        return seed
    import secrets

    seed = secrets.token_bytes(32)
    KEY_FILE.write_text(seed.hex() + "\n", encoding="utf-8")
    print(f"[키 생성] 새 서명 키를 만들었습니다: {KEY_FILE}")
    print(f"          공개키: {ed25519_tiny.public_key(seed).hex()}")
    print("          launcher.py의 UPDATE_PUBKEY_HEX를 이 값으로 바꾸고 재빌드해야 합니다!")
    return seed


def check_pubkey_matches_launcher(seed: bytes) -> None:
    """launcher.py에 내장된 공개키와 키 파일이 일치하는지 확인합니다."""
    pub = ed25519_tiny.public_key(seed).hex()
    try:
        text = LAUNCHER_FILE.read_text(encoding="utf-8")
        match = re.search(r'UPDATE_PUBKEY_HEX\s*=\s*"([a-f0-9]{64})"', text)
    except Exception:
        return
    if match and match.group(1) != pub:
        raise SystemExit(
            "[오류] launcher.py의 UPDATE_PUBKEY_HEX가 현재 서명 키와 다릅니다.\n"
            f"       launcher.py : {match.group(1)}\n"
            f"       현재 키     : {pub}\n"
            "       이대로 등록하면 배포된 런처가 서명 검증에 실패해 업데이트가 무시됩니다."
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_post = "--post" in sys.argv[1:]

    if not args:
        raise SystemExit("사용법: python sign_release.py <다운로드_URL> [--post]")
    url = args[0].strip()
    if not url.lower().startswith("https://"):
        raise SystemExit("[오류] 다운로드 URL은 https여야 합니다.")

    if not SETUP_FILE.exists():
        raise SystemExit(f"[오류] {SETUP_FILE} 가 없습니다. build.bat을 먼저 실행하세요.")

    version = VERSION_FILE.read_text(encoding="utf-8-sig").strip()

    sha = hashlib.sha256()
    with open(SETUP_FILE, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    sha256_hex = sha.hexdigest()

    seed = load_or_create_seed()
    check_pubkey_matches_launcher(seed)
    message = f"{MANIFEST_PREFIX}|{version}|{url}|{sha256_hex}".encode("utf-8")
    sig_hex = ed25519_tiny.sign(message, seed).hex()

    print()
    print("===== 릴리스 서명 완료 =====")
    print(f"version : {version}")
    print(f"url     : {url}")
    print(f"sha256  : {sha256_hex}")
    print(f"sig     : {sig_hex}")
    print()

    if not do_post:
        print("admin 패널(/admin.html)의 버전 관리에 위 네 값을 그대로 입력하거나,")
        print("ADMIN_KEY 환경변수를 설정하고 --post 옵션으로 자동 등록하세요.")
        return

    admin_key = os.environ.get("ADMIN_KEY", "").strip()
    if not admin_key:
        raise SystemExit("[오류] --post에는 ADMIN_KEY 환경변수가 필요합니다.")

    body = json.dumps(
        {"version": version, "url": url, "sha256": sha256_hex, "sig": sig_hex}
    ).encode("utf-8")
    req = urllib.request.Request(
        VERSION_API,
        data=body,
        headers={"Content-Type": "application/json", "X-Admin-Key": admin_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("success"):
        raise SystemExit(f"[오류] 서버 등록 실패: {result.get('message', '알 수 없는 오류')}")
    print(f"[서버 등록 완료] {result.get('message', '')}")


if __name__ == "__main__":
    main()

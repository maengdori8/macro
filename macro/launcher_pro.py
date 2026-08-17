"""매크로 런처 — 업데이트 확인 후 macro.exe를 실행합니다.

업데이트 보안 모델 (판매본 보호):
  1. https + 신뢰 호스트(GitHub releases)만 허용
  2. SHA256 필수 — 해시가 없거나 불일치하면 설치하지 않음
  3. Ed25519 서명 필수 — "버전|URL|해시" 매니페스트를 개발자 개인키로 서명한 값을
     서버가 내려주고, 여기 내장된 공개키로 검증. 서버(Vercel/Firestore)가 통째로
     탈취돼도 개인키 없이는 위조 업데이트를 배포할 수 없습니다.

설치 흐름: 검증 통과 → 설치 파일을 분리 실행하고 런처는 즉시 종료 →
설치가 끝나면 setup.iss [Run]이 새 launcher.exe를 /postupdate로 재실행 →
업데이트 확인을 건너뛰고 macro.exe를 띄웁니다(재설치 루프 원천 차단).
"""

import ctypes
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error

import ed25519_tiny

VERSION_CHECK_URL = "https://license-server-flame-eta.vercel.app/api/version"

# 이 런처가 어느 제품을 돌보는지. 빌드할 때 프로용으로 바꿔 넣는다(build.bat).
# ⚠️ 일반은 값을 비워 둔다 — 이미 배포된 런처와 **같은 요청**이 되어야 서버가
# 지금 쓰던 기록을 그대로 돌려주고, 기존 사용자 업데이트가 안 끊긴다.
LAUNCHER_PRODUCT = "macro_pro"

# 실행할 프로그램. 프로 빌드는 같은 폴더의 다른 exe 를 띄운다.
TARGET_EXE = "macro_pro.exe"

# 업데이트 매니페스트 서명 검증용 공개키. 개인키는 release_signing_key.txt(미배포)로
# 보관하며 sign_release.py가 서명을 만듭니다. 키를 바꾸면 기존 배포본은 업데이트를
# 거부하므로 재배포 전에는 절대 바꾸지 마세요.
UPDATE_PUBKEY_HEX = "a32926b5e2cea4631bb294c7882d416505206c037cd017f86bef024b954cc1d5"
UPDATE_MANIFEST_PREFIX = "macro-update-v1"

# 업데이트 설치 파일을 받을 수 있는 신뢰 호스트(화이트리스트).
ALLOWED_UPDATE_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}

# 같은 버전 설치를 이 시간(초) 안에 재시도하지 않습니다.
# 서버 버전과 설치본 version.txt가 어긋나는 사고가 나도 재설치 폭주를 막는 안전핀.
UPDATE_RETRY_COOLDOWN_SECONDS = 600
UPDATE_MARKER = os.path.join(tempfile.gettempdir(), "macro_update_attempt.json")

CREATE_NO_WINDOW = 0x08000000


def show_error(text: str, title: str = "Macro 실행 오류") -> None:
    """콘솔이 없는 빌드에서도 사용자가 볼 수 있게 메시지 박스를 띄웁니다."""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def get_app_dir():
    """런처 exe가 실제로 위치한 폴더(= 설치 폴더)를 반환합니다.

    Nuitka onefile에서 sys.executable은 임시 압축해제 폴더를 가리키므로 쓰면 안
    됩니다(과거 '항상 0.0.0으로 읽혀 매번 재설치'를 일으킨 원인). 원본 경로를
    보존하는 sys.argv[0]을 쓰고, 그래도 못 찾으면 GetModuleFileNameW로 보강합니다.
    """
    if getattr(sys, "frozen", False) or ("__compiled__" in globals()):
        argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if argv0 and os.path.isfile(argv0):
            return os.path.dirname(argv0)
        try:
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 32768):
                return os.path.dirname(buf.value)
        except Exception:
            pass
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def read_version():
    try:
        vpath = os.path.join(get_app_dir(), "version.txt")
        with open(vpath, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


APP_VERSION = read_version()


def parse_version(v):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


def _host_allowed(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return host in ALLOWED_UPDATE_HOSTS or host.endswith(".githubusercontent.com")


def _verify_manifest_signature(version: str, url: str, sha256_hex: str, sig_hex: str) -> bool:
    """서버가 내려준 업데이트 매니페스트의 Ed25519 서명을 검증합니다."""
    try:
        message = f"{UPDATE_MANIFEST_PREFIX}|{version}|{url}|{sha256_hex}".encode("utf-8")
        return ed25519_tiny.verify(
            message, bytes.fromhex(sig_hex), bytes.fromhex(UPDATE_PUBKEY_HEX)
        )
    except Exception:
        return False


def check_update():
    """검증을 모두 통과한 업데이트 정보만 반환합니다. 아니면 None."""
    try:
        check_url = VERSION_CHECK_URL
        if LAUNCHER_PRODUCT:
            check_url = f"{VERSION_CHECK_URL}?product={LAUNCHER_PRODUCT}"
        req = urllib.request.Request(check_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        remote_ver = (data.get("version") or "").strip()
        url = (data.get("url") or "").strip()
        sha256_hex = (data.get("sha256") or "").strip().lower()
        sig_hex = (data.get("sig") or "").strip().lower()

        if not remote_ver or not url:
            return None
        if not re.fullmatch(r"\d{1,4}(\.\d{1,4}){0,3}", remote_ver):
            return None
        if parse_version(remote_ver) <= parse_version(APP_VERSION):
            return None
        # 보안 게이트: https + 신뢰 호스트 + SHA256 + 서명. 하나라도 빠지면 업데이트 포기.
        if not url.lower().startswith("https://") or not _host_allowed(url):
            return None
        if not re.fullmatch(r"[a-f0-9]{64}", sha256_hex):
            return None
        if not _verify_manifest_signature(remote_ver, url, sha256_hex, sig_hex):
            return None
        return {"version": remote_ver, "url": url, "sha256": sha256_hex}
    except Exception:
        pass
    return None


def _recently_attempted(version: str) -> bool:
    """같은 버전 설치를 최근에 이미 시도했으면 True (재설치 루프 방지)."""
    try:
        with open(UPDATE_MARKER, "r", encoding="utf-8") as f:
            marker = json.load(f)
        return (
            marker.get("version") == version
            and time.time() - float(marker.get("ts", 0)) < UPDATE_RETRY_COOLDOWN_SECONDS
        )
    except Exception:
        return False


def _record_attempt(version: str) -> None:
    try:
        with open(UPDATE_MARKER, "w", encoding="utf-8") as f:
            json.dump({"version": version, "ts": time.time()}, f)
    except Exception:
        pass


def download_and_install(url, expected_sha256):
    """설치 파일을 받아 해시 검증 후 분리 실행합니다. 성공 시 True(런처는 종료해야 함)."""
    try:
        setup_path = os.path.join(tempfile.gettempdir(), "macro_setup_update.exe")

        sha = hashlib.sha256()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(setup_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)

        if not hmac.compare_digest(sha.hexdigest(), expected_sha256):
            try:
                os.remove(setup_path)
            except Exception:
                pass
            return False

        # 분리 실행 후 즉시 종료: 설치기가 launcher.exe를 교체할 때 파일 잠금이 없고,
        # 설치 완료 시 [Run]이 새 런처를 /postupdate로 다시 띄워줍니다.
        subprocess.Popen(
            [setup_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def launch_macro(app_dir):
    macro_exe = os.path.join(app_dir, TARGET_EXE)
    if not os.path.exists(macro_exe):
        show_error(
            "macro.exe를 찾을 수 없습니다.\n\n"
            "백신(Windows Defender 등)이 파일을 격리했을 수 있습니다.\n"
            "백신 예외 등록 후 프로그램을 다시 설치해 주세요.\n\n"
            f"확인한 경로: {macro_exe}"
        )
        return
    try:
        subprocess.Popen([macro_exe], close_fds=True)
    except Exception as exc:
        show_error(f"macro.exe 실행에 실패했습니다.\n\n{exc}")


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    # 빌드 산출물이 로컬 모듈과 표준 라이브러리를 빠짐없이 포함했는지,
    # 실제 macro.exe를 실행하지 않고 검사할 수 있는 설치 전 게이트입니다.
    if "--startup-selftest" in sys.argv[1:]:
        if len(UPDATE_PUBKEY_HEX) != 64:
            return 1
        if parse_version("1.2.3") != (1, 2, 3):
            return 1
        return 0

    app_dir = get_app_dir()

    # 설치 직후 재실행(/postupdate)이면 업데이트 확인을 건너뜁니다.
    skip_update = any(arg.lower() in ("/postupdate", "--postupdate") for arg in sys.argv[1:])

    if not skip_update:
        update = check_update()
        if update and not _recently_attempted(update["version"]):
            _record_attempt(update["version"])
            if download_and_install(update["url"], update["sha256"]):
                return 0  # 설치기에게 넘기고 종료. 새 런처가 macro를 실행합니다.

    launch_macro(app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

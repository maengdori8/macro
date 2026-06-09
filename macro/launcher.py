"""매크로 런처 — 업데이트 확인 후 macro.exe를 실행합니다."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error

VERSION_CHECK_URL = "https://license-server-flame-eta.vercel.app/api/version"

# 업데이트 설치 파일을 받을 수 있는 신뢰 호스트(화이트리스트).
# 서버가 탈취당해 임의 URL을 내려도 여기 없는 호스트면 실행하지 않습니다.
ALLOWED_UPDATE_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}


def get_app_dir():
    # PyInstaller(sys.frozen)와 Nuitka(__compiled__) 모두 대응
    if getattr(sys, "frozen", False) or ("__compiled__" in globals()):
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


def check_update():
    try:
        req = urllib.request.Request(VERSION_CHECK_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        remote_ver = data.get("version", "")
        url = data.get("url", "")
        if not remote_ver or not url:
            return None
        if parse_version(remote_ver) > parse_version(APP_VERSION):
            return {"version": remote_ver, "url": url, "sha256": (data.get("sha256") or "").strip().lower()}
    except Exception:
        pass
    return None


def _host_allowed(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return host in ALLOWED_UPDATE_HOSTS or host.endswith(".githubusercontent.com")


def download_and_install(url, expected_sha256=""):
    try:
        # 1) https + 신뢰 호스트만 허용 (서버 탈취 시 임의 URL 실행 차단).
        if not url.lower().startswith("https://") or not _host_allowed(url):
            print("업데이트 거부: 허용되지 않은 다운로드 출처입니다.")
            return False

        temp_dir = tempfile.gettempdir()
        setup_path = os.path.join(temp_dir, "macro_setup.exe")

        print(f"업데이트 다운로드 중...")
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

        # 2) 서버가 해시를 제공하면 일치할 때만 실행 (변조/스왑된 설치 파일 차단).
        digest = sha.hexdigest()
        if expected_sha256:
            if not hmac.compare_digest(digest, expected_sha256):
                print("업데이트 거부: 설치 파일 해시 불일치 (변조 의심).")
                try:
                    os.remove(setup_path)
                except Exception:
                    pass
                return False
        else:
            print("경고: 서버가 SHA256을 제공하지 않아 무결성 검증 없이 진행합니다.")

        print("설치 중...")
        proc = subprocess.Popen(
            [setup_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            creationflags=0x08000000,
        )
        proc.wait(timeout=120)
        # Inno Setup 종료 코드: 0=성공, 그 외=실패. 실패면 기존 버전으로 실행.
        if proc.returncode not in (0, None):
            print(f"설치 실패 (코드 {proc.returncode}). 기존 버전으로 실행합니다.")
            return False
        time.sleep(1)
        return True
    except Exception as e:
        print(f"업데이트 실패: {e}")
        return False


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    app_dir = get_app_dir()
    macro_exe = os.path.join(app_dir, "macro.exe")

    update = check_update()
    if update:
        print(f"새 버전 발견: {update['version']}")
        download_and_install(update["url"], update.get("sha256", ""))

    if os.path.exists(macro_exe):
        subprocess.Popen([macro_exe])
    else:
        print("macro.exe를 찾을 수 없습니다.")
        time.sleep(3)


if __name__ == "__main__":
    main()

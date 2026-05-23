"""매크로 런처 — 업데이트 확인 후 macro.exe를 실행합니다."""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

VERSION_CHECK_URL = "https://license-server-flame-eta.vercel.app/api/version"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def read_version():
    try:
        vpath = os.path.join(get_app_dir(), "version.txt")
        with open(vpath, "r", encoding="utf-8") as f:
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
            return {"version": remote_ver, "url": url}
    except Exception:
        pass
    return None


def download_and_install(url):
    try:
        temp_dir = tempfile.gettempdir()
        setup_path = os.path.join(temp_dir, "macro_setup.exe")

        print(f"업데이트 다운로드 중...")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(setup_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)

        print("설치 중...")
        proc = subprocess.Popen(
            [setup_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            creationflags=0x08000000,
        )
        proc.wait(timeout=120)
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
        download_and_install(update["url"])

    if os.path.exists(macro_exe):
        subprocess.Popen([macro_exe])
    else:
        print("macro.exe를 찾을 수 없습니다.")
        time.sleep(3)


if __name__ == "__main__":
    main()

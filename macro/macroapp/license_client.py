from __future__ import annotations
import hashlib
import json as _json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Optional

from macroapp.paths import APP_VERSION

STATUS_API_URL = "https://license-server-flame-eta.vercel.app/api/status"
STATUS_REPORT_INTERVAL_SECONDS = 30
VERSION_CHECK_URL = "https://license-server-flame-eta.vercel.app/api/version"
VERIFY_SERVER_URL = "https://license-server-flame-eta.vercel.app/api/verify"
LICENSE_FILE = "license.key"

def _send_status(license_key: str, rank: Optional[int] = None, running: bool = True, message: str = "") -> None:
    """매크로 상태를 서버에 전송합니다."""
    try:
        data = _json.dumps({
            "key": license_key,
            "hwid": get_hwid(),
            "rank": rank,
            "running": running,
            "message": message,
        }).encode("utf-8")
        req = urllib.request.Request(
            STATUS_API_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def load_saved_license(base_dir: Path) -> Optional[str]:
    """저장된 라이센스 키 파일을 읽습니다."""
    license_path = base_dir / LICENSE_FILE
    if license_path.exists():
        try:
            return license_path.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def save_license_key(base_dir: Path, key: str) -> bool:
    """라이센스 키를 파일에 저장합니다. 성공 여부를 반환합니다."""
    try:
        license_path = base_dir / LICENSE_FILE
        license_path.write_text(key.strip(), encoding="utf-8")
        return True
    except Exception:
        return False


def format_remaining_time(seconds: int) -> str:
    """남은 시간을 사람이 읽기 쉬운 형태로 변환합니다."""
    if seconds <= 0:
        return "만료됨"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days > 365:
        return f"{days}일 남음"
    if days > 0:
        return f"{days}일 {hours}시간 남음"
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}시간 {minutes}분 남음"
    return f"{minutes}분 남음"

def get_hwid() -> str:
    """머신 고유 HWID를 생성합니다."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["wmic", "csproduct", "get", "UUID"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and line != "UUID":
                    return hashlib.sha256(line.encode()).hexdigest()[:32]
    except Exception:
        pass
    mac = uuid.getnode()
    return hashlib.sha256(str(mac).encode()).hexdigest()[:32]


def verify_license_server(key: str, hwid: str) -> dict:
    """서버에 라이센스 키와 HWID를 검증합니다. 서버 연결 실패 시 _offline=True 반환."""
    try:
        data = _json.dumps({"key": key, "hwid": hwid}).encode("utf-8")
        req = urllib.request.Request(
            VERIFY_SERVER_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"_offline": True}


def _parse_version(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


def check_for_update() -> Optional[dict]:
    try:
        req = urllib.request.Request(VERSION_CHECK_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        remote_ver = data.get("version", "")
        download_url = data.get("url", "")
        if not remote_ver or not download_url:
            return None
        if _parse_version(remote_ver) > _parse_version(APP_VERSION):
            return {"version": remote_ver, "url": download_url, "changelog": data.get("changelog", "")}
    except Exception:
        pass
    return None


def apply_update(download_url: str, progress_callback=None) -> bool:
    import zipfile
    import tempfile
    if sys.platform != "win32":
        return False
    try:
        current_exe = sys.executable
        if not current_exe.endswith(".exe"):
            return False
        app_dir = str(Path(current_exe).resolve().parent)
        temp_zip = os.path.join(app_dir, "_update.zip")
        req = urllib.request.Request(download_url, method="GET")
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(temp_zip, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(downloaded / total)

        temp_dir = os.path.join(app_dir, "_update_tmp")
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)

        with zipfile.ZipFile(temp_zip, "r") as zf:
            zf.extractall(temp_dir)

        bat_path = os.path.join(app_dir, "_update.bat")
        with open(bat_path, "w", encoding="utf-8") as bat:
            bat.write('@echo off\n')
            bat.write('timeout /t 2 /nobreak >nul\n')
            bat.write(f'xcopy /Y /E "{temp_dir}\\*" "{app_dir}\\" >nul\n')
            bat.write(f'rmdir /s /q "{temp_dir}"\n')
            bat.write(f'del "{temp_zip}"\n')
            bat.write(f'start "" "{current_exe}"\n')
            bat.write('del "%~f0"\n')
        subprocess.Popen(["cmd", "/c", bat_path], creationflags=0x08000000)
        return True
    except Exception:
        return False

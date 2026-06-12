from __future__ import annotations
import hashlib
import json as _json
import subprocess
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Optional

STATUS_API_URL = "https://license-server-flame-eta.vercel.app/api/status"
STATUS_REPORT_INTERVAL_SECONDS = 30
VERIFY_SERVER_URL = "https://license-server-flame-eta.vercel.app/api/verify"
LICENSE_FILE = "license.key"

# HWID는 머신마다 고정이므로 1회만 계산해 캐시합니다(매 30초 WMIC 서브프로세스 제거).
_CACHED_HWID: Optional[str] = None

_CREATE_NO_WINDOW = 0x08000000

# 일부 메인보드는 SMBIOS UUID를 플레이스홀더로 채워 출하합니다 → 기기 구분 불가.
_INVALID_SMBIOS_UUIDS = {
    "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
    "00000000-0000-0000-0000-000000000000",
    "03000200-0400-0500-0006-000700080009",
}


def _smbios_uuid() -> Optional[str]:
    """SMBIOS 제품 UUID를 읽습니다. wmic(구형) → PowerShell CIM(wmic이 제거된
    Win11 24H2+) 순서로 시도하며, 두 경로 모두 동일한 UUID 문자열을 반환하므로
    기존 고객의 HWID(=sha256(UUID))가 그대로 유지됩니다."""
    commands = [
        ["wmic", "csproduct", "get", "UUID"],
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
        ],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=8,
                creationflags=_CREATE_NO_WINDOW,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and line != "UUID" and line.upper() not in _INVALID_SMBIOS_UUIDS:
                    return line
        except Exception:
            continue
    return None


def _machine_guid() -> Optional[str]:
    """Windows 설치 시 생성되는 MachineGuid (레지스트리, 서브프로세스 불필요)."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            value = str(value).strip()
            return value or None
    except Exception:
        return None


def get_hwid() -> str:
    """머신 고유 HWID를 생성합니다(세션당 1회 계산 후 캐시).

    우선순위: SMBIOS UUID(기존 호환) → MachineGuid → MAC.
    뒤로 갈수록 안정성이 떨어지므로 앞 단계가 가능하면 항상 그 값을 씁니다.
    """
    global _CACHED_HWID
    if _CACHED_HWID is not None:
        return _CACHED_HWID

    if sys.platform == "win32":
        source = _smbios_uuid()
        if source is None:
            source = _machine_guid()
        if source is not None:
            _CACHED_HWID = hashlib.sha256(source.encode()).hexdigest()[:32]
            return _CACHED_HWID

    mac = uuid.getnode()
    _CACHED_HWID = hashlib.sha256(str(mac).encode()).hexdigest()[:32]
    return _CACHED_HWID


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

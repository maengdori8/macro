from __future__ import annotations
import hashlib
import json as _json
import secrets
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Optional

try:
    import ed25519_tiny
except Exception:  # noqa: BLE001 - 서명 검증 모듈이 없으면 아래에서 fail-closed 처리합니다.
    ed25519_tiny = None  # type: ignore

STATUS_API_URL = "https://license-server-flame-eta.vercel.app/api/status"
STATUS_REPORT_INTERVAL_SECONDS = 30
VERIFY_SERVER_URL = "https://license-server-flame-eta.vercel.app/api/verify"
LICENSE_FILE = "license.key"

# ─── 라이센스 응답 서명 검증 (크랙/가짜서버 방지) ───
# 왜: 서버 응답이 서명 안 돼 있으면, hosts 파일로 도메인을 127.0.0.1로 돌리고
# {"valid": true}만 뱉는 가짜 로컬 서버를 띄우면 바이너리 패치 없이 크랙된다.
# 대응: 서버가 (verdict|hwid|nonce|exp)를 Ed25519로 서명하고, 클라이언트가 아래 공개키로
# 검증한다. 개인키는 서버(Vercel 환경변수)에만 있어 가짜 서버는 서명을 위조할 수 없다.
# nonce는 매 요청 새로 생성해 서명에 묶으므로 한 번 캡처한 정상 응답의 재전송도 막힌다.
# 이 공개키는 업데이트 서명 키(launcher)와 별개다(용도 분리 → 한쪽이 뚫려도 파급 차단).
LICENSE_PUBKEY_HEX = "2e8a3100a9a778f9f9a2486689cc1588096a74564de9e15746e76ae7f4703ddd"
LICENSE_MESSAGE_PREFIX = "license-v1"
# 응답이 이 시간(초)보다 늦게 만료되면 정상으로 본다(서명된 exp 기준, 시계 왜곡 여유 없음).
_LICENSE_MIN_REMAINING_SECONDS = 0


def _license_message(verdict: str, hwid: str, nonce: str, exp: int) -> bytes:
    """서버가 서명하고 클라이언트가 검증하는 정규 메시지. 필드는 전부 [0-9a-z|] 뿐이라
    구분자 주입이 불가능하다(hwid/nonce=hex, verdict=고정어, exp=정수)."""
    return f"{LICENSE_MESSAGE_PREFIX}|{verdict}|{hwid}|{nonce}|{int(exp)}".encode("utf-8")


def verify_signed_response(
    response: dict,
    sent_nonce: str,
    hwid: str,
    *,
    pubkey_hex: Optional[str] = None,
    now: Optional[float] = None,
) -> dict:
    """서명·바인딩을 모두 검증해 신뢰할 수 있는 판정을 돌려준다(순수 함수, 단위검증 가능).

    반환: {"valid": bool, "message": str, "remaining_seconds": int, "days": int}
    실패는 전부 valid=False로 수렴한다(fail-closed). 하나라도 어긋나면 거부:
      1) 공개키/서명모듈 존재      2) 서명이 공개키로 검증됨
      3) 응답 nonce == 보낸 nonce (재전송 차단)
      4) 응답 hwid == 내 hwid      (계정 도용 차단)
      5) 서명된 verdict == valid 필드 (MITM 뒤집기 차단)
      6) valid면 exp가 미래
    """
    key_hex = (pubkey_hex if pubkey_hex is not None else LICENSE_PUBKEY_HEX).strip()
    reject = {"valid": False, "message": "라이센스 응답 검증 실패", "remaining_seconds": 0, "days": 0}

    if ed25519_tiny is None or not key_hex:
        # 공개키가 안 박혔거나 검증 모듈이 없으면, 서명을 확인할 방법이 없으므로 거부한다.
        return {**reject, "message": "서명 검증을 사용할 수 없습니다(빌드 설정 오류)."}

    try:
        verdict = str(response.get("verdict", "")).strip()
        resp_hwid = str(response.get("hwid", "")).strip()
        resp_nonce = str(response.get("nonce", "")).strip()
        exp = int(response.get("exp", 0))
        sig_hex = str(response.get("sig", "")).strip()
        message = str(response.get("message", ""))
        pub = bytes.fromhex(key_hex)
        sig = bytes.fromhex(sig_hex)
    except Exception:  # noqa: BLE001 - 형식이 깨진 응답은 곧 거부다.
        return reject

    if verdict not in ("valid", "invalid"):
        return reject
    if not secrets.compare_digest(resp_nonce, str(sent_nonce)):
        return {**reject, "message": "응답 nonce 불일치(재전송 의심)."}
    if not secrets.compare_digest(resp_hwid, str(hwid)):
        return {**reject, "message": "응답 기기 정보 불일치."}
    if not ed25519_tiny.verify(_license_message(verdict, resp_hwid, resp_nonce, exp), sig, pub):
        return {**reject, "message": "서명이 유효하지 않습니다."}

    # 서명이 확인된 verdict가 최종 판정이다. valid 필드는 참고용일 뿐 신뢰하지 않는다.
    if verdict != "valid":
        return {"valid": False, "message": message or "유효하지 않은 라이센스입니다.",
                "remaining_seconds": 0, "days": 0}

    current = time.time() if now is None else now
    remaining = int(exp - current)
    if remaining <= _LICENSE_MIN_REMAINING_SECONDS:
        return {"valid": False, "message": "만료된 라이센스입니다.", "remaining_seconds": 0, "days": 0}

    return {
        "valid": True,
        "message": message or "유효한 라이센스입니다.",
        "remaining_seconds": remaining,
        "days": max(1, (remaining + 86399) // 86400),
    }

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
    """서버에 라이센스 키·HWID·nonce를 보내고, 서명된 응답을 검증합니다.

    반환은 기존과 호환됩니다: 연결 실패는 {"_offline": True}, 그 외에는
    {"valid": bool, "message": str, "remaining_seconds": int, "days": int}.
    서명·nonce·hwid 바인딩 중 하나라도 어긋나면 valid=False로 수렴합니다(fail-closed).
    가짜 서버는 개인키가 없어 서명을 만들 수 없으므로 여기서 걸립니다.
    """
    nonce = secrets.token_hex(16)
    try:
        data = _json.dumps({"key": key, "hwid": hwid, "nonce": nonce}).encode("utf-8")
        req = urllib.request.Request(
            VERIFY_SERVER_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"_offline": True}

    return verify_signed_response(payload, nonce, hwid)

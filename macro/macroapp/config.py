from __future__ import annotations
import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from macroapp.logging_util import LogCallback
from macroapp.paths import app_dir

FC_ONLINE_PROCESS_NAMES = ["fczf"]


# ─── 내장 자산(판매본 보호) ───
# 빌드 시 gen_assets.py가 targets.json/target_*.png를 macroapp/_assets.py에 박아
# Nuitka가 컴파일합니다. 그러면 설치 폴더엔 느슨한 로직/이미지 파일이 없어
# 구매자가 내부 구성을 열람·복사할 수 없습니다.
# 개발/소유자는 exe 옆에 느슨한 파일을 두면 그게 우선합니다(오버라이드).
def _embedded():
    try:
        from macroapp import _assets  # 빌드 시 생성됨(개발 중엔 없을 수 있음)
        return _assets
    except Exception:
        return None


# UI에서 캡처한 사용자 템플릿이 저장되는 폴더입니다.
# 기본 이미지(빌드 내장/느슨한 파일)는 건드리지 않으므로 언제든 되돌릴 수 있습니다.
CUSTOM_TARGETS_DIR_NAME = "custom_targets"


def _persistent_data_dir() -> Path:
    """설치 폴더와 무관하게 유지되는 사용자 데이터 폴더(%LOCALAPPDATA%\\Macro).

    캡처를 설치 폴더(Program Files) 안에 두면 언인스톨/재설치 시 함께 지워진다.
    여기(사용자 AppData)에 두면 업데이트·재설치·언인스톨 무엇을 해도 보존된다.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else (Path.home() / ".macro")
    return root / "Macro"


def custom_targets_dir(base_dir: Path) -> Path:
    """사용자 캡처 템플릿 폴더 경로를 반환합니다.

    설치 폴더가 아니라 %LOCALAPPDATA%\\Macro 아래에 두어 업데이트/재설치/언인스톨에도
    캡처가 보존되게 합니다. base_dir 인자는 호환을 위해 받지만 위치 결정엔 쓰지 않습니다.
    """
    return _persistent_data_dir() / CUSTOM_TARGETS_DIR_NAME


def migrate_custom_targets(install_dir: Path) -> None:
    """예전 위치(설치 폴더/custom_targets)의 캡처를 새 위치(AppData)로 1회 이전합니다.

    이미 새 위치에 있으면 덮어쓰지 않습니다. 실패해도 매크로 본체엔 영향이 없도록
    전부 가드합니다. (옛 파일은 지우지 않아 안전; 언인스톨 시 설치 폴더와 함께 정리됨.)
    """
    try:
        old = install_dir / CUSTOM_TARGETS_DIR_NAME
        new = custom_targets_dir(install_dir)
        if not old.is_dir() or old.resolve() == new.resolve():
            return
        new.mkdir(parents=True, exist_ok=True)
        for f in old.glob("*.png"):
            dest = new / f.name
            if not dest.exists():
                try:
                    dest.write_bytes(f.read_bytes())
                except Exception:
                    pass
    except Exception:
        pass


def _read_asset_bytes(filename: str, base_dir: Path, include_custom: bool = True) -> Optional[bytes]:
    """이미지 바이트를 사용자 캡처 → 느슨한 파일 → 내장 자산 순으로 읽습니다."""
    if include_custom:
        custom = custom_targets_dir(base_dir) / filename
        if custom.exists():
            try:
                return custom.read_bytes()
            except Exception:
                pass
    loose = base_dir / filename
    if loose.exists():
        try:
            return loose.read_bytes()
        except Exception:
            pass
    a = _embedded()
    if a is not None:
        b64 = getattr(a, "ASSETS", {}).get(filename)
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception:
                pass
    return None


def read_target_image_bytes(base_dir: Path, filename: str) -> Optional[bytes]:
    """현재 적용 중인 타겟 이미지 바이트를 반환합니다(썸네일 등 UI 표시용)."""
    return _read_asset_bytes(filename, base_dir)


def has_custom_target_image(base_dir: Path, filename: str) -> bool:
    """해당 타겟이 UI에서 캡처한 커스텀 템플릿을 쓰는지 확인합니다."""
    return (custom_targets_dir(base_dir) / filename).exists()


def save_custom_target_image(base_dir: Path, filename: str, png_bytes: bytes) -> Path:
    """캡처한 PNG 바이트를 커스텀 템플릿으로 원자적으로 저장하고 경로를 반환합니다.

    디스크 부족 등으로 쓰기가 중단돼도 잘린 PNG가 최우선 경로에 남아
    다음 시작을 막는 일이 없도록 임시 파일에 쓴 뒤 교체합니다.
    """
    path = custom_targets_dir(base_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(png_bytes)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def delete_custom_target_image(base_dir: Path, filename: str) -> bool:
    """커스텀 템플릿을 삭제해 기본 이미지로 되돌립니다. 삭제했으면 True."""
    path = custom_targets_dir(base_dir) / filename
    if path.exists():
        path.unlink()
        return True
    return False


def _read_embedded_targets_json() -> Optional[str]:
    """내장된 targets.json 문자열(있으면)을 반환합니다."""
    a = _embedded()
    if a is not None:
        return getattr(a, "TARGETS_JSON", None)
    return None

# UI 입력칸의 기본값입니다.
WINDOW_TITLE = "FC Online"

# 아무것도 발견되지 않았을 때 CPU 과부하를 막기 위한 기본 대기 시간입니다.
LOOP_SLEEP_SECONDS = 0.03

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# WGC 세션 시작 뒤 첫 프레임을 기다리는 최대 시간입니다.
WGC_FIRST_FRAME_TIMEOUT_SECONDS = 2.0

# ─── 등수 OCR (큐 매칭 화면에서 내 등수 읽기) ───
# 내 정보는 왼쪽, 상대는 오른쪽 → 왼쪽 일부만 OCR하면 상대 등수는 안 읽힘.
RANK_OCR_ENABLED = True
RANK_OCR_INTERVAL_SECONDS = 1.0   # 이 간격마다 1회 OCR (2~3초 떠있으면 충분히 잡음)
RANK_OCR_LEFT_FRACTION = 0.45     # 프레임 가로의 왼쪽 비율만 OCR (상대=오른쪽 제외)
RANK_OCR_TOP_FRACTION = 0.0       # 위쪽 잘라낼 비율(0=전체 높이)
RANK_OCR_BOTTOM_FRACTION = 1.0    # 아래쪽 경계 비율(1.0=끝까지)

# ─── SKIP 자동 넘기기 ───
# 화면에 'SKIP'(대소문자 무관) 또는 '스킵' 글자가 보이면, 사라질 때까지
# A(=s)와 Start를 번갈아 눌러 넘긴다.
SKIP_ENABLED = True
SKIP_OCR_INTERVAL_SECONDS = 0.3   # 이 간격마다 SKIP 텍스트 확인(작을수록 빨리 반응·무거움)
SKIP_PRESS_DELAY_SECONDS = 0.05   # A·Start 누름 사이 지연
SKIP_OCR_MAX_WIDTH = 1280         # OCR 전 이 폭으로 축소(0=축소 안 함). 속도용.
# SKIP을 찾을 영역(프레임 비율). 기본=전체. 특정 위치만 보려면 좁히세요.
SKIP_OCR_LEFT_FRACTION = 0.0
SKIP_OCR_RIGHT_FRACTION = 1.0
SKIP_OCR_TOP_FRACTION = 0.0
SKIP_OCR_BOTTOM_FRACTION = 1.0

# 매칭 영역 중심 주변에서 클릭 좌표를 약간 조정합니다.
# 허가된 UI 테스트에서 고정 좌표 취약성을 줄이기 위한 안정화 값입니다.
CLICK_JITTER_PIXELS = 3

# WM_LBUTTONDOWN과 WM_LBUTTONUP 사이의 짧은 지연입니다.
CLICK_MESSAGE_DELAY_SECONDS = 0.01

# 마우스를 대상 위치에 올린 뒤 클릭하기 전 기다리는 시간입니다.
MOUSE_HOVER_BEFORE_CLICK_SECONDS = 0.8

# PostMessage 가상 마우스 이동 단계와 전체 이동 시간입니다.
CURVED_CLICK_MIN_STEPS = 15
CURVED_CLICK_MAX_STEPS = 25
CURVED_CLICK_MOVE_DURATION_SECONDS = 0.2

# DWM 확장 프레임 bounds 속성입니다.
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# 화면 영역 캡처 모드의 기본 영역입니다.
DEFAULT_REGION_X = 0
DEFAULT_REGION_Y = 0
DEFAULT_REGION_WIDTH = 1280
DEFAULT_REGION_HEIGHT = 720

TARGET_CONFIG_FILENAME = "targets.json"
DEFAULT_TARGET_CONFIGS: list[dict[str, object]] = [
    {"name": "target_A", "filename": "target_A.png", "action": "click"},
    {"name": "target_B", "filename": "target_B.png", "action": "click"},
    {"name": "target_C", "filename": "target_C.png", "action": "click", "vibrate_before_click": True},
    {"name": "target_D", "filename": "target_D.png", "action": "click"},
    {
        "name": "target_E",
        "filename": "target_E.png",
        "action": "key",
        "key": "s",
        "key_mode": "sendinput",
        "key_target": "all",
    },
    {
        "name": "target_F",
        "filename": "target_F.png",
        "action": "key",
        "key": "esc",
        "key_mode": "sendinput",
        "key_target": "all",
    },
    {
        "name": "target_G",
        "filename": "target_G.png",
        "action": "key",
        "key": "esc",
        "key_mode": "sendinput",
        "key_target": "all",
    },
    {"name": "target_H", "filename": "target_H.png", "action": "click"},
    {"name": "target_I", "filename": "target_I.png", "action": "click"},
]

# 1차 사전필터 축소 배율. 클수록 CPU↓ (최종 클릭 정확도는 ROI 정밀매칭이라 무관).
# 2=가장 안전(권장, 8/8 탐지). 3=CPU 2.4배↓이나 얇은(16px) 템플릿 누락 위험.
DOWNSCALE_FACTOR = 2


@dataclass
class TargetImage:
    """탐지할 이미지 정보입니다."""

    name: str
    filename: str
    wait_after_click: float
    threshold: float = 0.8
    action: str = "click"
    key: Optional[str] = None
    key_mode: str = "sendinput"
    key_target: str = "all"
    message: Optional[str] = None
    message_mode: str = "sendmessage"
    message_target: str = "top"
    message_wparam: Optional[int] = None
    message_lparam: Optional[int] = None
    command_id: Optional[int] = None
    notify_code: int = 0
    control_id: Optional[int] = None
    control_hwnd: Optional[int] = None
    control_class: Optional[str] = None
    control_text: Optional[str] = None
    vibrate_before_click: bool = False

    # load_targets()에서 GrayScale 이미지가 채워집니다.
    # repr=False로 두면 로그에 큰 NumPy 배열 내용이 출력되지 않습니다.
    image_gray: Optional[np.ndarray] = field(default=None, repr=False)

def _parse_optional_int(value: object, field_name: str) -> Optional[int]:
    """JSON 숫자 또는 0x 문자열을 int로 변환합니다."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name}에는 bool이 아니라 숫자를 넣어야 합니다.")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name}에는 정수 값을 넣어야 합니다: {value!r}")
        return int(value)
    if isinstance(value, str):
        return int(value.strip(), 0)
    raise ValueError(f"{field_name} 값을 정수로 해석할 수 없습니다: {value!r}")

def _target_from_config(config: dict[str, object], index: int) -> Optional[TargetImage]:
    """targets.json 항목 하나를 TargetImage 설정으로 변환합니다."""

    if bool(config.get("enabled", True)) is False:
        return None

    filename_value = config.get("filename")
    if not filename_value:
        raise ValueError(f"{index + 1}번째 타겟에 filename이 없습니다.")

    filename = str(filename_value)
    name = str(config.get("name") or Path(filename).stem)
    action = str(config.get("action", "click")).strip().lower()
    if action not in ("click", "key", "message"):
        raise ValueError(f"{name}의 action은 click, key, message 중 하나여야 합니다: {action!r}")

    key_value = config.get("key")
    key = str(key_value).strip() if key_value is not None else None
    if action == "key" and not key:
        raise ValueError(f"{name}은 key action이라 key 값이 필요합니다.")

    key_mode = str(config.get("key_mode", "sendinput")).strip().lower()

    key_target = str(config.get("key_target", "all")).strip().lower()

    message_value = config.get("message")
    message = str(message_value).strip() if message_value is not None else None
    if action == "message" and not message:
        raise ValueError(f"{name}은 message action이라 message 값이 필요합니다.")

    message_mode = str(config.get("message_mode", "sendmessage")).strip().lower()
    if message_mode not in ("postmessage", "sendmessage"):
        raise ValueError(f"{name}의 message_mode는 postmessage 또는 sendmessage여야 합니다: {message_mode!r}")

    has_control_filter = any(
        config.get(field) not in (None, "")
        for field in ("control_id", "control_hwnd", "control_class", "control_text")
    )
    default_message_target = "control" if has_control_filter else "top"
    message_target = str(config.get("message_target", default_message_target)).strip().lower()
    if message_target not in ("top", "focus", "control", "all"):
        raise ValueError(
            f"{name}의 message_target은 top, focus, control, all 중 하나여야 합니다: {message_target!r}"
        )

    message_wparam = _parse_optional_int(config.get("wparam"), "wparam")
    message_lparam = _parse_optional_int(config.get("lparam"), "lparam")
    command_id = _parse_optional_int(config.get("command_id"), "command_id")
    notify_code = _parse_optional_int(config.get("notify_code"), "notify_code") or 0
    control_id = _parse_optional_int(config.get("control_id"), "control_id")
    control_hwnd = _parse_optional_int(config.get("control_hwnd"), "control_hwnd")
    control_class_value = config.get("control_class")
    control_text_value = config.get("control_text")
    control_class = str(control_class_value).strip() if control_class_value is not None else None
    control_text = str(control_text_value).strip() if control_text_value is not None else None

    threshold = float(config.get("threshold", 0.8))
    threshold = max(0.0, min(1.0, threshold))
    wait_after_click = float(
        config.get("wait_after_action", config.get("wait_after_click", 0.0))
    )

    return TargetImage(
        name=name,
        filename=filename,
        wait_after_click=max(0.0, wait_after_click),
        threshold=threshold,
        action=action,
        key=key,
        key_mode=key_mode,
        key_target=key_target,
        message=message,
        message_mode=message_mode,
        message_target=message_target,
        message_wparam=message_wparam,
        message_lparam=message_lparam,
        command_id=command_id,
        notify_code=notify_code,
        control_id=control_id,
        control_hwnd=control_hwnd,
        control_class=control_class,
        control_text=control_text,
        vibrate_before_click=bool(config.get("vibrate_before_click", False)),
    )

def load_target_definitions(
    base_dir: Path,
    logger: Optional[LogCallback] = None,
) -> list[TargetImage]:
    """targets.json에서 타겟 설정을 읽고, 없으면 기본 설정을 사용합니다."""

    log = logger or print
    config_path = base_dir / TARGET_CONFIG_FILENAME
    raw_targets: object = DEFAULT_TARGET_CONFIGS

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                raw_config = json.load(config_file)
            raw_targets = raw_config.get("targets", raw_config) if isinstance(raw_config, dict) else raw_config
        except json.JSONDecodeError as exc:
            log(f"[설정 오류] {config_path} JSON 형식 오류 (줄 {exc.lineno}, 칸 {exc.colno}): {exc.msg}")
            log("[설정 안내] 기본 타겟 설정을 대신 사용합니다.")
            raw_targets = DEFAULT_TARGET_CONFIGS
        except Exception as exc:
            log(f"[설정 오류] {config_path} 파일을 읽지 못했습니다: {exc}")
            log("[설정 안내] 기본 타겟 설정을 대신 사용합니다.")
            raw_targets = DEFAULT_TARGET_CONFIGS
    else:
        # 느슨한 targets.json이 없으면 바이너리에 내장된 설정을 사용합니다(판매본).
        embedded = _read_embedded_targets_json()
        if embedded:
            try:
                raw_config = json.loads(embedded)
                raw_targets = raw_config.get("targets", raw_config) if isinstance(raw_config, dict) else raw_config
            except Exception:
                raw_targets = DEFAULT_TARGET_CONFIGS
        else:
            log(f"[설정 안내] {TARGET_CONFIG_FILENAME}이 없어 기본 타겟 설정을 사용합니다.")

    if not isinstance(raw_targets, list):
        log("[설정 오류] 타겟 설정은 리스트이거나 {'targets': [...]} 형태여야 합니다.")
        raw_targets = DEFAULT_TARGET_CONFIGS

    targets: list[TargetImage] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict):
            log(f"[설정 오류] {index + 1}번째 타겟 설정이 객체가 아니라 건너뜁니다.")
            continue
        try:
            target = _target_from_config(raw_target, index)
        except Exception as exc:
            log(f"[설정 오류] {index + 1}번째 타겟 설정을 건너뜁니다: {exc}")
            continue
        if target is not None:
            targets.append(target)

    if targets:
        return targets

    log("[설정 오류] 사용할 타겟이 없어 기본 타겟 설정을 사용합니다.")
    return [
        target
        for target in (
            _target_from_config(config, index)
            for index, config in enumerate(DEFAULT_TARGET_CONFIGS)
        )
        if target is not None
    ]

def clone_target_definition(target: TargetImage) -> TargetImage:
    """이미지 배열 없이 타겟 설정만 복사합니다."""

    return TargetImage(
        name=target.name,
        filename=target.filename,
        wait_after_click=target.wait_after_click,
        threshold=target.threshold,
        action=target.action,
        key=target.key,
        key_mode=target.key_mode,
        key_target=target.key_target,
        message=target.message,
        message_mode=target.message_mode,
        message_target=target.message_target,
        message_wparam=target.message_wparam,
        message_lparam=target.message_lparam,
        command_id=target.command_id,
        notify_code=target.notify_code,
        control_id=target.control_id,
        control_hwnd=target.control_hwnd,
        control_class=target.control_class,
        control_text=target.control_text,
        vibrate_before_click=target.vibrate_before_click,
    )

def load_targets(
    base_dir: Path,
    logger: Optional[LogCallback] = None,
    definitions: Optional[list[TargetImage]] = None,
) -> Optional[list[TargetImage]]:
    """설정된 타겟 이미지를 GrayScale 이미지로 미리 로드합니다."""

    log = logger or print
    target_definitions = definitions or load_target_definitions(base_dir, logger=log)
    targets = [clone_target_definition(target) for target in target_definitions]

    def _decode(raw_bytes: bytes) -> Optional[np.ndarray]:
        try:
            file_bytes = np.frombuffer(raw_bytes, dtype=np.uint8)
            return cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception:
            return None

    for target in targets:
        is_custom = has_custom_target_image(base_dir, target.filename)
        raw = _read_asset_bytes(target.filename, base_dir)
        if raw is None:
            log(f"[오류] 이미지 자산을 찾을 수 없습니다: {target.filename}")
            log("       (느슨한 파일도 없고 바이너리 내장 자산도 없습니다)")
            return None

        image_bgr = _decode(raw)

        if image_bgr is None and is_custom:
            # 손상된 커스텀 캡처가 시작 자체를 막지 않도록 기본 이미지로 자가 복구합니다.
            log(f"[경고] 커스텀 캡처가 손상되어 기본 이미지를 대신 사용합니다: {target.filename}")
            log("       해당 타겟을 다시 캡처하거나 '기본값' 버튼으로 정리하세요.")
            is_custom = False
            raw = _read_asset_bytes(target.filename, base_dir, include_custom=False)
            image_bgr = _decode(raw) if raw is not None else None

        if image_bgr is None:
            log(f"[오류] 이미지 로드에 실패했습니다: {target.filename}")
            log("       파일이 손상되었거나 OpenCV가 읽을 수 없는 형식일 수 있습니다.")
            return None

        target.image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        source = "커스텀 캡처" if is_custom else "기본"
        log(
            f"[이미지 로드] {target.filename} ({source}), "
            f"크기={target.image_gray.shape[1]}x{target.image_gray.shape[0]}, "
            f"임계값={target.threshold:.2f}, action={target.action}"
        )

    return targets

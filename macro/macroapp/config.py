from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from macroapp.logging_util import LogCallback
from macroapp.paths import app_dir

FC_ONLINE_PROCESS_NAMES = ["fczf"]

# UI 입력칸의 기본값입니다.
WINDOW_TITLE = "FC Online"

# 아무것도 발견되지 않았을 때 CPU 과부하를 막기 위한 기본 대기 시간입니다.
LOOP_SLEEP_SECONDS = 0.03

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# WGC 세션 시작 뒤 첫 프레임을 기다리는 최대 시간입니다.
WGC_FIRST_FRAME_TIMEOUT_SECONDS = 2.0

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

    for target in targets:
        image_path = base_dir / target.filename

        if not image_path.exists():
            log(f"[오류] 이미지 파일을 찾을 수 없습니다: {image_path}")
            log(f"       targets.json의 filename을 확인하거나 파일을 같은 폴더에 넣어주세요.")
            return None

        try:
            # cv2.imread는 한글/특수문자 경로에서 실패하는 경우가 있어,
            # read_bytes + np.frombuffer + cv2.imdecode (유니코드 경로 안전, str 변환 불필요).
            file_bytes = np.frombuffer(image_path.read_bytes(), dtype=np.uint8)
            image_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception as exc:
            log(f"[오류] 이미지 파일을 읽는 중 문제가 발생했습니다: {image_path}")
            log(f"       원본 오류: {exc}")
            return None

        if image_bgr is None:
            log(f"[오류] 이미지 로드에 실패했습니다: {image_path}")
            log("       파일이 손상되었거나 OpenCV가 읽을 수 없는 형식일 수 있습니다.")
            return None

        target.image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        log(
            f"[이미지 로드] {target.filename}, "
            f"크기={target.image_gray.shape[1]}x{target.image_gray.shape[0]}, "
            f"임계값={target.threshold:.2f}, action={target.action}"
        )

    return targets

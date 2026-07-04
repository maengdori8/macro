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

# 대상 창 제목 기본값. 빈 값이면 제목으로 찾지 않고 프로세스명(FC_ONLINE_PROCESS_NAMES,
# 예: fczf)으로만 대상을 찾습니다. 창 제목이 바뀌어도 영향받지 않게 프로세스 기반이 기본.
WINDOW_TITLE = ""

# 아무것도 발견되지 않았을 때 CPU 과부하를 막기 위한 기본 대기 시간입니다.
LOOP_SLEEP_SECONDS = 0.03

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# WGC 세션 시작 뒤 첫 프레임을 기다리는 최대 시간입니다.
WGC_FIRST_FRAME_TIMEOUT_SECONDS = 2.0

# ─── 등수/티어/점수 OCR (공식경기 감독모드 화면에서 내 정보 읽기) ───
# 내 팀=왼쪽, 상대=오른쪽. 내 점수/티어는 왼쪽 팀 아래에 뜬다(예: '2229점', '챌린저 3부 감독').
# 전체 높이를 읽으면 상단 중앙의 '공식경기 감독모드' 제목까지 잡혀 '경기 감독'으로 오인식됨.
# → 가로는 왼쪽 절반(상대 제외), 세로는 화면 중하단 띠(상단 제목 제외)만 본다.
RANK_OCR_ENABLED = True
# 패널이 ~0.7초만 떠서, 시간 간격으로 폴링하면 윈도우를 통째로 놓칠 수 있다.
# → 워커는 '새 프레임이 올라올 때마다'(프레임 신선도) OCR하고, 이 값은 단지 하한(0=제한 없음).
RANK_OCR_INTERVAL_SECONDS = 0.1   # 등수 OCR 최소 간격(초). 0=매 프레임(CPU 폭증). 0.1=최대 10회/초로
                                  # 제한 → CPU 대폭↓. 패널이 수 초간 떠 있어 0.1초×3표=0.3초면 확정(인식 충분히 빠름).
RANK_OCR_LEFT_FRACTION = 0.50     # 가로: 0~이 비율(상대=오른쪽 제외)
RANK_OCR_TOP_FRACTION = 0.45      # 세로 시작 비율(상단 제목/로고 제외)
RANK_OCR_BOTTOM_FRACTION = 0.66   # 세로 끝 비율(하단 미리보기 이미지 제외)

# ─── 매치 화면 게이트 ───
# 등수/점수는 '공식경기 감독모드'(팀 정보/유니폼 선택 탭이 보이는 화면)에서만 읽는다.
# 이 영역(상단 중앙 탭 바)에서 '팀 정보'/'유니폼'이 보일 때만 등수 OCR을 진행 →
# 구단 관리 등 다른 화면의 숫자를 등수/점수로 오인식하는 일을 원천 차단.
MATCH_GATE_ENABLED = True
MATCH_GATE_LEFT_FRACTION = 0.37   # 탭 바 가로 좌
MATCH_GATE_RIGHT_FRACTION = 0.64  # 탭 바 가로 우
MATCH_GATE_TOP_FRACTION = 0.17    # 탭 바 세로 상
MATCH_GATE_BOTTOM_FRACTION = 0.26 # 탭 바 세로 하
MATCH_GATE_TOKENS = ("팀정보", "유니폼")  # 공백 제거 후 부분일치(둘 중 하나면 매치 화면)

# ── 등수 OCR 정확도(전처리·컨센서스) ──
# 전처리: crop을 목표 높이로 정수배 업스케일(작은 글자 인식률↑) + 대비 스트레칭.
RANK_OCR_TARGET_HEIGHT = 320      # crop 높이를 이 픽셀에 맞춰 스케일(해상도 무관 글자 크기 일정화)
RANK_OCR_MAX_UPSCALE = 3.0        # 업스케일 상한(과확대 방지)
RANK_OCR_MAX_WIDTH = 1100         # OCR 입력 폭 상한(>이면 축소). OCR 1회 비용을 눌러 0.7초 내 표본↑
RANK_OCR_INVERT_FALLBACK = True   # 1차 인식 실패 시에만 흑백 반전 후 1회 재시도(light-on-dark 보강)
# 컨센서스: 0.7초 동안 얻은 여러 읽기를 필드별 최빈값으로 확정(단발 오인식 폐기).
RANK_OCR_VOTE_MIN = 3             # 같은 값 이 표 이상이면 즉시 확정(단발·2프레임 오독 차단 위해 3)
RANK_OCR_PANEL_GAP_SECONDS = 0.9  # 이 시간 이상 패널 미검출이면 새 패널로 보고 투표 초기화
RANK_OCR_COMMIT_AFTER_GONE = 0.35 # 패널이 사라진 뒤 이 시간 지나면 1표라도 확정(놓치지 않기)

# ─── SKIP 자동 넘기기 ───
# 화면에 'SKIP'(대소문자 무관) 또는 '스킵' 글자가 보이면, 사라질 때까지
# A(=s)와 Start를 번갈아 눌러 넘긴다.
SKIP_ENABLED = True
SKIP_OCR_INTERVAL_SECONDS = 0.3   # 이 간격마다 SKIP 텍스트 확인(작을수록 빨리 반응·무거움)
SKIP_PRESS_DELAY_SECONDS = 0.05   # A·Start 누름 사이 지연
# '(A) SKIP' 버튼 위치 클릭 시도 — 실측 결과 이 게임은 클릭으론 스킵 안 됨 → 기본 False.
SKIP_A_CLICK = False
# (A) SKIP 처리 — 실측: 게임이 가상 START는 인식하지만 가상 A는 무시(탭·1s 홀드 모두).
# 키보드 s '탭'은 통함 → 프롬프트는 탭 기반이고, 게임이 생각하는 'A 동작'이
# XInput A와 어긋나 있을 가능성(자체 DirectInput 매핑/컨트롤러 설정)이 높다.
#
# 전략: 아래 후보 버튼을 한 사이클에 하나씩 눌러보고(자동 탐색), 스킵이 사라지면
# 그 버튼을 '학습'해 다음부터 바로 사용한다 — 비활성 100% 유지.
# 이미 답을 알면 SKIP_A_BUTTON에 지정(예: "b")하면 탐색 없이 바로 그 버튼만 쓴다.
# 실측(2026-07-03): focus_s가 통하지만 포커스 전환 때문에 화면이 깜빡임.
# → 깜빡임 0인 무포커스 기법(attach_post_s, attach_state_s)을 먼저 재학습하도록 비워둔다.
# 무깜빡임 기법이 통하면 그게 학습되고, 안 통하면 focus_s로 폴백(깜빡이지만 동작).
SKIP_A_BUTTON = ""
# 실측: 가상패드는 START 외 전부 무시. 그리고 사용자 요구 = '화면 변화 0'(자동화).
# 기본 스윕은 '깜빡임 0 + 유출 0' 방식만 넣는다(적대적 리뷰로 검증한 4기법):
#   attach_state_s = AttachThreadInput+SetKeyboardState로 GetKeyState 폴링을 속임
#   char_s         = WM_KEYDOWN+WM_CHAR+WM_KEYUP 딥 전송(CEF 크롬 UI가 WM_CHAR로 받음)
#   attach_post_s  = AttachThreadInput(큐 공유)+포커스창에 WM_KEYDOWN/UP(SetFocus 없음)
#   pm_s           = WM_KEYDOWN 딥 전송(WM_CHAR 없이)
#   (가상패드 후보도 화면 변화 0이라 함께 순회 — 이 게임엔 대부분 무효지만 무해)
# 통한 입력은 학습돼 다음부턴 바로 사용된다.
#
# ⚠️ 스윕에 일부러 뺀 것(부작용 있음, 필요하면 SKIP_A_BUTTON로 수동 지정):
#   "focus_s" = 확실히 동작하지만 SetFocus가 top-level을 활성화해 '화면이 깜빡임'.
#   "si_s"    = 깜빡임은 없지만 그 순간 활성인 다른 앱에 's'가 새어 들어갈 수 있음.
# 진단: 어떤 것도 안 통하면 게임 입력이 RawInput/DirectInput 포그라운드 전용이라
# 유저모드로는 '비활성+깜빡임0+유출0'이 원리적으로 불가(로그의 창 클래스 덤프로 판별).
# focus_child_s = focus_s와 같은 원리인데 자식 창에 SetFocus → 깜빡임 없이 동작 기대.
# 실측상 focus_s(top-level)만 먹혔으므로, 그 '먹히는 원리'를 유지하되 깜빡임만 제거한 이게
# 1순위. 안 되면(자식 창 없음 등) 나머지 무깜빡임 기법으로 순회.
SKIP_A_SWEEP_BUTTONS = ("focus_child_s", "attach_state_s", "char_s", "attach_post_s", "pm_s",
                        "a", "b", "x", "y", "rb", "lb", "rt", "lt", "down")
SKIP_A_TAP_SECONDS = 0.15    # 후보 버튼 탭 길이
# 최후수단(전면 순간전환+SendInput 키보드 s)은 '탐색을 다 돌고도' 이 시간 이상 지났을 때만.
# 0 = 완전 끄기(기본) → 비활성 100% 보장. 스킵을 못 넘겨도 컷신은 자연 종료되므로 안전.
SKIP_A_SENDINPUT_AFTER_SECONDS = 0.0
SKIP_OCR_MAX_WIDTH = 1280         # OCR 전 이 폭으로 축소(0=축소 안 함). 속도용.
# SKIP을 찾을 영역(프레임 비율). FC온라인 스킵 안내(일반·(A)형)는 항상 화면 하단에 뜨므로
# 하단 22%만 본다 → skip-A matchTemplate + OCR 비용을 ~5배 줄이고 노이즈도 차단(CPU 대폭↓).
SKIP_OCR_LEFT_FRACTION = 0.0
SKIP_OCR_RIGHT_FRACTION = 1.0
SKIP_OCR_TOP_FRACTION = 0.78
SKIP_OCR_BOTTOM_FRACTION = 1.0

# ─── SKIP 버튼 분기(A형 vs Start형) ───
# '(A) SKIP'처럼 초록 A 버튼이 붙은 스킵은 A만, 그 외 일반 스킵은 Start만 누른다.
# A형은 target_skip_a.png 템플릿(grayscale 매칭)으로 먼저 잡고, 매칭되면 그 프레임은
# OCR 'skip' 텍스트를 읽지 않는다(= A형일 때 Start 경로로 안 샌다).
# 템플릿 파일이 없으면 match는 항상 False → 기존처럼 OCR 텍스트→Start로 안전 폴백.
SKIP_A_MATCH_THRESHOLD = 0.82     # (A) SKIP 템플릿 상관도 이 값 이상이면 A형으로 판정
SKIP_TEXT_CONSENSUS = 2           # 일반 스킵(OCR 'skip')은 이만큼 연속 감지돼야 Start(단발 노이즈 차단)
SKIP_FALLBACK_BOTH_SECONDS = 0.6  # 한 스킵이 이 시간 넘게 안 사라지면 A·Start 둘 다(템플릿 빗나가도 안 갇힘)

# ─── 자동 정지 단발 오독 가드 ───
# 합의로 확정된 값이라도, 같은 정지조건이 연속 이 횟수만큼 확정될 때만 실제로 멈춘다.
# (한 번의 오독이 작업 전체를 멈추는 사고를 막는다. 1=즉시 정지=기존 동작.)
STOP_CONFIRM_COUNT = 2

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

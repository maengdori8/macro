from __future__ import annotations
import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from macroapp.skip_candidates import (
    SKIP_A_CANDIDATES,
    SKIP_GENERIC_ANY_KEY_CANDIDATES,
    SKIP_GENERIC_CANDIDATES,
    SKIP_GENERIC_ESCAPE_CANDIDATES,
    SKIP_GENERIC_HIGHLIGHT_CANDIDATES,
    SKIP_S_CANDIDATES,
)

import cv2
import numpy as np

from macroapp.logging_util import LogCallback

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

# ─── 감독모드 즉시 종료 (프로 전용) ───
#
# 경기 도중 나가는 절차가 길어서, 한 번에 정리하고 다음 경기로 넘어가기 위한 기능입니다.
# 형제 프로젝트(../mpause)에서 이식했고 판정 로직은 exit_core.py 로 통째로 복사했습니다
# (두 사본이 갈라지지 않게 tests/test_no_drift.py 가 바이트 비교로 지킵니다).
#
# ⚠️ 이 값들은 UI 에 노출하지 않습니다. 대상 이름이나 동작 방식이 화면에 보이면
# 판매본에서 그대로 드러납니다.
EXIT_TARGET_PROCESS_NAME = FC_ONLINE_PROCESS_NAMES[0]
EXIT_HOLD_SECONDS = 10.0
# ⚠️ 10초는 **경계값**이다. 2026-08-23 실측(8-22 프로 로그, 자동 종료 8건 전수 추적):
#   성공 5건 = 마무리가 15~20초에 끝나고 16~37초 안에 로비 도달
#   실패 3건 = 마무리가 50초(=홀드10+EXIT_TIMEOUT_SECONDS40) 타임아웃 뒤 '게임 화면을
#              확인해 주세요', 그리고 **경기가 그대로 계속 진행**됐다(249·391·598초 뒤에야
#              경기후 프롬프트 등장; [7]은 완료 직후 target_J 전술창=인게임이 잡혔다).
# 즉 실패는 '확인 버튼을 못 눌러서'가 아니라 **정지 10초로 접속이 안 끊겨 확인 창 자체가
# 안 뜬 것**이다(같은 10초가 어떤 판은 되고 어떤 판은 안 된다 = 서버 타임아웃 경계).
# 그래서 재시도는 **홀드를 늘려서** 한다 — 같은 10초로 다시 하면 같은 결과다(실측 [6]→[7]:
# 재시도도 50초 타임아웃 뒤 598초까지 경기 지속).
# ⚠️⚠️ **상한은 절대 넘기지 말 것 — 넘기면 게임이 통째로 꺼진다.**
# 사용자 실측(2026-08-23): "정지 시간 너무 오래하면 겜 자체가 꺼짐, 딱 허용수치가 11초정도".
# 게임이 스스로 프리즈를 감지해 종료하는 것으로 보인다. 무인 방치가 기본이라 게임이 꺼지면
# 매크로가 창을 못 찾아 그날 나머지가 통째로 날아간다(8-18 의 3시간 정지가 그 사고였다).
# 그래서 재시도 상승 폭은 10.0 → 11.0 사이 0.5초씩뿐이다. 신뢰성의 주 수단은 홀드 상승이
# 아니라 **재시도 자체**(로비 미도달 트리거)다.
EXIT_RETRY_HOLD_STEP_SECONDS = 0.5    # 재시도마다 홀드를 이만큼만 늘린다(여유가 1초뿐)
EXIT_RETRY_HOLD_MAX_SECONDS = 11.0    # 절대 상한 — 넘기면 게임이 꺼진다(사용자 실측)
EXIT_TICK_SECONDS = 0.05

#: 마무리 단계에서 찾을 그림. targets.json 의 같은 이름을 그대로 씁니다
#: (자동화가 쓰는 것과 **같은 픽셀** — 사본을 따로 두지 않습니다).
EXIT_TARGET_NAME = "target_H"
EXIT_MATCH_THRESHOLD = 0.8

#: 대상 창을 찾는 최대 시간(초).
EXIT_WINDOW_WAIT_SECONDS = 6.0
#: 그림이 뜨기를 기다리는 최대 시간(초). 이 안에 한 번도 안 보이면 조용히 끝냅니다.
EXIT_TIMEOUT_SECONDS = 40.0
#: 한 번 누른 뒤 사라졌는지 확인하는 시간(초).
EXIT_VERIFY_SECONDS = 1.2
#: 사라진 뒤 이만큼 더 안 보이면 성공으로 확정합니다(한 프레임 깜빡임 방지).
EXIT_SETTLE_SECONDS = 0.6
#: 최대 시도 횟수(무한 입력 방지).
EXIT_MAX_PRESSES = 8
#: 시도 순서. 좌표를 먼저 누르고, 안 먹으면 패드로 바꿔 봅니다.
EXIT_METHODS = ("click", "pad")
#: 패드로 누를 버튼(메뉴 확정 = A).
EXIT_PAD_BUTTON = "a"
#: 화면을 여는 패드 버튼 — 가만히 두면 찾는 그림이 뜨지 않습니다(실측).
#: ⚠️ XUSB↔DInput 번호가 어긋나 START 가 RT 로 받힌 이력이 있습니다.
#: 이 화면에서 start 가 안 먹으면 그 어긋남 기준의 다른 버튼부터 실측하세요.
EXIT_OPEN_BUTTON = "start"
#: 재개 후 첫 열기 입력까지의 지연(초). 0 = 되살아나자마자 바로 누릅니다(사용자 요구).
EXIT_OPEN_DELAY_SECONDS = 0.0
#: 찾는 그림을 **처음 본 뒤** 누르기까지 기다리는 시간(초).
#: ⚠️ 이 값은 mPause(FOLLOWUP_FIRST_PRESS_DELAY_SECONDS=3.0)에 있던 것인데
#: macro 로 옮길 때 ConfirmSequence 에 넘기는 배선이 빠져 0 으로 동작했다.
#: 그림이 뜬 직후는 화면이 자리 잡는 중이라 입력이 안 먹거나 엉뚱하게 들어간다.
EXIT_FIRST_PRESS_DELAY_SECONDS = 3.0
#: 열기 입력 뒤에도 안 뜨면 다시 누르는 간격(초). 너무 짧으면 이미 열린 화면을
#: 두 번째 입력이 도로 닫습니다(토글).
EXIT_OPEN_RETRY_SECONDS = 5.0
#: 열기 입력 최대 횟수. 그림이 한 번이라도 보이면 더 누르지 않습니다.
EXIT_MAX_OPENS = 3
#: 인식 루프의 최소 간격(초).
EXIT_LOOP_SLEEP_SECONDS = 0.03

# ─── 0:2 패배 자동 종료 (프로 전용) ───
#
# 경기 스코어가 0:2 가 되면 즉시 종료를 **일부 판에서만** 자동 실행합니다.
# 매번 나가면 비매너 점수가 쌓이므로, 열세 판을 따로 세고 그중 일부만 나갑니다
# (기본 40%, 서버 운영 설정으로 조절). 판정 규칙은 macroapp/auto_exit.py 에 있습니다.
AUTO_EXIT_ENABLED = True
#: 상대가 이 점수차 이상 앞서면 열세로 판정합니다(내 팀=왼쪽 박스).
#: 2 면 0:2, 0:3, 1:3, 2:4 … 전부 해당. 정확한 스코어 하나가 아니라 점수차 기준입니다.
AUTO_EXIT_DEFICIT_GOALS = 2
#: 센 패배 중 나가는 비율. 0.4 = 20판 중 8판.
AUTO_EXIT_RATIO = 0.4
#: 스코어보드 OCR 영역(프레임 비율 x1,y1,x2,y2).
#: 실측(사용자 녹화, 1920x1080 최대화 창): 스코어보드는 상단 **왼쪽**이고
#: 점수는 콜론 없는 박스 두 개(`팀명 [0][0] 상대명 … [19:05]`)다. 이 영역은
#: 그 박스 두 개만 덮는다 — 오른쪽 시계(19:05)와 상대 닉네임(숫자일 수 있음,
#: 실측 예: "4903")이 들어오면 파서가 전부 버리므로 절대 넓히지 말 것.
#: 파일 로그의 '[자동 종료] 스코어 읽음' 값으로 미세 조정한다.
AUTO_EXIT_SCORE_REGION = (0.150, 0.050, 0.210, 0.130)
#: 스코어 OCR 최소 간격(초). 등수 OCR 과 같은 워커에서 돌므로 과하게 줄이지 말 것.
AUTO_EXIT_OCR_INTERVAL_SECONDS = 1.0
#: '박스는 보이는데 숫자 미상'(템플릿 없는 숫자 4~9) 순간의 스코어 영역을 logs/score_unknown/
#: 에 저장하는 세션당 상한. 글리프 표본을 스크린샷 없이 실전에서 모으기 위한 것이다 —
#: 파일 로그의 '숫자 미상'이 그 신호였는데, 신호만 있고 표본이 없으면 다음 빌드에 못 넣는다.
AUTO_EXIT_UNKNOWN_DUMP_LIMIT = 30
#: 같은 미상 구간이 이어져도 이 간격마다 한 장 더 저장한다(3:1 → 4:1 처럼 숫자가 바뀌는데
#: 읽기 결과는 둘 다 '미상'이라 전이만 보면 한 장밖에 안 남는다).
AUTO_EXIT_UNKNOWN_DUMP_INTERVAL_SECONDS = 60.0
# ─── 종료 규칙 기본값(구매자별 서버 설정이 덮어쓴다 — auto_exit.ExitSettings) ───
#: 대량 실점: 상대−나 ≥ 이 값이면 **비율 무시하고 무조건** 종료(0=끔).
AUTO_EXIT_HARD_DEFICIT_GOALS = 3
#: 후반 규칙: 경기 시계가 이 분 이상이고(0=끔) 상대−나 ≥ 아래 값이면 종료(비율 적용).
AUTO_EXIT_LATE_MINUTE = 70
AUTO_EXIT_LATE_DEFICIT_GOALS = 1
#: 경기 시계 박스를 찾을 영역(프레임 비율, 좌·상·우·하). 상대 닉네임 오른쪽 흰 가로
#: 박스("84:10") — 실측(1936x1056 창): 박스 위치 (592,93,94,35) → 약 (0.306,0.088)~(0.354,0.121).
#: 모양(가로로 긴 밝은 덩어리)으로 찾으므로 영역은 넉넉히 둔다. 스코어 박스는 정사각형이라 제외된다.
AUTO_EXIT_CLOCK_REGION = (0.22, 0.05, 0.45, 0.13)
#: 시계 OCR 최소 간격(초). winocr 호출 하나가 SKIP(0.3초)·등수 OCR 과 같은 워커를 쓰므로
#: 자주 돌리지 않는다 — '70분 이후' 판정에 5초 해상도면 충분하다.
AUTO_EXIT_CLOCK_INTERVAL_SECONDS = 5.0
#: 시계 값이 이만큼 연속으로 '내려가지 않게' 읽혀야 확정한다(단발 오독 차단).
AUTO_EXIT_CLOCK_CONSENSUS = 2

# ─── 감독모드 홈 화면 OCR(로비의 티어·랭킹 점수·순위) ───
# 경기 결과 패널은 0.7초만 떠서 놓치기 쉬운데, 홈 화면은 큰 글자로 오래 떠 있다.
# 매치 게이트(팀정보/유니폼 탭)가 안 보이는 화면에서만, 이 간격으로 읽는다.
HOME_OCR_ENABLED = True
#: 홈 화면 상단 카드 영역(클라이언트 비율, 좌·상·우·하). 1920x1080 실측: 제목 '세미프로 3부
#: 감독'·'랭킹 점수'·'순위' 블록이 모두 들어오고, 아래 '등급 변동/친구 순위' 탭은 제외된다.
HOME_OCR_REGION = (0.13, 0.16, 0.70, 0.47)
HOME_OCR_INTERVAL_SECONDS = 3.0
#: 같은 값이 이만큼 연속으로 읽혀야 확정한다(한 번의 오독이 등수·점수를 덮지 않게).
HOME_OCR_VOTE_MIN = 2
#: 0:2 가 이 횟수만큼 **연속** 읽혀야 한 판으로 확정합니다(오독 1회 방어).
AUTO_EXIT_CONFIRM_COUNT = 3
#: 스코어가 이 시간(초) 동안 계속 안 읽히면 경기가 끝난 것으로 봅니다.
#: 하프타임·리플레이(잠깐 사라짐)보다 길고, 경기 사이 간격(수 분)보다 짧게.
AUTO_EXIT_MATCH_RESET_SECONDS = 60.0
# '숫자 미상'만으로는 경기를 무한 연장하지 않는다. 미상은 '3:1 같은 화면을 경기 종료로
# 오인하지 마라'는 방어였는데, **경기가 아닌 화면**(구단 엠블럼·결과 패널·빈 박스)도
# 미상으로 잡혀 판 사이 공백에 한 번만 섞여도 래치가 영영 안 풀렸다.
# 실측(2026-08-23): 06:11 에 한 판이 쿼터를 쓴 뒤 세션 끝까지 발동 0회 — 0:2 를 26 게임분
# 방치. → 진짜 스코어를 이 시간 동안 못 읽으면 경기가 끝난 것으로 본다(한 판 ~13분).
AUTO_EXIT_UNKNOWN_RESET_SECONDS = 180.0
# ─── 종료 재시도 ───
# 일시정지→재시작까지 간 판은 '나가기로 확정된 판'이다. 그런데 마무리(확인 버튼)가
# 실패해 실제로는 안 나가지는 경우가 있다. 종료 시도가 끝난 뒤 이 시간이 지나도
# 같은 판에서 종료조건(열세)이 그대로면 = 아직 안 나갔으면, 쿼터를 새로 세지 않고
# 다시 시도한다. 판이 끝날 때까지(스코어보드 60초 부재) 살아 있는 래치로 '같은 판'을
# 보장하고, 무한 반복을 막기 위해 판당 최대 횟수를 둔다.
AUTO_EXIT_RETRY_SECONDS = 30.0
#: 재시도 시점에 스코어를 못 읽으면(리플레이 가림 등) 이 간격으로 다시 확인한다.
AUTO_EXIT_RETRY_RECHECK_SECONDS = 3.0
#: 한 판당 최대 재시도 횟수(게임이 영영 안 나가질 때 무한 스팸 방지).
AUTO_EXIT_RETRY_MAX = 5

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# ── 가동률 감시(정지 알림) ──────────────────────────────────────────────────
# 2026-08-22 로그 실측: 하루 판수를 가장 크게 깎는 건 컷신(판당 ~1분)이 아니라 매크로가
# 멈춰 서 있는 시간이다. 8-18 프로 로그는 게임 창이 사라진 뒤 **3시간(그날 51판의 30%)**
# 동안 '창을 찾지 못했습니다'만 7,509회 찍고 사용자가 돌아올 때까지 그대로였다.
STALL_FIRST_ALERT_SECONDS = 180.0    # 창 유실이 이만큼 이어지면 첫 알림(오탐 방지용 여유)
STALL_REPEAT_ALERT_SECONDS = 900.0   # 그 뒤 반복 알림 간격(15분) — 로그·디스코드 도배 금지
# 진행 없음은 창 유실보다 관대하게: 한 경기가 10분 넘게 걸리므로 경기 한 판보다 짧게
# 잡으면 정상 경기를 정지로 오인한다.
STALL_PROGRESS_GRACE_SECONDS = 420.0

# ── 자동 종료 '실효' 측정 ────────────────────────────────────────────────────
# 종료 러너의 '완료' 메시지는 '시도가 끝났다'는 뜻이지 '판이 끝났다'가 아니다.
# 8-22 실측: 03:55:46 '[종료] 완료' 뒤에도 04:00:11 경기후 프롬프트까지 4분 이상 경기가
# 계속됐다(마무리 확인 클릭 실패). 그래서 성공을 **다음 로비 도달**로 재정의한다.
EXIT_EFFECT_LOBBY_TARGETS = ("target_B", "target_C", "target_D")
EXIT_EFFECT_FAST_SECONDS = 60.0   # 이 안에 로비면 정상 종료, 넘으면 마무리 실패 의심
# 매 프레임마다 비교적 비싼 Win32 프로세스/클라이언트 영역 검증을 반복하지 않습니다.
# 창 종료는 WGC closed 이벤트가 즉시 잡고, 정상 상태의 유효성만 초당 한 번 재확인합니다.
WINDOW_VALIDATION_INTERVAL_SECONDS = 1.0

# ─── 잘못 열린 알림 패널 자동 복구 ───
# 일부 PC에서 화면 좌표가 어긋나 하단 알림(종) 버튼이 눌리면 오른쪽 패널이 전체 자동화를
# 가립니다. 오른쪽의 밝은 패널 + 왼쪽의 어두운 오버레이를 연속 확인한 뒤 같은 종 버튼을
# 다시 눌러 닫습니다. 좌표는 WGC 프레임 비율이라 창 테두리 유무에도 같은 위치를 가리킵니다.
NOTIFICATION_PANEL_GUARD_ENABLED = True
NOTIFICATION_PANEL_CHECK_INTERVAL_SECONDS = 0.25
NOTIFICATION_PANEL_CONFIRM_COUNT = 2
NOTIFICATION_PANEL_RETRY_SECONDS = 1.5
NOTIFICATION_TOGGLE_X_FRACTION = 0.783
NOTIFICATION_TOGGLE_Y_FRACTION = 0.977

# WGC 세션 시작 뒤 첫 프레임을 기다리는 최대 시간입니다.
WGC_FIRST_FRAME_TIMEOUT_SECONDS = 2.0
# 시작 후에는 이벤트를 이 시간까지만 기다려 정지 요청에도 빠르게 반응합니다.
# 새 프레임이 오면 즉시 깨어나므로 폴링 지연이나 불필요한 busy loop가 없습니다.
WGC_FRAME_WAIT_SECONDS = 0.1
# 자동화 루프의 실효 처리율(LOOP_SLEEP_SECONDS≈30ms)을 넘는 WGC 프레임은
# BGRA→gray 변환 전에 버립니다. 반응 속도는 그대로 두고 캡처 대역폭을 절반가량 줄입니다.
WGC_CAPTURE_MAX_FPS = 30.0

# ─── 등수/티어/점수 OCR (공식경기 감독모드 화면에서 내 정보 읽기) ───
# 내 팀=왼쪽, 상대=오른쪽. 내 점수/티어는 왼쪽 팀 아래에 뜬다(예: '2229점', '챌린저 3부 감독').
# 전체 높이를 읽으면 상단 중앙의 '공식경기 감독모드' 제목까지 잡혀 '경기 감독'으로 오인식됨.
# → 가로는 왼쪽 절반(상대 제외), 세로는 화면 중하단 띠(상단 제목 제외)만 본다.
RANK_OCR_ENABLED = True
# 패널이 ~0.7초만 떠서, 시간 간격으로 폴링하면 윈도우를 통째로 놓칠 수 있다.
# → 워커는 '새 프레임이 올라올 때마다'(프레임 신선도) OCR하고, 이 값은 단지 하한(0=제한 없음).
RANK_OCR_INTERVAL_SECONDS = 0.1   # 등수 OCR 최소 간격(초). 0=매 프레임(CPU 폭증). 0.1=최대 10회/초로
                                  # 제한 → CPU 대폭↓. 패널이 수 초간 떠 있어 0.1초×3표=0.3초면 확정(인식 충분히 빠름).
# 게이트+등수 ROI의 작은 지문이 같으면 이전 OCR 결과를 재사용합니다. 투표 횟수와
# 확정 지연은 유지하면서 같은 정지 UI를 winocr로 반복 해석하지 않습니다.
RANK_OCR_CACHE_SECONDS = 1.0
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
SKIP_PENDING_OCR_INTERVAL_SECONDS = 0.08  # 실험 중에만 경계 시각을 더 촘촘히 관찰
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
# 실측(2026-07-28): focus_s 계열은 대상 HWND를 실제 전면으로 순간 전환하므로
# 완전 비활성 계약을 위반한다. 자동 후보와 폴백에서 영구 제외한다.
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
# ⚠️ 실측으로 영구 제외한 것:
#   "focus_s" = 대상 top-level을 실제 전면으로 순간 전환하므로 엄격 모드에서 금지.
#                수동 설정이나 과거 학습값에 남아 있어도 안전 후보 목록 밖이라 복원되지 않는다.
#   "si_s"    = 현재 전면인 다른 앱으로만 전역 S가 전달돼 대상 게임의 비활성 입력이 아니다.
# 진단: 어떤 것도 안 통하면 게임 입력이 RawInput/DirectInput 포그라운드 전용이라
# 유저모드로는 '비활성+깜빡임0+유출0'이 원리적으로 불가(로그의 창 클래스 덤프로 판별).
# focus_child_s도 내부적으로 전역 SendInput 스캔코드를 사용하므로 엄격 모드에서
# 제외한다. 자식 포커스가 성공하더라도 다른 앱 입력 누출 0을 증명할 수 없다.
# 실측 확정(사용자): 컷신 스킵을 넘기는 입력은 오직 '키보드 s' 또는 '게임패드 A' 뿐이다.
# (다른 버튼·키·pause 메뉴로는 안 넘어감.) 게다가 hold-to-skip — 전면에서 'A 홀드'로 넘어감.
# → 스윕이 훑는 축은 '다른 동작'이 아니라 [스킵 입력=s/A] × [전달 경로] × [탭 vs 홀드].
#   원래 스윕은 0.15초 '탭'만 했다 → hold-to-skip이면 탭은 원리상 못 넘김. '홀드'가 빠진 축.
# 배경 유일 희망 = s를 '메시지'로 딥 전송(게임이 메시지 계층=CEF로 스킵을 읽을 때만 통함).
#   게임패드 A는 값이 배경에 도달하나 컷신은 전면 게이팅이라 실패 예상(그래도 사실 확인용 포함).
#   si_s(SendInput 전역)·focus_s(top-level SetFocus)는 비활성 계약 위반이라 영구 제외.
# 전달 경로: char_s=WM_CHAR 딥(CEF), attach_state_s=GetKeyState 속이기, attach_post_s=큐공유+Post,
#   pm_s=WM_KEYDOWN 딥. '*_hold'=1초 홀드.
SKIP_A_SWEEP_BUTTONS = ("char_s_hold", "char_s",
                        "attach_state_s_hold", "attach_state_s",
                        "attach_post_s_hold", "attach_post_s",
                        "pm_s_hold", "pm_s",
                        "a_hold", "a")
SKIP_A_TAP_SECONDS = 0.15    # 후보 버튼 탭 길이
SKIP_A_SWEEP_HOLD_SECONDS = 1.0   # '*_hold' 후보의 홀드 길이(hold-to-skip 대응)
# 완전 비활성 실험 모드. 후보 입력 동안 별도 감시 스레드가 전면 HWND를 연속 확인하고,
# 대상 게임 HWND가 한 번이라도 전면이 된 후보만 해당 세션에서 즉시 격리합니다.
SKIP_STRICT_INACTIVE_EXPERIMENT = True
SKIP_INACTIVE_EXPERIMENT_CANDIDATES = SKIP_A_CANDIDATES
# 키보드 표시 장치에서는 화면에 [S] SKIP이 뜨며, 이 경우 실제 s 전달 경로를 먼저
# 탐색해야 합니다. A 경로도 뒤에서 사실 확인하되 우선순위를 분리합니다.
SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES = SKIP_S_CANDIDATES
SKIP_GENERIC_INACTIVE_EXPERIMENT_CANDIDATES = SKIP_GENERIC_CANDIDATES
SKIP_GENERIC_ANY_KEY_INACTIVE_EXPERIMENT_CANDIDATES = (
    SKIP_GENERIC_ANY_KEY_CANDIDATES
)
SKIP_GENERIC_ESCAPE_INACTIVE_EXPERIMENT_CANDIDATES = (
    SKIP_GENERIC_ESCAPE_CANDIDATES
)
SKIP_GENERIC_HIGHLIGHT_INACTIVE_EXPERIMENT_CANDIDATES = (
    SKIP_GENERIC_HIGHLIGHT_CANDIDATES
)
SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS = 0.20
SKIP_EXPERIMENT_MIN_FAMILY_ATTEMPTS = 3
# After this many attributable consecutive successes, pause discovery for the
# variant and run the same candidate through the 30-success confirmation gate.
# One failure immediately returns the variant to 40/40 discovery.
SKIP_EXPERIMENT_CONFIRMATION_LOCK_SUCCESSES = 3
# sham 20%를 제외한 실제 입력 에피소드를 탐색/재현 2:2로 배분합니다.
# 전체 에피소드 기준으로는 탐색 40% / 재현 40% / sham 20%입니다.
SKIP_EXPERIMENT_REAL_ALLOCATION = (
    "explore", "explore", "exploit", "exploit",
)
SKIP_EXPERIMENT_PRESERVE_FOREGROUND = True
# ── 가짜 입력 대조군 ──
# 컷신은 상대가 눌러도 양쪽 다 끝난다. 그래서 후보의 '성공'만 세면 우리 입력의 효과와
# 상대의 스킵을 구분할 수 없다(2026-07-28 데이터에서 실제로 문제가 됐다).
# 아래 후보는 대조 관찰·가드·판정 창을 똑같이 거치면서 입력만 보내지 않는다.
# 그 성공률이 곧 '우리가 안 눌렀을 때의 종료율' 실측값이며, 다른 후보와의 차이가 순수 효과다.
# AI전처럼 상대가 없는 환경에서 이 값이 0%에 가까우면 그 모드는 깨끗한 실험장이라는 뜻이다.
SKIP_SHAM_CANDIDATE = "control_noop"
SKIP_SHAM_EVERY = 5          # N번째 컷신마다 대조 에피소드(0이면 끔)

SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS = 1.50
SKIP_EXPERIMENT_CONFIRM_SUCCESSES = 30
# 상대가 먼저 누르거나 자연 종료된 화면을 우리 입력 성공으로 오인하지 않도록 후보 입력 전에
# 무입력 상태로 프롬프트가 유지되는지 관찰합니다. 실기기에서 일반 S 프롬프트가 약 2초 뒤
# 자연 종료되는 사례가 확인되어, 최종 확인은 그보다 긴 3초를 사용합니다.
SKIP_EXPERIMENT_CONTROL_SECONDS = 3.0
# The strongest highlight route was 7/7 when attempted after 3.00-3.41 seconds
# and missed only the deliberately delayed 3.73-second sample.  Production
# reacts at the earliest fully attributable boundary, so new candidate and
# sham evidence now use exactly the required three-second control.
SKIP_EXPERIMENT_HIGHLIGHT_CONTROL_OFFSETS = (0.0,)
# The strict experiment only evaluates prompts that survive the complete
# three-second no-input control.  Short progressive screens are intentionally
# disabled because they cannot satisfy the attribution goal.
SKIP_EXPERIMENT_PROGRESSIVE_CONTROL = False
SKIP_EXPERIMENT_CONTROL_RAMP_SUCCESSES = 3
# Require sustained disappearance so animation/capture flicker cannot be
# mistaken for either our success or an opponent/natural exit.
SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS = 0.40
# The post-match highlight carousel briefly hides the prompt between clips.
# Require a much longer continuous absence there so a clip transition cannot
# masquerade as leaving the highlight sequence. The response deadline still
# uses the first absent frame and remains the strict 1.5 seconds above.
SKIP_EXPERIMENT_HIGHLIGHT_EXIT_CONFIRM_SECONDS = 5.0
# A post-match summary can remain on screen indefinitely after one failed
# candidate. Wait this long from the prior input before opening a fresh 3 s
# no-input control on the same persistent prompt.
SKIP_EXPERIMENT_HIGHLIGHT_RETRY_SECONDS = 8.0
SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS = 0.005
SKIP_EXPERIMENT_LOG_FILENAME = "skip_experiments.jsonl"
SKIP_EXPERIMENT_LEARNING_FILENAME = "skip_learning.json"
# 최후수단(전면 순간전환+SendInput 키보드 s)은 '탐색을 다 돌고도' 이 시간 이상 지났을 때만.
# 0 = 완전 끄기(기본) → 비활성 100% 보장. 스킵을 못 넘겨도 컷신은 자연 종료되므로 안전.
SKIP_A_SENDINPUT_AFTER_SECONDS = 0.0
# ── (A) SKIP 최종 결론(2026-07-07 실측) ──
# 실측으로 확정: 게임패드는 배경에서 읽히나(메뉴 '패스'=A는 배경 OK), '컷신 스킵'은
# 매치 엔진이 포커스(전면)일 때만 읽는다. 사용자가 직접 확인: "A 홀드 + 알트탭으로
# FIFA 전면화해야 스킵됨". 즉 컷신 스킵은 배경으로는 원리적으로 불가.
# → 유일한 동작 방식: 게임을 잠깐 전면화 + A를 홀드 + 원래 창 복원(사용자의 알트탭 자동화).
# 화면이 잠깐 바뀌지만 스킵은 확실히 되고 큰 시간손해를 없앤다.
# 기본 False(화면 변화 0 유지, 스킵은 안 함 → 컷신 자연 종료 대기).
# 스킵을 원하면 True로: 컷신마다 ~1초 FIFA가 전면에 번쩍인다(피할 수 없음, 스킵의 대가).
# 배경 활성 스푸핑(깜빡임 0): WM_ACTIVATEAPP+WM_ACTIVATE로 '나 활성' 속인 뒤 A 홀드.
# 엔진이 스레드-로컬 활성 플래그로 게이팅하면 배경에서 스킵됨(SDL #4450류). 통하면 이걸로 끝.
# GetForegroundWindow 직접 확인 게임이면 무효 → 아래 전면화 폴백으로.
SKIP_A_ACTIVATE_SPOOF = True
SKIP_A_FOREGROUND_AFTER_SECONDS = 2.0  # 스푸핑으로 이 시간 넘게 안 사라지면 전면화 폴백 시도
                                       # (SKIP_A_FOREGROUND=True일 때만).
SKIP_A_FOREGROUND = False
SKIP_A_HOLD_SECONDS = 1.2         # A를 홀드하는 시간. 1.0초 경계 누락을 피하도록 여유를 둠.
                                  # 낮출수록 번쩍임이 짧아짐(예: 0.6). 너무 낮으면 게이지가 안 참.
SKIP_A_FG_COOLDOWN_SECONDS = 2.5  # 전면화-홀드 사이 최소 간격. (A) SKIP이 떠 있는 내내 매 사이클
                                  # 전면화하면 FIFA가 반복해서 번쩍이므로, 컷신당 사실상 1번만 하게 막음.
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
SKIP_S_MATCH_THRESHOLD = 0.80     # [S] SKIP 템플릿 상관도 — 키보드 표시 장치 전용
# 전체 템플릿은 공통 ``SKIP`` 글자만 맞아도 높은 점수가 나올 수 있다. 따라서
# 왼쪽의 실제 A/S 키 아이콘도 같은 위치에서 별도로 일치해야 종류를 확정한다.
SKIP_A_ICON_MATCH_THRESHOLD = 0.65
SKIP_S_ICON_MATCH_THRESHOLD = 0.65
# 프롬프트 분류 규칙이 바뀌면 이전 세대의 A/S 귀속 결과를 복원하지 않는다.
# (일반 ``아무 키``/``ESC`` 프롬프트가 A/S로 섞인 과거 표본을 격리.)
SKIP_PROMPT_CLASSIFIER_GENERATION = 7
SKIP_TEXT_CONSENSUS = 2           # 일반 스킵(OCR 'skip')은 이만큼 연속 감지돼야 Start(단발 노이즈 차단)
SKIP_FALLBACK_BOTH_SECONDS = 0.6  # 한 스킵이 이 시간 넘게 안 사라지면 A·Start 둘 다(템플릿 빗나가도 안 갇힘)
# ─── '아무 키나' 프롬프트 직행 ───
# "SKIP 하려면 아무 키나 누르세요. (Enter 키 제외)" 는 답이 알려진 화면이다(START 로
# 넘어감 — 7-28 실측 53/53, 사용자 확인). 그래서 OCR 이 이 문구를 읽으면 비활성 실험
# (3초 대조·후보 순환·대조군)을 거치지 않고 **바로 START** 를 누른다. 템플릿(target_G)
# 경로는 그대로 살아 있다 — winocr 가 없는 구매자 PC 의 유일한 경로라서, 이 규칙은
# 그것을 대체하지 않고 보강한다(해상도가 달라 템플릿이 빗나가는 PC 대비).
SKIP_ANYKEY_DIRECT_START = True
# 직행 START 를 적용할 프롬프트 형태(OCR 힌트). 처음엔 '아무 키나'만 넣었는데, 실측 로그가
# **▷ SKIP(escape 형)도 같은 처리가 필요함**을 보여 줬다(2026-08-22, 구매자 다수 재보고):
# 템플릿이 START 를 한 번 누른 직후 OCR 이 같은 프롬프트를 '실험 에피소드'로 가져가
# _skip_active_until 을 5.2초씩 연장하며 **템플릿의 재시도를 굶겼다** — 8초 초과 6건이 전부
# 이 모양이었다(13·15·16·16·16·27초). 이 프롬프트들의 답은 이미 START 로 확정돼 있으므로
# (7-28 실측 349펄스, 에피소드 29/30 이 0~3초 통과) 후보를 탐색할 이유가 없다.
# ⚠️ A/S(hold-to-skip)는 여기 없다 — 그쪽은 배경 입력이 원리적으로 안 되는, 답이 아직
# 없는 프롬프트라 실험(H1 attach_active_hold_a 등)이 계속 돌아야 한다.
SKIP_DIRECT_START_HINTS = ("any_key", "escape", "start")
# ⚠️ **힌트가 아예 안 나온 프롬프트(None)도 직행이다** — 이게 맨 '▷ SKIP' 이고, 실측상
# 가장 흔하다. 실전 원장(mAuto Pro skip_experiments.jsonl, 2026-08-22): generic 에피소드
# 236건 중 **104건(44%)** 이 prompt_hint="start" 인데, 그 "start" 는 OCR 이 읽은 게 아니라
# **힌트를 못 냈을 때의 폴백**이다(같은 블록에서 _skip_kind="start" 로 잠긴다). 그 104건의
# 증거 이미지를 다시 돌려 보니 OCR 원문은 "skip"(+선수 이름)뿐이고 esc/enter/아무키 토큰이
# 없어 classify 가 (True, None) 을 내며, A/S/F/G 템플릿은 40/40 전부 미매칭이었다.
# → 힌트 None 을 직행에서 빼면 '고치려던 화면'이 그대로 실험(5.2초 봉쇄)에 남는다.
SKIP_DIRECT_START_INCLUDES_UNKNOWN = True
# ⚠️ escape_highlight(경기 후 하이라이트)는 **직행에서 뺀다.** 이 레포가 '아직 답을 못 찾은'
# 형태로 기록해 둔 유일한 프롬프트다(전용 후보 카탈로그·전용 exit_confirm 5.0초·learned 강제
# None). 맨 START 가 통한다는 증거가 없으므로 실험에 남긴다. 힌트가 None 이어도 화면이
# 하이라이트로 판정되면(_is_highlight_summary_context) 직행하지 않는다.
SKIP_ANYKEY_REPRESS_SECONDS = 0.8  # 프롬프트가 계속 보이면 이 간격으로 START 를 다시 누른다

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
        # ▷ SKIP 화면(구매자 실측: START 로 넘어감). key 는 게임패드 START 다.
        # ⚠️ "esc" 로 두면 안 된다 — esc/start 는 같은 버튼(16)이지만, 이름이
        # esc 면 SKIP 실험의 ESC 보류 게이트에 걸려 엄격 모드(기본값)에서 입력이
        # 영구 억류된다. OCR 이 안 되는 PC 에선 이 화면에서 완전히 멈춘다(실사고).
        # wait 0.5초: 전환 중 무한 연타(과거 6회/초)를 절반 이하로 누르되, 펄스가
        # 한 번 안 먹었을 때 3초 안에 5~6회 재시도할 여력은 남긴다(실측 29/30 통과는
        # 연타 조건에서 나온 통계 — 재시도를 너무 줄이면 이상치 복구력이 떨어진다).
        "name": "target_F",
        "filename": "target_F.png",
        "action": "key",
        "key": "start",
        "key_mode": "sendinput",
        "key_target": "all",
        "wait_after_action": 0.5,
    },
    {
        # target_F 다음에 오는 프롬프트(같은 하단 우측 계열). target_F 와 같은 이유로
        # key 는 "start" 다 — esc 이름이면 ESC 보류 게이트에 걸려 여기서 또 멈춘다.
        # (2026-07-28 로그: 이 프롬프트 53회 전부 버튼 16 입력으로 즉시 통과 실측.)
        "name": "target_G",
        "filename": "target_G.png",
        "action": "key",
        "key": "start",
        "key_mode": "sendinput",
        "key_target": "all",
        "wait_after_action": 0.5,
    },
    {"name": "target_H", "filename": "target_H.png", "action": "click"},
    {"name": "target_I", "filename": "target_I.png", "action": "click"},
    # 경기 중 하단 전술창(팀 전술/개인 전술/경기 분석)이 떠 있으면 오른쪽 위 '−'(접기)를
    # 눌러 가린다 — 전술창이 하단 22% 를 덮어 SKIP 프롬프트 인식을 막는다(사용자 요청,
    # 수동으로도 마우스로 − 를 누른다). 템플릿 = 전술 아이콘 + '−'(중심이 − 버튼),
    # 실전 프레임(2026-08-21 1920x1080)에서 잘라 냈다.
    # ⚠️ 템플릿은 펼침·접힘 두 상태에 다 맞는다(실측 1.000/0.997) — 그레이스케일로는
    # 못 가른다. '아래 어두운 면' 트릭은 실전 프레임마다 밝기가 달라 오히려 펼침도 놓쳤다
    # (0.82). 대신 **위치**로 가른다: 펼침은 아이콘 줄이 y≈0.75, 접힘은 y≈0.97(맨 아래).
    # match_top/bottom_frac 로 펼침 밴드만 받아, 펼쳤을 때만 눌러 접고 접힌 뒤엔 다시 안
    # 누른다. 템플릿 중심 x=1608·y=804 가 곧 '−' 라 click_offset 는 0.
    {
        "name": "target_J",
        "filename": "target_J.png",
        "action": "click",
        "threshold": 0.88,
        "wait_after_action": 1.0,
        "match_top_frac": 0.55,
        "match_bottom_frac": 0.90,
    },
]

# 1차 사전필터 축소 배율. 클수록 CPU↓ (최종 클릭 정확도는 ROI 정밀매칭이라 무관).
# 2=가장 안전(권장, 8/8 탐지). 3=CPU 2.4배↓이나 얇은(16px) 템플릿 누락 위험.
DOWNSCALE_FACTOR = 2
# 1928x1048·8타겟 실측에서 2스레드와 기본 12스레드의 검색 지연은 같았습니다.
# 과도한 내부 병렬화만 막아 게임과 OCR에 CPU 여유를 남깁니다.
OPENCV_WORKER_THREADS = 2

# ─── 해상도 자동 보정 ───
# 내장 타깃 템플릿은 아래 세로 크기의 WGC 클라이언트 프레임에서 잘라낸 '픽셀 조각'입니다.
# 게임을 다른 해상도로 켜면 UI가 그 비율만큼 커져 단일 배율 matchTemplate이 전부 실패합니다.
# 그래서 캡처 프레임을 이 세로 크기로 정규화한 뒤 같은 템플릿으로 매칭합니다.
#
# 세로(높이) 기준인 이유: 게임 UI는 세로 해상도에 맞춰 커지므로 2560x1440은 1.34배가 되지만,
# 21:9(2560x1080)처럼 가로만 넓어지는 해상도에서는 UI 크기가 그대로여서 배율이 1.0이 됩니다.
# 가로 기준으로 잡으면 후자에서 멀쩡한 화면을 잘못 축소하게 됩니다.
TEMPLATE_REFERENCE_HEIGHT = 1048
# 창 맞춤(window_fit)이 목표로 삼는 WGC 프레임 크기. 템플릿을 잘라낸 그 크기입니다.
TEMPLATE_REFERENCE_WIDTH = 1928
TEMPLATE_REFERENCE_SIZE = (TEMPLATE_REFERENCE_WIDTH, TEMPLATE_REFERENCE_HEIGHT)
# 이 오차 안이면 리사이즈를 건너뜁니다(기존 1920 사용자 성능 영향 0).
CAPTURE_SCALE_DEADZONE = 0.01
# 지원 범위를 벗어난 배율은 보정하지 않습니다(오검출 상태로 클릭하는 것보다 안전).
CAPTURE_SCALE_MIN = 0.5
CAPTURE_SCALE_MAX = 2.5


def capture_normalization_scale(frame_height: int) -> float:
    """캡처 프레임 세로를 템플릿 기준 세로로 맞추는 배율을 돌려줍니다.

    1.0이면 보정하지 않습니다(같은 해상도이거나, 지원 범위를 벗어나 보정을 포기한 경우).
    정규화 프레임 크기 = 원본 크기 / 배율.
    """

    try:
        height = int(frame_height)
    except (TypeError, ValueError):
        return 1.0
    if height <= 0 or TEMPLATE_REFERENCE_HEIGHT <= 0:
        return 1.0

    scale = height / float(TEMPLATE_REFERENCE_HEIGHT)
    if abs(scale - 1.0) <= CAPTURE_SCALE_DEADZONE:
        return 1.0
    if not (CAPTURE_SCALE_MIN <= scale <= CAPTURE_SCALE_MAX):
        return 1.0
    return scale


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
    # 클릭 지점 보정(px). 템플릿 중심이 버튼이 아닐 때 쓴다.
    click_offset_x: int = 0
    click_offset_y: int = 0
    # 세로 검색 밴드(프레임 높이 비율). 같은 템플릿이 화면 두 곳(예: 전술창 펼침/접힘)에
    # 나타날 때, 원하는 위치의 매칭만 받는다. 매칭 중심 y 가 이 밴드 밖이면 무시한다.
    # 기본 (0,1) = 전체. target_J: 펼침 상태 아이콘 줄은 y≈0.75, 접힘은 y≈0.97 →
    # 밴드 (0.55,0.90) 이면 펼침만 눌러 접고 접힌 뒤엔 다시 안 누른다(실측 결함 수정).
    match_top_frac: float = 0.0
    match_bottom_frac: float = 1.0

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
        click_offset_x=int(_parse_optional_int(config.get("click_offset_x"), "click_offset_x") or 0),
        click_offset_y=int(_parse_optional_int(config.get("click_offset_y"), "click_offset_y") or 0),
        match_top_frac=float(config.get("match_top_frac", 0.0) or 0.0),
        match_bottom_frac=float(config.get("match_bottom_frac", 1.0) if config.get("match_bottom_frac") is not None else 1.0),
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
        click_offset_x=target.click_offset_x,
        click_offset_y=target.click_offset_y,
        match_top_frac=target.match_top_frac,
        match_bottom_frac=target.match_bottom_frac,
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

        # 화면 캡처와 동일한 BGR→gray 변환식을 유지해야 템플릿 점수가 달라지지 않습니다.
        target.image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        # 첫 인식 프레임에서 각 템플릿을 따로 축소하지 않도록 시작 시 한 번만 준비합니다.
        height, width = target.image_gray.shape[:2]
        small_width = width // DOWNSCALE_FACTOR
        small_height = height // DOWNSCALE_FACTOR
        target._small_gray = (
            cv2.resize(target.image_gray, (small_width, small_height), interpolation=cv2.INTER_AREA)
            if small_width >= 4 and small_height >= 4
            else None
        )
        source = "커스텀 캡처" if is_custom else "기본"
        log(
            f"[이미지 로드] {target.filename} ({source}), "
            f"크기={target.image_gray.shape[1]}x{target.image_gray.shape[0]}, "
            f"임계값={target.threshold:.2f}, action={target.action}"
        )

    return targets

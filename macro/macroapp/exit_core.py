"""순수 로직 — Windows 의존성 없이 단위 검증 가능한 부분만 모은다.

winproc.py(ctypes/Win32)와 gui.py(tkinter)에서 이 모듈을 가져다 쓴다.
여기에는 import 시점에 실패할 수 있는 의존성을 절대 두지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


# ---------------------------------------------------------------------------
# 프로세스 이름 정규화 / 매칭
# ---------------------------------------------------------------------------

def normalize_name(text: str) -> str:
    """사용자 입력을 비교용 표준형으로 바꾼다.

    - 앞뒤 공백/따옴표 제거
    - 경로가 섞여 들어오면 파일명만 사용 (C:\\...\\notepad.exe → notepad.exe)
    - 소문자화
    - 확장자 .exe 는 붙이지 않는다(있는 그대로 두고 matches()에서 양쪽 다 본다).
      System / Registry 처럼 확장자가 없는 진짜 프로세스가 있기 때문.
    """
    if not text:
        return ""
    cleaned = text.strip().strip('"').strip("'").strip()
    # 경로 구분자가 있으면 마지막 조각만 취한다.
    for sep in ("\\", "/"):
        if sep in cleaned:
            cleaned = cleaned.rsplit(sep, 1)[-1]
    return cleaned.strip().lower()


def _name_variants(normalized: str) -> tuple[str, ...]:
    """'notepad' 와 'notepad.exe' 를 서로 같은 것으로 보기 위한 후보들."""
    if not normalized:
        return ()
    if normalized.endswith(".exe"):
        return (normalized, normalized[: -len(".exe")])
    return (normalized, normalized + ".exe")


def matches(query: str, exe_name: str) -> bool:
    """사용자가 입력한 이름(query)이 실제 프로세스 이름(exe_name)과 같은가.

    대소문자 무시, .exe 유무 무시. 부분 일치는 하지 않는다 —
    'ex' 가 explorer.exe 를 잡아버리면 사고로 이어진다.
    """
    q = normalize_name(query)
    e = normalize_name(exe_name)
    if not q or not e:
        return False
    return e in _name_variants(q)


# ---------------------------------------------------------------------------
# 위험 프로세스 판정
# ---------------------------------------------------------------------------

#: 일시정지하면 Windows 가 멈추거나 강제 재부팅되는 프로세스. 무조건 거부한다.
FATAL_NAMES: frozenset[str] = frozenset(
    {
        "system",
        "system idle process",
        "secure system",
        "registry",
        "memory compression",
        "ntoskrnl.exe",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
        "fontdrvhost.exe",
        # 서비스 호스트는 이름 하나에 수십 개 프로세스가 딸려 있어
        # 전부 멈추면 사실상 시스템 정지와 같다.
        "svchost.exe",
    }
)

#: 멈춰도 복구되지만 화면/소리/작업표시줄이 얼어붙어 사용자가 놀라는 프로세스.
#: 확인 후 진행한다.
RISKY_NAMES: frozenset[str] = frozenset(
    {
        "dwm.exe",  # 데스크톱 합성 — 화면 갱신이 통째로 멈춘다
        "explorer.exe",  # 작업표시줄/바탕화면
        "audiodg.exe",  # 오디오 그래프
        "taskmgr.exe",  # 작업관리자 자신
        "perfmon.exe",  # 리소스 모니터 호스트
        "msmpeng.exe",  # Defender — 멈추면 실시간 검사가 밀린다
    }
)

VERDICT_OK = "ok"
VERDICT_RISKY = "risky"
VERDICT_FATAL = "fatal"
VERDICT_EMPTY = "empty"


@dataclass(frozen=True)
class NameVerdict:
    """프로세스 이름 입력에 대한 판정."""

    verdict: str
    message: str

    @property
    def blocked(self) -> bool:
        return self.verdict in (VERDICT_FATAL, VERDICT_EMPTY)

    @property
    def needs_confirm(self) -> bool:
        return self.verdict == VERDICT_RISKY


def classify_name(text: str) -> NameVerdict:
    """사용자가 입력한 프로세스 이름이 안전한지 판정한다."""
    normalized = normalize_name(text)
    if not normalized:
        return NameVerdict(VERDICT_EMPTY, "프로세스 이름을 입력하세요.")

    # .exe 유무와 무관하게 걸러낸다.
    for candidate in _name_variants(normalized):
        if candidate in FATAL_NAMES:
            return NameVerdict(
                VERDICT_FATAL,
                f"'{normalized}' 은(는) Windows 핵심 프로세스입니다. "
                "일시정지하면 시스템이 멈추므로 차단했습니다.",
            )
    for candidate in _name_variants(normalized):
        if candidate in RISKY_NAMES:
            return NameVerdict(
                VERDICT_RISKY,
                f"'{normalized}' 을(를) 멈추면 화면이나 작업표시줄이 "
                "잠시 얼어붙을 수 있습니다. 그래도 진행할까요?",
            )
    return NameVerdict(VERDICT_OK, "")


# ---------------------------------------------------------------------------
# 유지 시간 입력 파싱
# ---------------------------------------------------------------------------

MIN_HOLD_SECONDS = 0.1
MAX_HOLD_SECONDS = 600.0

# [0-9] 를 명시한다. \d 는 전각 숫자('１０')까지 매치하고 float() 도 그것을 받아들여,
# 사용자가 IME 로 친 전각 입력이 조용히 통과한다. 입력은 ASCII 숫자로 못 박는다.
_NUMBER_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(?:초|s|sec)?\s*$", re.IGNORECASE)


def parse_hold_seconds(text: str, default: float = 10.0) -> tuple[float | None, str]:
    """'10', '10초', '2.5' 같은 입력을 초 단위 실수로 바꾼다.

    반환: (초, 오류 메시지). 실패하면 (None, 사유).
    """
    if text is None:
        return default, ""
    raw = str(text).strip()
    if not raw:
        return default, ""
    m = _NUMBER_RE.match(raw)
    if not m:
        return None, "유지 시간은 숫자로 입력하세요 (예: 10)."
    value = float(m.group(1))
    if value < MIN_HOLD_SECONDS:
        return None, f"유지 시간은 {MIN_HOLD_SECONDS:g}초 이상이어야 합니다."
    if value > MAX_HOLD_SECONDS:
        return None, f"유지 시간은 {MAX_HOLD_SECONDS:g}초 이하여야 합니다."
    return value, ""


def format_remaining(seconds: float) -> str:
    """남은 시간을 UI 문구로. 음수는 0으로 클램프."""
    remaining = max(0.0, seconds)
    return f"{remaining:.1f}초"


# ---------------------------------------------------------------------------
# 1회성 실행 상태 머신
# ---------------------------------------------------------------------------

STATE_IDLE = "idle"
STATE_SUSPENDING = "suspending"
STATE_HELD = "held"
STATE_RESUMING = "resuming"
#: 마무리 단계 — 유지 구간이 끝난 뒤 화면을 보며 처리하는 구간.
STATE_FOLLOWUP = "followup"
STATE_DONE = "done"
STATE_FAILED = "failed"

#: 이 상태에서는 버튼을 다시 눌러도 무시한다(1회성 보장).
BUSY_STATES: frozenset[str] = frozenset(
    {STATE_SUSPENDING, STATE_HELD, STATE_RESUMING, STATE_FOLLOWUP}
)


@dataclass
class RunPlan:
    """버튼 한 번에 대응하는 1회성 실행 계획."""

    query: str
    hold_seconds: float
    pids: tuple[int, ...] = ()
    state: str = STATE_IDLE
    error: str = ""
    log: list[str] = field(default_factory=list)

    @property
    def busy(self) -> bool:
        return self.state in BUSY_STATES


def can_start(state: str) -> bool:
    """현재 상태에서 새 실행을 시작해도 되는가."""
    return state not in BUSY_STATES


def run_progress(phase: str, elapsed: float, total: float) -> float:
    """진행바에 넣을 값(0.0~1.0)을 계산한다.

    phase 별 구간을 나눠 두는 이유: 유지 구간은 길이를 알지만 마무리 구간은
    상대(게임 화면)에 달려 있어 정확한 길이를 모른다. 그래서 유지 구간이
    앞쪽 대부분을 차지하고, 마무리 구간은 남은 폭을 천천히 채우다가 끝나면
    100%가 된다. 진행바가 뒤로 가는 일이 없도록 구간은 항상 단조 증가한다.

    phase: prepare | hold | followup | done
    """

    hold_share = 0.70          # 유지 구간이 차지하는 폭
    followup_share = 0.28      # 마무리 구간이 차지하는 폭(남은 2%는 완료용)

    if phase == "done":
        return 1.0
    if phase == "prepare":
        return 0.02

    span = max(1e-6, float(total))
    ratio = max(0.0, min(1.0, float(elapsed) / span))
    if phase == "hold":
        return 0.02 + (hold_share - 0.02) * ratio
    if phase == "followup":
        return hold_share + followup_share * ratio
    return 0.0


def press_methods(methods: Sequence[str], companion_running: bool) -> tuple[str, ...]:
    """상황에 맞는 시도 방법 목록.

    감독모드 도구가 같이 돌고 있으면 패드 방법을 뺀다. 가상 패드는 **만든
    프로세스만** 쓸 수 있어서 그쪽 것을 빌려 쓸 방법이 없고, 여기서 또 만들면
    컨트롤러가 두 개 꽂혀 게임이 어느 쪽을 1번으로 볼지 달라진다. 어차피 그
    상황에서는 그쪽이 같은 화면을 같은 방식으로 처리한다.

    전부 빠지면 좌표 방법 하나는 남긴다(시도할 게 없으면 아무 일도 안 일어난다).
    """

    if not companion_running:
        return tuple(methods)
    remaining = tuple(method for method in methods if method != "pad")
    return remaining or ("click",)


@dataclass
class Decision:
    """다음에 무엇을 할지. action: press | open | wait | done | timeout | exhausted."""

    action: str
    method: str = ""


class ConfirmSequence:
    """찾는 그림이 사라질 때까지의 판정을 담당하는 순수 상태 머신.

    화면 캡처도 입력도 여기서 하지 않는다 — '지금 보이나(found)'와 시각(now)만
    받아 다음 행동을 정한다. 그래서 Windows 없이도 전부 단위 검증할 수 있다.

    규칙:
      * 그림이 한 번도 안 보였으면 open_delay_seconds 뒤에 '열기(open)'를 지시한다
        — 가만히 있으면 그림이 뜨지 않는 화면에서, 그림을 띄우는 입력을 넣는 단계.
        그래도 안 뜨면 open_retry_seconds 간격으로 max_opens 번까지 반복한다.
        **한 번이라도 보였으면 다시 열지 않는다** — 여는 입력이 이미 열린 화면을
        도로 닫을 수 있다(토글). timeout 시각이 지난 뒤에도 열지 않는다(조용히
        끝나야 할 실행이 화면을 열어 두면 안 된다). max_opens=0 이면 이 단계 자체가 없다.
      * 보이면 누른다 — 단, **첫 발견 후 first_press_delay_seconds 만큼 기다렸다가**
        누른다(그림이 뜬 직후는 화면이 아직 자리 잡는 중이라 입력이 안 먹거나
        엉뚱하게 들어갈 수 있다 — 사용자 실측 요구). 대기 중 깜빡여도 기준은
        첫 발견 시각이다. 누른 뒤 verify_seconds 동안은 다시 누르지 않고 지켜본다
        (같은 화면에 입력이 연달아 들어가면 다음 화면까지 눌러 버린다).
      * 여전히 보이면 **다음 방법**으로 바꿔 가며 누른다(methods 순환).
      * 사라지고 settle_seconds 가 지나면 성공. 한 프레임 깜빡임을 성공으로
        오인하지 않기 위해 곧바로 끝내지 않는다.
      * 한 번도 못 봤는데 timeout_seconds 가 지나면 timeout(조용히 종료).
      * max_presses 를 다 썼는데 **마지막 누름의 verify 창이 끝난 뒤에도** 계속
        보이면 exhausted(실패 보고). 창이 끝나기 전에는 판정을 미룬다 — 마지막
        시도가 성공했는데 화면 반응이 한 박자 늦었을 수 있다.
    """

    def __init__(
        self,
        started_at: float,
        *,
        methods: Sequence[str],
        verify_seconds: float = 1.2,
        settle_seconds: float = 0.6,
        timeout_seconds: float = 40.0,
        max_presses: int = 8,
        open_delay_seconds: float = 0.0,
        open_retry_seconds: float = 0.0,
        max_opens: int = 0,
        first_press_delay_seconds: float = 0.0,
    ) -> None:
        if not methods:
            raise ValueError("methods 가 비어 있습니다.")
        self.started_at = float(started_at)
        self.methods = tuple(methods)
        self.verify_seconds = float(verify_seconds)
        self.settle_seconds = float(settle_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.max_presses = int(max_presses)
        self.open_retry_seconds = float(open_retry_seconds)
        self.max_opens = int(max_opens)
        self.first_press_delay_seconds = float(first_press_delay_seconds)

        self.presses = 0
        self.opens = 0
        self.seen = False
        self.last_seen_at: float | None = None
        self._next_press_at = float("-inf")
        self._next_open_at = self.started_at + float(open_delay_seconds)

    def decide(self, now: float, found: bool) -> Decision:
        now = float(now)
        if found:
            if not self.seen:
                # 첫 발견 — 바로 누르지 않고 대기 시각을 잡는다. 대기 중 그림이
                # 깜빡여도 이 기준은 다시 잡지 않는다(첫 발견 시각 고정).
                self._next_press_at = now + self.first_press_delay_seconds
            self.seen = True
            self.last_seen_at = now
            if self.presses >= self.max_presses:
                # 마지막 누름의 verify 창이 끝나기 전에는 포기를 확정하지 않는다.
                # 게임 반응(누름→소멸)이 루프 틱보다 느려서, 여기서 바로 포기하면
                # 마지막 시도가 성공해도 '아직 보임' 한 프레임 때문에 실패가 된다.
                if now >= self._next_press_at:
                    return Decision("exhausted")
                return Decision("wait")
            if now >= self._next_press_at:
                method = self.methods[self.presses % len(self.methods)]
                self.presses += 1
                self._next_press_at = now + self.verify_seconds
                return Decision("press", method)
            return Decision("wait")

        if self.presses and self.last_seen_at is not None:
            if now - self.last_seen_at >= self.settle_seconds:
                return Decision("done")
            return Decision("wait")

        # timeout 을 열기보다 먼저 본다 — 시한이 지난 뒤에 화면을 여는 입력이
        # 나가면, 조용히 끝나야 할 실행이 게임 화면을 연 채로 방치한다.
        if now - self.started_at >= self.timeout_seconds:
            return Decision("timeout")

        if not self.seen and self.opens < self.max_opens and now >= self._next_open_at:
            self.opens += 1
            self._next_open_at = now + self.open_retry_seconds
            return Decision("open")

        return Decision("wait")


def select_targets(
    query: str,
    processes: Iterable[tuple[int, str]],
    exclude_pids: Sequence[int] = (),
) -> list[int]:
    """이름이 일치하는 PID 목록을 고른다.

    processes: (pid, exe_name) 나열. exclude_pids: 자기 자신 등 제외할 PID.
    같은 이름의 프로세스가 여러 개면 전부 대상으로 삼는다 —
    하나만 멈추면 나머지가 계속 돌아 '멈춘 것 같지 않은' 상태가 되기 때문.
    """
    excluded = set(exclude_pids)
    found: list[int] = []
    for pid, name in processes:
        if pid in excluded:
            continue
        if matches(query, name):
            found.append(pid)
    return sorted(found)

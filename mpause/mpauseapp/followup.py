"""유지 시간이 끝난 직후 자동으로 도는 마무리 단계.

사용자가 따로 시작 버튼을 누를 필요가 없다. 유지 구간이 끝나는 순간 곧바로
화면을 보기 시작한다. 이 화면은 가만히 두면 기다리는 그림을 보여 주지 않는다 —
여는 입력(패드)을 넣어 그림을 띄우고, 뜨는 즉시 누른다.

구조를 셋으로 나눈 이유:

  screen.py  — 창을 찾고 캡처하고 그림 위치를 찾는다 (Windows 필요)
  press.py   — 찾은 자리를 누른다 (Windows 필요)
  core.py    — '언제 누르고 언제 끝낼지' 판정만 한다 (순수 로직, 어디서나 검증 가능)

이 파일은 셋을 잇는 얇은 층이다. 판정 규칙을 여기에 두지 않는 이유는,
그 부분이 정확히 버그가 숨는 자리인데 Windows 가 있어야만 돌면 검증할 수
없기 때문이다(mAuto 에서 배운 방식).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from mpauseapp import core, deps, press, screen, winproc
from mpauseapp.config import (
    COMPANION_PROCESS_NAME,
    FOLLOWUP_ENABLED,
    FOLLOWUP_FIRST_PRESS_DELAY_SECONDS,
    FOLLOWUP_LOOP_SLEEP_SECONDS,
    FOLLOWUP_MATCH_THRESHOLD,
    FOLLOWUP_MAX_OPENS,
    FOLLOWUP_MAX_PRESSES,
    FOLLOWUP_METHODS,
    FOLLOWUP_OPEN_BUTTON,
    FOLLOWUP_OPEN_DELAY_SECONDS,
    FOLLOWUP_OPEN_RETRY_SECONDS,
    FOLLOWUP_PAD_BUTTON,
    FOLLOWUP_PROGRESS_SPAN_SECONDS,
    FOLLOWUP_SETTLE_SECONDS,
    FOLLOWUP_TIMEOUT_SECONDS,
    FOLLOWUP_VERIFY_SECONDS,
    FOLLOWUP_WINDOW_WAIT_SECONDS,
)

# 결과 코드 — 러너가 이걸 보고 사용자 문구를 고른다.
RESULT_OK = "ok"              # 눌렀고 사라졌다
RESULT_QUIET = "quiet"        # 열기 없이 지켜봤는데 끝까지 안 떴다(문제로 보지 않는다)
RESULT_NO_SHOW = "no_show"    # 열기까지 했는데 끝내 안 떴다 — 열기 실패 의심, 화면 확인 안내
RESULT_FAILED = "failed"      # 계속 떠 있어서 포기했다
RESULT_NO_WINDOW = "no_window"
RESULT_SKIPPED = "skipped"    # 의존성이 없어 아예 못 한다
RESULT_CANCELLED = "cancelled"

Ticker = Callable[[float], None]


@dataclass(frozen=True)
class Plan:
    """준비 단계가 알아낸 것 — 어느 창을 볼지, 어떤 방법을 쓸지."""

    hwnd: Optional[int] = None
    methods: tuple[str, ...] = field(default=tuple(FOLLOWUP_METHODS))


def companion_running() -> bool:
    """감독모드 도구가 같이 돌고 있는가. 확인에 실패하면 False."""

    try:
        processes = winproc.list_processes()
    except Exception:
        return False
    return bool(
        core.select_targets(
            COMPANION_PROCESS_NAME,
            processes,
            exclude_pids=[winproc.current_pid()],
        )
    )


def prepare(cancel: Optional[threading.Event] = None) -> Optional[Plan]:
    """본 동작이 시작되기 전에 미리 해 둘 준비 — 볼 창과 쓸 방법을 정한다.

    가상 패드는 여기서 만들지 않는다. 만드는 순간 장치 목록에 컨트롤러가
    나타나는데, 대부분의 실행은 첫 번째 방법(좌표)만으로 끝나서 패드를 쓰지도
    않는다. 정말 필요해지는 순간(press.pad_press)에 만든다.

    실패해도 예외를 내지 않는다. 이 단계는 전부 '있으면 좋은 것'이고,
    본 동작은 이것과 무관하게 끝나야 한다.
    """

    if not FOLLOWUP_ENABLED:
        return None
    if cancel is not None and cancel.is_set():
        return None
    try:
        hwnd = screen.find_window()
    except Exception:
        hwnd = None
    try:
        methods = core.press_methods(FOLLOWUP_METHODS, companion_running())
    except Exception:
        methods = tuple(FOLLOWUP_METHODS)
    return Plan(hwnd=hwnd, methods=methods)


def run(
    cancel: threading.Event,
    *,
    hwnd: Optional[int] = None,
    methods: Optional[tuple[str, ...]] = None,
    on_tick: Optional[Ticker] = None,
) -> str:
    """여는 입력으로 그림을 띄우고, 뜨면 누른다. 결과 코드를 돌려준다."""

    if not FOLLOWUP_ENABLED:
        return RESULT_SKIPPED
    if not deps.vision_ready():
        return RESULT_SKIPPED
    if not methods:
        methods = tuple(FOLLOWUP_METHODS)

    template = screen.load_template(FOLLOWUP_MATCH_THRESHOLD)
    if template is None:
        return RESULT_SKIPPED

    if not screen.window_alive(hwnd):
        hwnd = screen.wait_for_window(FOLLOWUP_WINDOW_WAIT_SECONDS, cancel=cancel)
    if cancel.is_set():
        return RESULT_CANCELLED
    if not hwnd:
        return RESULT_NO_WINDOW

    watcher = screen.Watcher(hwnd)
    if not watcher.start():
        return RESULT_SKIPPED

    # 열기 입력은 패드로만 낼 수 있다. 계획에서 패드 방법이 빠졌다는 건 감독모드
    # 도구가 같이 돈다는 뜻이고(core.press_methods), 그때 여기서 패드를 만들면
    # 컨트롤러가 두 개 꽂힌다 — 그 상황에서는 열기 단계 자체를 끈다.
    max_opens = FOLLOWUP_MAX_OPENS if "pad" in methods else 0

    # 마지막으로 '보였다/안 보였다'를 기억한다. 새 프레임이 없으면(=화면이 그대로)
    # 이 값을 그대로 쓴다. 새 프레임이 없는 것을 '안 보임'으로 처리하면,
    # 그림이 떠 있는데도 사라진 줄 알고 성공으로 끝내 버린다.
    last_found = False
    last_center: Optional[tuple[int, int]] = None
    last_client: Optional[tuple[int, int]] = None

    # 패드 생성부터는 반드시 try 안에서 한다 — 여기서 무엇이 터져도 finally 의
    # release_pad() 가 도달해야 컨트롤러가 장치 목록에 남지 않는다.
    try:
        if max_opens > 0:
            # 패드를 지금 미리 꽂아 둔다. 첫 열기 입력까지의 지연 동안 게임이 새
            # 컨트롤러를 장치로 잡을 시간이 생긴다(만들자마자 누르면 유실될 수 있다).
            press.ensure_pad()

        started = time.monotonic()
        sequence = core.ConfirmSequence(
            started,
            methods=methods,
            verify_seconds=FOLLOWUP_VERIFY_SECONDS,
            settle_seconds=FOLLOWUP_SETTLE_SECONDS,
            timeout_seconds=FOLLOWUP_TIMEOUT_SECONDS,
            max_presses=FOLLOWUP_MAX_PRESSES,
            open_delay_seconds=FOLLOWUP_OPEN_DELAY_SECONDS,
            open_retry_seconds=FOLLOWUP_OPEN_RETRY_SECONDS,
            max_opens=max_opens,
            first_press_delay_seconds=FOLLOWUP_FIRST_PRESS_DELAY_SECONDS,
        )

        while True:
            if cancel.is_set():
                return RESULT_CANCELLED
            if not watcher.alive():
                return RESULT_NO_WINDOW

            frame = watcher.frame(timeout=0.1)
            if frame is not None:
                center, _score = screen.locate(frame, template)
                last_found = center is not None
                if center is not None:
                    last_center = center

            now = time.monotonic()
            if on_tick is not None:
                on_tick(now - started)

            decision = sequence.decide(now, last_found)
            if decision.action == "press":
                last_client = _press(
                    decision.method,
                    watcher=watcher,
                    template=template,
                    center=last_center,
                    last_client=last_client,
                )
            elif decision.action == "open":
                # 그림을 띄우는 입력. 게임은 활성일 때만 패드를 읽으므로 mAuto 와
                # 똑같이 가짜 포커스를 먼저 보낸다. 재시도 판정은 전부 core 몫.
                press.fake_focus(watcher.hwnd)
                press.pad_press(FOLLOWUP_OPEN_BUTTON)
            elif decision.action == "done":
                return RESULT_OK
            elif decision.action == "timeout":
                # 같은 '끝내 안 떴다'라도 의미가 다르다. 열기를 한 번도 안 했다면
                # 지켜만 본 것이라 무해하지만(quiet), 열기까지 했는데 안 떴다면
                # 열기 실패(버튼 오매핑·장치 미인식·패드 런타임 부재)일 가능성이
                # 높다 — '완료'로 보고하면 실패가 영영 관측되지 않는다.
                return RESULT_NO_SHOW if sequence.opens else RESULT_QUIET
            elif decision.action == "exhausted":
                return RESULT_FAILED
            elif FOLLOWUP_LOOP_SLEEP_SECONDS > 0 and frame is None:
                # 새 프레임이 없었으면 조금 쉰다(정지 화면에서 CPU 를 태우지 않는다).
                cancel.wait(FOLLOWUP_LOOP_SLEEP_SECONDS)
    except Exception:
        # 여기서 무슨 일이 나든 본 동작은 이미 끝난 뒤다. 조용히 접는다.
        return RESULT_FAILED
    finally:
        watcher.stop()
        # 패드는 여기서 뽑지 않는다 — mAuto 와 동일하게 앱 수명 동안 유지한다.
        # 매 실행 꽂았다 뽑으면 게임이 장치를 매번 다시 잡아야 하고(첫 입력 유실),
        # '컨트롤러 분리' 반응을 일으킬 수 있다. 뽑는 곳은 gui._on_close 한 곳이다.


def after_resume(cancel: threading.Event, prepared: object, on_tick) -> str:
    """PauseRunner 가 재개 직후 부르는 진입점(러너가 기대하는 시그니처).

    러너는 마무리 단계가 무엇을 하는지 몰라야 하므로, 진행률 변환도 여기서 한다.
    """

    plan = prepared if isinstance(prepared, Plan) else Plan()

    def tick(elapsed: float) -> None:
        if on_tick is not None:
            on_tick(
                core.run_progress("followup", elapsed, FOLLOWUP_PROGRESS_SPAN_SECONDS)
            )

    return run(cancel, hwnd=plan.hwnd, methods=plan.methods, on_tick=tick)


def _press(
    method: str,
    *,
    watcher: "screen.Watcher",
    template: "screen.Template",
    center: Optional[tuple[int, int]],
    last_client: Optional[tuple[int, int]],
) -> Optional[tuple[int, int]]:
    """한 번 누른다. 돌려주는 값은 다음 곡선 이동의 출발점(클라이언트 좌표)."""

    if method == "pad":
        # 열기와 같은 이유 — 배경 창은 가짜 포커스 없이는 패드 입력을 무시한다.
        press.fake_focus(watcher.hwnd)
        press.pad_press(FOLLOWUP_PAD_BUTTON)
        return last_client

    if center is None:
        return last_client

    size = template.gray.shape[:2] if template.gray is not None else (1, 1)
    point = press.jitter(center[0], center[1], size)
    client = watcher.to_client(point[0], point[1])
    if client is None:
        return last_client

    start = last_client
    if start is None:
        # 첫 이동은 창 한가운데에서 출발한다.
        client_size = watcher.client_size()
        start = (
            (client_size[0] // 2, client_size[1] // 2) if client_size else client
        )

    if press.curved_click(watcher.hwnd, start, client):
        return client
    return last_client

"""즉시 종료의 마무리 단계 — 되살린 직후 화면을 보고 정리한다.

형제 프로젝트(../mpause)의 같은 단계를 이 앱으로 옮긴 것이다. 다만 화면 인식과
입력은 **이 앱의 원본을 그대로 쓴다** — 캡처는 InactiveManager, 매칭은
find_template_center, 좌표 클릭은 post_curved_click, 패드는 input_gamepad.
사본을 따로 두지 않으므로 자동화 쪽을 고치면 여기도 같이 좋아진다.

판정 규칙(언제 열고, 언제 누르고, 언제 끝낼지)은 exit_core.ConfirmSequence 에
있다. 그쪽은 Windows 없이 전부 단위 검증되는 순수 로직이다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from macroapp import exit_core, input_gamepad, winapi
from macroapp.config import (
    EXIT_LOOP_SLEEP_SECONDS,
    EXIT_MATCH_THRESHOLD,
    EXIT_FIRST_PRESS_DELAY_SECONDS,
    EXIT_MAX_OPENS,
    EXIT_MAX_PRESSES,
    EXIT_METHODS,
    EXIT_OPEN_BUTTON,
    EXIT_OPEN_DELAY_SECONDS,
    EXIT_OPEN_RETRY_SECONDS,
    EXIT_PAD_BUTTON,
    EXIT_SETTLE_SECONDS,
    EXIT_TARGET_NAME,
    EXIT_TIMEOUT_SECONDS,
    EXIT_VERIFY_SECONDS,
    EXIT_WINDOW_WAIT_SECONDS,
    WINDOW_TITLE,
    CLICK_JITTER_PIXELS,
)
from macroapp.logging_util import LogCallback
from macroapp.matching import find_template_center
from macroapp.window import InactiveManager

# 결과 코드 — 러너가 이걸 보고 사용자 문구를 고른다(exit_runner._FOLLOWUP_MESSAGES).
RESULT_OK = "ok"
RESULT_QUIET = "quiet"
#: 열기 입력까지 했는데 끝내 그림이 안 떴다 — 열기가 안 먹은 것으로 보고
#: 사용자에게 화면을 확인하라고 알린다(그냥 조용히 끝내면 안 된다).
RESULT_NO_SHOW = "no_show"
RESULT_FAILED = "failed"
RESULT_NO_WINDOW = "no_window"
RESULT_SKIPPED = "skipped"
RESULT_CANCELLED = "cancelled"

Ticker = Callable[[float], None]


@dataclass(frozen=True)
class Plan:
    """준비 단계가 알아낸 것 — 무엇을 보고, 어떤 방법을 쓸지."""

    manager: Optional[InactiveManager] = None
    methods: tuple[str, ...] = field(default=tuple(EXIT_METHODS))


def _silent(_message: str) -> None:
    """기본 로거 — 아무 데도 남기지 않는다."""


def prepare(
    cancel: Optional[threading.Event] = None,
    *,
    logger: Optional[LogCallback] = None,
    manager: Optional[InactiveManager] = None,
) -> Optional[Plan]:
    """정지 **전에** 창을 찾아 둔다.

    정지한 뒤에 하면 그 시간이 그대로 정지 시간에 더해지고, 그 구간에서는 취소도
    듣지 못한다. 실패해도 예외를 내지 않는다 — 준비는 '있으면 좋은 것'이다.

    이미 자동화가 돌고 있으면 그쪽 InactiveManager 를 그대로 받아 쓴다(창을 두 번
    찾지 않고, WGC 세션도 하나만 유지된다).
    """

    log = logger or _silent
    if cancel is not None and cancel.is_set():
        return None
    try:
        if manager is None:
            manager = InactiveManager(WINDOW_TITLE, logger=log)
            if not manager.find_window():
                manager = None
    except Exception:
        manager = None
    return Plan(manager=manager, methods=tuple(EXIT_METHODS))


def run(
    cancel: threading.Event,
    *,
    manager: Optional[InactiveManager] = None,
    methods: Optional[tuple[str, ...]] = None,
    on_tick: Optional[Ticker] = None,
    logger: Optional[LogCallback] = None,
    targets=None,
) -> str:
    """여는 입력으로 화면을 띄우고, 찾는 그림이 뜨면 누른다."""

    log = logger or _silent
    methods = tuple(methods or EXIT_METHODS)

    target = _load_target(targets, log)
    if target is None:
        log("[종료] 대상 이미지를 준비하지 못해 마무리 단계를 건너뜁니다.")
        return RESULT_SKIPPED

    owns_manager = manager is None
    if manager is None:
        manager = InactiveManager(WINDOW_TITLE, logger=log)
        deadline = time.monotonic() + EXIT_WINDOW_WAIT_SECONDS
        while not manager.find_window():
            if cancel.is_set():
                return RESULT_CANCELLED
            if time.monotonic() >= deadline:
                return RESULT_NO_WINDOW
            cancel.wait(0.25)

    # 열기 입력은 패드로만 낼 수 있다. 패드를 쓸 수 없으면 그 단계를 아예 끈다.
    max_opens = EXIT_MAX_OPENS if ("pad" in methods and winapi.vg is not None) else 0

    started = time.monotonic()
    sequence = exit_core.ConfirmSequence(
        started,
        methods=methods,
        verify_seconds=EXIT_VERIFY_SECONDS,
        settle_seconds=EXIT_SETTLE_SECONDS,
        timeout_seconds=EXIT_TIMEOUT_SECONDS,
        max_presses=EXIT_MAX_PRESSES,
        open_delay_seconds=EXIT_OPEN_DELAY_SECONDS,
        open_retry_seconds=EXIT_OPEN_RETRY_SECONDS,
        max_opens=max_opens,
        # ⚠️ 이 인자를 빠뜨리면 기본값 0 이라 그림을 보자마자 누른다. mPause 에는
        # 있었는데 이 앱으로 옮길 때 누락됐던 배선이다(실측으로 드러남).
        first_press_delay_seconds=EXIT_FIRST_PRESS_DELAY_SECONDS,
    )

    # 새 프레임이 없으면(=화면이 그대로) 직전 판정을 유지한다. '안 보임'으로
    # 처리하면 그림이 떠 있는데도 사라진 줄 알고 성공으로 끝낸다.
    last_found = False
    last_center: Optional[tuple[int, int]] = None

    try:
        while True:
            if cancel.is_set():
                return RESULT_CANCELLED

            frame = manager.capture_client_area()
            if frame is not None:
                center, _score = find_template_center(frame, target, log)
                last_found = center is not None
                if center is not None:
                    last_center = center

            now = time.monotonic()
            if on_tick is not None:
                on_tick(now - started)

            decision = sequence.decide(now, last_found)
            if decision.action == "press":
                _press(decision.method, manager, target, last_center, log)
            elif decision.action == "open":
                log("[종료] 화면을 여는 입력을 보냅니다.")
                _pad(EXIT_OPEN_BUTTON, log)
            elif decision.action == "done":
                return RESULT_OK
            elif decision.action == "timeout":
                # 열기까지 눌렀는데 안 떴으면 조용히 넘기지 않는다(열기 실패 의심).
                return RESULT_NO_SHOW if sequence.opens else RESULT_QUIET
            elif decision.action == "exhausted":
                return RESULT_FAILED
            elif frame is None and EXIT_LOOP_SLEEP_SECONDS > 0:
                cancel.wait(EXIT_LOOP_SLEEP_SECONDS)
    except Exception as exc:  # noqa: BLE001 - 본 동작은 이미 끝난 뒤다
        log(f"[종료] 마무리 단계에서 문제가 발생했습니다: {exc}")
        return RESULT_FAILED
    finally:
        # 우리가 만든 캡처 세션만 정리한다. 자동화가 쓰던 것을 빌려 왔으면
        # 여기서 끄면 안 된다(자동화의 눈을 감겨 버린다).
        if owns_manager:
            try:
                manager.stop_capture()
            except Exception:
                pass


def after_resume(cancel: threading.Event, prepared: object, on_tick) -> str:
    """exit_runner 가 재개 직후 부르는 진입점(러너가 기대하는 시그니처)."""

    plan = prepared if isinstance(prepared, Plan) else Plan()

    def tick(elapsed: float) -> None:
        if on_tick is not None:
            on_tick(elapsed)

    return run(cancel, manager=plan.manager, methods=plan.methods, on_tick=tick)


# ─── 내부 ──────────────────────────────────────────────────────────────────


def _load_target(targets, log: LogCallback):
    """자동화가 쓰는 것과 같은 타겟 정의에서 찾을 그림 하나를 고른다."""

    if targets:
        for item in targets:
            if item.name == EXIT_TARGET_NAME:
                return _with_threshold(item)
        return None

    # 자동화가 안 돌고 있으면 여기서 직접 읽는다.
    # ⚠️ 예전엔 존재하지 않는 이름(TARGET_DEFINITIONS)을 import 해서 이 경로가 늘
    # ImportError 로 죽었고, 러너는 그걸 '마무리 실패'로 삼켰다(2026-08-22 발견 —
    # after_resume 는 targets 를 안 넘기므로 **항상** 이 경로를 탄다). 정의는 런타임과
    # 같은 규칙(임베드 targets.json → 기본값)으로 읽는다.
    from macroapp.config import load_target_definitions, load_targets
    from macroapp.paths import app_dir

    base_dir = app_dir()
    loaded = load_targets(base_dir, log, definitions=load_target_definitions(base_dir, log))
    if not loaded:
        return None
    for item in loaded:
        if item.name == EXIT_TARGET_NAME:
            return _with_threshold(item)
    return None


def _with_threshold(target):
    """같은 그림이라도 이 단계에서는 우리 임계값을 쓴다(자동화와 분리)."""

    from copy import copy

    # ⚠️ 사본을 쓴다. 자동화 루프와 같은 객체를 공유하면 두 스레드가 같은
    # _last_match 캐시를 동시에 갱신해 서로의 탐색 위치를 흔든다.
    clone = copy(target)
    clone.threshold = EXIT_MATCH_THRESHOLD
    clone._last_match = None
    clone._last_match_misses = 0
    return clone


def _pad(button_name: str, log: LogCallback) -> bool:
    button = input_gamepad.KEY_TO_GAMEPAD.get(str(button_name).strip().lower())
    if button is None:
        return False
    try:
        return bool(input_gamepad.send_gamepad_button(button))
    except Exception as exc:  # noqa: BLE001
        log(f"[종료] 패드 입력 실패: {exc}")
        return False


def _press(method: str, manager: InactiveManager, target, center, log: LogCallback) -> bool:
    if method == "pad":
        return _pad(EXIT_PAD_BUTTON, log)
    if center is None:
        return False

    x, y = _jitter(center, target)
    start_x, start_y = manager.get_virtual_start_position(x, y)
    try:
        return bool(manager.post_curved_click(start_x, start_y, x, y))
    except Exception as exc:  # noqa: BLE001
        log(f"[종료] 좌표 입력 실패: {exc}")
        return False


def _jitter(center, target) -> tuple[int, int]:
    """그림 영역 안에서 중심 주변으로 조금 흔든다(자동화와 같은 방식)."""

    import random

    if target.image_gray is None:
        return int(center[0]), int(center[1])
    height, width = target.image_gray.shape[:2]
    max_x = min(CLICK_JITTER_PIXELS, max(0, (width - 1) // 2))
    max_y = min(CLICK_JITTER_PIXELS, max(0, (height - 1) // 2))
    return (
        int(center[0]) + random.randint(-max_x, max_x),
        int(center[1]) + random.randint(-max_y, max_y),
    )

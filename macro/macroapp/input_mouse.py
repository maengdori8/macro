from __future__ import annotations
import random
import time
from typing import Optional

from macroapp import winapi
from macroapp.logging_util import LogCallback
from macroapp.config import (
    CLICK_MESSAGE_DELAY_SECONDS,
    CURVED_CLICK_MIN_STEPS,
    CURVED_CLICK_MAX_STEPS,
    CURVED_CLICK_MOVE_DURATION_MIN_SECONDS,
    CURVED_CLICK_MOVE_DURATION_MAX_SECONDS,
    CURVED_CLICK_STEP_JITTER,
)
from macroapp.input_message import post_mouse_move, post_mouse_down, post_mouse_up

def get_bezier_point(
    p1: tuple[int, int],
    p2: tuple[int, int],
    p3: tuple[int, int],
    t: float,
) -> tuple[int, int]:
    """2차 베지에 곡선 위의 한 점을 계산합니다."""

    t = max(0.0, min(1.0, float(t)))
    one_minus_t = 1.0 - t
    x = one_minus_t * one_minus_t * p1[0] + 2 * one_minus_t * t * p2[0] + t * t * p3[0]
    y = one_minus_t * one_minus_t * p1[1] + 2 * one_minus_t * t * p2[1] + t * t * p3[1]
    return int(round(x)), int(round(y))


def _build_bezier_control_point(
    start_pos: tuple[int, int],
    end_pos: tuple[int, int],
) -> tuple[int, int]:
    """출발점과 목적지 사이에 랜덤한 휨을 가진 제어점을 만듭니다."""

    start_x, start_y = start_pos
    end_x, end_y = end_pos
    dx = end_x - start_x
    dy = end_y - start_y
    distance = max(1.0, (dx * dx + dy * dy) ** 0.5)

    mid_x = (start_x + end_x) / 2.0
    mid_y = (start_y + end_y) / 2.0
    perpendicular_x = -dy / distance
    perpendicular_y = dx / distance

    bend = random.uniform(-0.35, 0.35) * distance
    if abs(bend) < 12.0 and distance >= 24.0:
        bend = 12.0 if bend >= 0 else -12.0

    control_x = mid_x + perpendicular_x * bend + random.uniform(-8.0, 8.0)
    control_y = mid_y + perpendicular_y * bend + random.uniform(-8.0, 8.0)
    return int(round(control_x)), int(round(control_y))


def pick_move_duration(min_seconds: float, max_seconds: float, rng=random) -> float:
    """이번 이동에 쓸 전체 이동 시간(초)을 범위 안에서 새로 뽑습니다."""

    low, high = sorted((max(0.0, float(min_seconds)), max(0.0, float(max_seconds))))
    return rng.uniform(low, high)


def build_step_delays(
    steps: int,
    move_duration: float,
    jitter: float,
    rng=random,
) -> list[float]:
    """스텝 사이 대기 시간 목록(steps-1개)을 만듭니다. 합계는 move_duration 그대로입니다.

    각 간격을 1±jitter 배로 흔든 뒤 합계를 move_duration 에 다시 맞춥니다.
    전체 이동 시간은 뽑은 값 그대로 두고, 한 이동 안의 순간 속도만 계속 바뀝니다.
    jitter 가 0 이면 예전과 같은 등간격입니다.
    """

    gaps = max(1, int(steps) - 1)
    total = max(0.0, float(move_duration))
    spread = max(0.0, min(0.95, float(jitter)))
    if total <= 0.0:
        return [0.0] * gaps
    if spread <= 0.0:
        return [total / gaps] * gaps
    weights = [rng.uniform(1.0 - spread, 1.0 + spread) for _ in range(gaps)]
    scale = total / sum(weights)
    return [weight * scale for weight in weights]


def _clamp_client_point(
    point: tuple[int, int],
    client_width: int,
    client_height: int,
) -> tuple[int, int]:
    """좌표가 클라이언트 영역 밖으로 나가지 않게 보정합니다."""

    max_x = max(0, int(client_width) - 1)
    max_y = max(0, int(client_height) - 1)
    x = max(0, min(max_x, int(point[0])))
    y = max(0, min(max_y, int(point[1])))
    return x, y


def post_curved_click(
    hwnd: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    steps: Optional[int] = None,
    move_duration: Optional[float] = None,
    down_up_delay: float = CLICK_MESSAGE_DELAY_SECONDS,
    use_send_message: bool = False,
    logger: Optional[LogCallback] = None,
) -> bool:
    """클라이언트 좌표 기준으로 베지에 곡선 이동 후 왼쪽 클릭 메시지를 보냅니다.

    move_duration 을 안 주면 이동마다 CURVED_CLICK_MOVE_DURATION_MIN~MAX 범위에서 새로
    뽑습니다(이동 속도 랜덤화). 스텝 간격도 CURVED_CLICK_STEP_JITTER 만큼 흔듭니다.
    """

    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not winapi.win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    left, top, right, bottom = winapi.win32gui.GetClientRect(hwnd)
    client_width = int(right - left)
    client_height = int(bottom - top)
    if client_width <= 0 or client_height <= 0:
        raise RuntimeError("대상 창의 클라이언트 영역 크기가 0입니다.")

    if steps is None:
        steps = random.randint(CURVED_CLICK_MIN_STEPS, CURVED_CLICK_MAX_STEPS)
    steps = max(2, int(steps))

    start_pos = _clamp_client_point((start_x, start_y), client_width, client_height)
    end_pos = _clamp_client_point((end_x, end_y), client_width, client_height)
    control_pos = _clamp_client_point(
        _build_bezier_control_point(start_pos, end_pos),
        client_width,
        client_height,
    )

    # ⚠️ 속도 난수는 **궤적 난수(스텝 수 → 휨 → 제어점 흔들림)를 모두 뽑은 뒤**에 뽑는다.
    # 순서를 바꾸면 같은 시드에서 궤적 좌표가 달라진다 — 궤적은 불변식(CLAUDE.md)이고
    # 이번 변경은 '속도'만 바꾼다. tests/test_input_mouse_speed.py 가 좌표를 바이트 단위로 고정한다.
    if move_duration is None:
        move_duration = pick_move_duration(
            CURVED_CLICK_MOVE_DURATION_MIN_SECONDS,
            CURVED_CLICK_MOVE_DURATION_MAX_SECONDS,
        )
    step_delays = build_step_delays(steps, move_duration, CURVED_CLICK_STEP_JITTER)

    for index in range(steps):
        t = index / (steps - 1)
        move_x, move_y = _clamp_client_point(
            get_bezier_point(start_pos, control_pos, end_pos, t),
            client_width,
            client_height,
        )
        post_mouse_move(
            hwnd,
            move_x,
            move_y,
            use_send_message=use_send_message,
        )
        if index < steps - 1 and step_delays[index] > 0:
            time.sleep(step_delays[index])

    final_x, final_y = end_pos
    post_mouse_down(hwnd, final_x, final_y, use_send_message=use_send_message)
    if down_up_delay > 0:
        time.sleep(down_up_delay)
    post_mouse_up(hwnd, final_x, final_y, use_send_message=use_send_message)
    return True

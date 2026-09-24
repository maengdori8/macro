"""곡선 마우스 이동 — 속도는 넓게 랜덤, 궤적은 예전과 바이트 동일.

## 왜 이 테스트가 있나

사용자 실측: 마우스 이동 속도가 일정하면 게임이 마우스를 못 읽는다. 예전 코드는
속도를 랜덤화하는 것처럼 보였지만(스텝 수 15~25 랜덤) **실제로는 안 했다** —
위치가 시간에 대해 B(시간/0.2초)로 정해져 스텝 수는 샘플링 촘촘함만 바꾸고, 이동
시간은 0.2초 고정이었다. 실측(2026-09-24, 800px·40회): 이동 시간 0.205~0.222초,
같은 거리 평균 속도 폭 x1.1.

이 테스트가 고정하는 계약:
1. **궤적은 불변(CLAUDE.md)** — 같은 시드면 보내는 좌표가 변경 전과 바이트 동일.
   속도 난수는 궤적 난수(스텝 수 → 휨 → 제어점 흔들림)를 다 뽑은 **뒤**에 뽑는다.
   아래 GOLDEN 은 변경 전 HEAD 구현으로 뜬 값이다.
2. 이동 시간은 이동마다 설정 범위에서 새로 뽑힌다(같은 거리 속도 폭이 실제로 넓다).
3. 한 이동 안에서도 스텝 간격이 흔들린다(순간 속도가 계속 바뀐다). 합계는 뽑은 시간 그대로.
4. 호출자가 move_duration 을 명시하면 그 시간을 정확히 지킨다.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from macroapp import config
from macroapp import input_mouse as im

# (시드, 시작, 끝, 변경 전 HEAD 구현이 보낸 WM_MOUSEMOVE 좌표 전부)
GOLDEN = (
    (1, (400, 540), (1200, 540), [[400, 540], [451, 544], [501, 547], [552, 550], [602, 552], [652, 554], [702, 555], [752, 556], [802, 556], [852, 556], [902, 555], [952, 554], [1002, 552], [1052, 550], [1101, 547], [1151, 544], [1200, 540]]),
    (7, (100, 100), (1800, 950), [[100, 100], [163, 197], [228, 289], [297, 374], [369, 454], [443, 528], [521, 596], [602, 658], [685, 714], [772, 765], [861, 810], [954, 849], [1049, 882], [1147, 909], [1249, 930], [1353, 946], [1460, 956], [1571, 960], [1684, 958], [1800, 950]]),
    (42, (960, 540), (980, 560), [[960, 540], [962, 540], [963, 540], [965, 540], [967, 540], [968, 540], [970, 540], [971, 540], [972, 541], [973, 541], [974, 542], [975, 543], [976, 544], [977, 544], [978, 545], [978, 546], [979, 548], [979, 549], [980, 550], [980, 552], [980, 553], [980, 555], [980, 556], [980, 558], [980, 560]]),
    (2026, (1500, 200), (300, 900), [[1500, 200], [1432, 266], [1362, 329], [1291, 389], [1218, 447], [1143, 502], [1066, 554], [988, 603], [908, 650], [826, 694], [743, 735], [658, 774], [571, 809], [482, 842], [392, 872], [300, 900]]),
)

_FAKE_WINDOW = SimpleNamespace(
    IsWindow=lambda hwnd: True,
    GetClientRect=lambda hwnd: (0, 0, 1920, 1080),
)


def _run(start, end, *, move_duration=None):
    """post_curved_click 을 가짜 창에서 돌려 (좌표들, 클릭들, 대기 시간들)을 돌려준다."""
    moves, clicks, sleeps = [], [], []
    with patch.object(im.winapi, "win32gui", _FAKE_WINDOW),         patch.object(im, "post_mouse_move", lambda h, x, y, **k: moves.append([x, y])),         patch.object(im, "post_mouse_down", lambda h, x, y, **k: clicks.append(("down", x, y))),         patch.object(im, "post_mouse_up", lambda h, x, y, **k: clicks.append(("up", x, y))),         patch.object(im.time, "sleep", lambda seconds: sleeps.append(seconds)):
        kwargs = {} if move_duration is None else {"move_duration": move_duration}
        im.post_curved_click(1, start[0], start[1], end[0], end[1], **kwargs)
    return moves, clicks, sleeps


@pytest.mark.parametrize("seed,start,end,expected", GOLDEN)
def test_궤적은_변경_전과_바이트_동일하다(seed, start, end, expected) -> None:
    random.seed(seed)
    moves, clicks, _ = _run(start, end)
    assert moves == expected, "궤적이 바뀌었다 — 속도 난수가 궤적 난수보다 먼저 뽑혔나?"
    assert clicks == [("down", *end), ("up", *end)]


def test_이동마다_이동_시간이_범위_안에서_새로_뽑힌다() -> None:
    random.seed(12345)
    totals = []
    for _ in range(200):
        # 클릭 사이 대기(down_up_delay)는 이동 시간이 아니므로 마지막 하나를 뺀다.
        _, _, sleeps = _run((400, 540), (1200, 540))
        totals.append(sum(sleeps[:-1]))
    low = config.CURVED_CLICK_MOVE_DURATION_MIN_SECONDS
    high = config.CURVED_CLICK_MOVE_DURATION_MAX_SECONDS
    assert all(low - 1e-9 <= t <= high + 1e-9 for t in totals), (min(totals), max(totals))
    # 예전(0.2초 고정)은 폭 x1.0 이었다. 범위 양 끝 근처까지 실제로 퍼져야 한다.
    assert max(totals) / min(totals) >= 3.0, (min(totals), max(totals))
    span = high - low
    assert min(totals) <= low + span * 0.1
    assert max(totals) >= high - span * 0.1


def test_한_이동_안에서도_스텝_간격이_흔들린다() -> None:
    random.seed(99)
    _, _, sleeps = _run((100, 100), (1800, 950))
    gaps = sleeps[:-1]
    assert max(gaps) / min(gaps) >= 1.5, gaps   # 등간격(예전)이면 1.0


def test_명시한_이동_시간은_정확히_지킨다() -> None:
    random.seed(3)
    _, _, sleeps = _run((400, 540), (1200, 540), move_duration=0.3)
    assert sum(sleeps[:-1]) == pytest.approx(0.3)


def test_build_step_delays_합계와_개수() -> None:
    rng = random.Random(7)
    delays = im.build_step_delays(20, 0.4, 0.45, rng=rng)
    assert len(delays) == 19
    assert sum(delays) == pytest.approx(0.4)
    assert all(d > 0 for d in delays)


def test_build_step_delays_흔들림_0이면_예전처럼_등간격() -> None:
    delays = im.build_step_delays(11, 0.2, 0.0)
    assert delays == pytest.approx([0.02] * 10)


def test_build_step_delays_경계값() -> None:
    assert im.build_step_delays(2, 0.0, 0.45) == [0.0]
    # 흔들림이 1 이상이어도 간격이 0 이하로 떨어지지 않게 막는다.
    rng = random.Random(1)
    assert all(d > 0 for d in im.build_step_delays(30, 0.3, 5.0, rng=rng))


def test_pick_move_duration_범위와_역순_입력() -> None:
    rng = random.Random(5)
    values = [im.pick_move_duration(0.5, 0.12, rng=rng) for _ in range(500)]
    assert all(0.12 <= v <= 0.5 for v in values)


def test_설정값_고정() -> None:
    low = config.CURVED_CLICK_MOVE_DURATION_MIN_SECONDS
    high = config.CURVED_CLICK_MOVE_DURATION_MAX_SECONDS
    assert 0.0 < low < high
    # 범위를 다시 좁히면 '속도가 일정해서 게임이 못 읽는' 상태로 돌아간다.
    assert high / low >= 3.0
    assert 0.0 < config.CURVED_CLICK_STEP_JITTER < 1.0

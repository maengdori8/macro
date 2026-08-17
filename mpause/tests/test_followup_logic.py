"""마무리 단계의 판정 규칙 — Windows 없이 전부 검증한다.

캡처·입력은 여기서 하지 않는다. '지금 보이나'와 '지금 몇 시인가'만 넣어
다음 행동이 맞는지 본다. 이 규칙이 틀리면 같은 화면을 연타하거나(다음 화면까지
눌러 버린다), 한 프레임 깜빡임을 성공으로 오인한다.
"""

from __future__ import annotations

import threading
import time

import pytest

from mpauseapp import core, runner as runner_mod


def make(**kwargs) -> core.ConfirmSequence:
    options = dict(
        methods=("click", "pad"),
        verify_seconds=1.0,
        settle_seconds=0.5,
        timeout_seconds=10.0,
        max_presses=4,
    )
    options.update(kwargs)
    return core.ConfirmSequence(0.0, **options)


def test_presses_immediately_when_first_seen():
    sequence = make()
    decision = sequence.decide(0.3, True)
    assert (decision.action, decision.method) == ("press", "click")


def test_first_press_waits_after_first_sighting():
    """감지 즉시 누르지 않는다 — 첫 발견 후 지정한 시간이 지나야 누른다.

    뜬 직후는 화면이 자리 잡는 중이라 입력이 안 먹거나 엉뚱하게 들어갈 수 있다
    (사용자 실측 요구). 대기 중 깜빡여도 기준은 첫 발견 시각 그대로다.
    """
    sequence = make(first_press_delay_seconds=3.0)
    assert sequence.decide(0.0, True).action == "wait"     # 첫 발견 — 대기 시작
    assert sequence.decide(1.0, False).action == "wait"    # 깜빡임 — 기준 유지
    assert sequence.decide(2.9, True).action == "wait"     # 아직 3초 전
    decision = sequence.decide(3.0, True)
    assert (decision.action, decision.method) == ("press", "click")
    assert sequence.opens == 0                             # 보인 뒤라 열지도 않는다


def test_does_not_press_again_inside_verify_window():
    sequence = make()
    sequence.decide(0.0, True)
    assert sequence.decide(0.5, True).action == "wait"
    assert sequence.presses == 1


def test_switches_method_when_still_visible():
    """같은 방법으로 계속 두드리지 않는다 — 안 먹히는 경로면 바꿔 본다."""
    sequence = make()
    assert sequence.decide(0.0, True).method == "click"
    assert sequence.decide(1.0, True).method == "pad"
    assert sequence.decide(2.0, True).method == "click"


def test_done_only_after_settle():
    sequence = make()
    sequence.decide(0.0, True)
    assert sequence.decide(0.2, False).action == "wait"   # 깜빡임일 수 있다
    assert sequence.decide(0.6, False).action == "done"


def test_never_appearing_times_out_quietly():
    sequence = make(timeout_seconds=5.0)
    assert sequence.decide(4.9, False).action == "wait"
    assert sequence.decide(5.0, False).action == "timeout"
    assert sequence.presses == 0


def test_gives_up_after_max_presses():
    sequence = make(max_presses=2, verify_seconds=1.0)
    sequence.decide(0.0, True)
    sequence.decide(1.0, True)
    assert sequence.decide(2.0, True).action == "exhausted"


def test_last_press_still_gets_its_verify_window():
    """마지막 누름 직후 '아직 보임' 한 프레임으로 실패를 확정하면 안 된다.

    게임 반응(누름→소멸)은 루프 틱보다 느리다. 여기서 바로 exhausted 를 내면
    max_presses=N 이 사실상 'N-1회 + 마지막 1회는 판정 기회 없음'이 되고,
    마지막 시도가 성공해도 사용자에게 실패로 보고된다(적대적 리뷰 확정 건).
    """
    sequence = make(max_presses=2, verify_seconds=1.0, settle_seconds=0.5)
    sequence.decide(0.0, True)                             # 1회
    sequence.decide(1.0, True)                             # 2회(마지막)
    assert sequence.decide(1.1, True).action == "wait"     # verify 창 안 — 판정 유보
    assert sequence.decide(1.4, False).action == "wait"    # 사라짐 — settle 대기
    assert sequence.decide(2.0, False).action == "done"    # 마지막 시도가 성공했다

    still = make(max_presses=2, verify_seconds=1.0)
    still.decide(0.0, True)
    still.decide(1.0, True)
    assert still.decide(2.0, True).action == "exhausted"   # 창이 끝나도 보이면 포기


def test_timeout_does_not_fire_once_pressed():
    """누른 뒤에는 timeout 대신 settle/exhausted 로 끝나야 한다."""
    sequence = make(timeout_seconds=1.0, settle_seconds=0.5)
    sequence.decide(0.0, True)
    assert sequence.decide(1.2, False).action == "done"


def test_empty_methods_is_rejected():
    with pytest.raises(ValueError):
        core.ConfirmSequence(0.0, methods=())


# ─── 열기(open) 단계 — 그림을 띄우는 입력 ──────────────────────────────────


def test_open_fires_after_delay_then_retries():
    """그림이 안 보이면 지연 뒤에 열고, 그래도 안 뜨면 간격을 두고 다시 연다."""
    sequence = make(open_delay_seconds=1.0, open_retry_seconds=2.0, max_opens=2)
    assert sequence.decide(0.5, False).action == "wait"    # 아직 지연 중
    assert sequence.decide(1.0, False).action == "open"
    assert sequence.decide(2.9, False).action == "wait"    # 재시도 간격 전
    assert sequence.decide(3.0, False).action == "open"
    assert sequence.decide(6.0, False).action == "wait"    # 횟수 소진 → 더는 안 연다
    assert sequence.opens == 2


def test_open_stops_once_the_target_was_seen():
    """한 번이라도 보였으면 다시 열지 않는다 — 여는 입력이 화면을 도로 닫는다(토글)."""
    sequence = make(open_delay_seconds=0.5, open_retry_seconds=1.0, max_opens=5)
    assert sequence.decide(0.5, False).action == "open"
    assert sequence.decide(0.6, True).action == "press"
    assert sequence.decide(0.7, False).action == "wait"    # settle 대기, open 아님
    assert sequence.decide(5.0, False).action == "done"    # open 이 끼어들지 않는다
    assert sequence.opens == 1


def test_open_is_disabled_by_default():
    """max_opens=0(기본)이면 기존과 완전히 같은 동작이어야 한다."""
    sequence = make(timeout_seconds=5.0)
    assert sequence.decide(1.0, False).action == "wait"
    assert sequence.decide(5.0, False).action == "timeout"
    assert sequence.opens == 0


def test_open_exhausted_still_times_out_quietly():
    """열기를 다 써도 그림이 끝내 안 뜨면 조용히 timeout 으로 끝난다."""
    sequence = make(
        open_delay_seconds=0.0,
        open_retry_seconds=1.0,
        max_opens=2,
        timeout_seconds=5.0,
    )
    assert sequence.decide(0.0, False).action == "open"
    assert sequence.decide(1.0, False).action == "open"
    assert sequence.decide(5.0, False).action == "timeout"
    assert sequence.presses == 0


def test_open_never_fires_past_the_timeout():
    """시한이 지난 뒤에는 열지 않는다 — 조용히 끝나야 할 실행이 화면을 열면 안 된다."""
    sequence = make(
        open_delay_seconds=0.0,
        open_retry_seconds=1.0,
        max_opens=99,
        timeout_seconds=3.0,
    )
    assert sequence.decide(0.0, False).action == "open"
    assert sequence.decide(3.0, False).action == "timeout"   # open 이 가로채면 안 된다


def test_open_guard_is_seen_itself_not_branch_order():
    """보였지만 누르지 못한 상태(seen=True·presses=0)에서도 다시 열지 않는다.

    평소엔 보이면 즉시 누르므로 '누른 뒤(settle) 분기'가 먼저 가로채지만, 토글
    방지는 분기 순서가 아니라 seen 가드 자체에 걸려 있어야 한다 — 리팩터링으로
    순서가 바뀌어도 안전 규칙이 남게 고정한다(적대적 리뷰 확정 건).
    """
    sequence = make(
        max_presses=0,               # 보여도 누를 수 없는 극단 설정
        open_delay_seconds=0.0,
        open_retry_seconds=1.0,
        max_opens=5,
    )
    assert sequence.decide(0.0, True).action == "exhausted"  # 보였다 — seen=True
    decision = sequence.decide(1.0, False)
    assert decision.action != "open", "보인 적이 있는데 다시 열었다(토글 위험)"
    assert sequence.opens == 0


# ─── 진행바 ────────────────────────────────────────────────────────────────


def test_progress_is_monotonic_across_phases():
    values = [
        core.run_progress("prepare", 0, 1),
        core.run_progress("hold", 0, 10),
        core.run_progress("hold", 5, 10),
        core.run_progress("hold", 10, 10),
        core.run_progress("followup", 0, 12),
        core.run_progress("followup", 6, 12),
        core.run_progress("followup", 12, 12),
        core.run_progress("done", 0, 0),
    ]
    assert values == sorted(values), values
    assert values[0] > 0.0
    assert values[-1] == 1.0


def test_progress_is_clamped():
    assert core.run_progress("hold", -5, 10) >= 0.0
    assert core.run_progress("hold", 999, 10) <= 1.0
    assert core.run_progress("followup", 999, 12) <= 1.0
    assert core.run_progress("hold", 1, 0) <= 1.0   # 0 나눗셈 방지


# ─── 러너와의 연결 (Windows 불필요 — 훅을 주입한다) ─────────────────────────


class FakeHandle:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.name = "t.exe"
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return True


@pytest.fixture
def fake_target(monkeypatch):
    from mpauseapp import winproc

    handle = FakeHandle(41)
    order: list[str] = []
    monkeypatch.setattr(winproc, "list_processes", lambda: [(41, "t.exe")])
    monkeypatch.setattr(winproc, "open_process", lambda pid, name="": handle)
    monkeypatch.setattr(winproc, "suspend", lambda h: order.append("suspend"))
    monkeypatch.setattr(winproc, "resume", lambda h: order.append("resume"))
    return order


def test_followup_runs_after_resume_with_prepared_value(fake_target):
    """마무리는 **재개가 끝난 뒤에만** 돈다. 순서가 바뀌면 멈춘 채로 오래 남는다."""
    order = fake_target
    seen = {}

    def prepare(cancel):
        order.append("prepare")
        return 12345

    def after_resume(cancel, prepared, on_tick):
        order.append("followup")
        seen["prepared"] = prepared
        on_tick(0.9)
        return "ok"

    runner = runner_mod.PauseRunner(prepare=prepare, after_resume=after_resume)
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    assert order.index("prepare") < order.index("resume") < order.index("followup")
    assert seen["prepared"] == 12345

    events = []
    runner.drain(events.append, limit=200)
    done = [e for e in events if e.kind == runner_mod.EVT_DONE]
    assert done and done[0].text == runner_mod.MSG_DONE
    assert any(e.kind == runner_mod.EVT_TICK and e.progress == 1.0 for e in events)


def test_prepare_runs_before_anything_is_touched(fake_target):
    """준비는 정지 **전에** 끝나야 한다.

    정지한 뒤에 부르면 준비에 걸린 시간이 그대로 정지 시간에 더해지고,
    그 구간에서는 취소도 안 듣는다(창을 닫아도 워커가 모른다).
    """
    order = fake_target
    runner = runner_mod.PauseRunner(prepare=lambda cancel: order.append("prepare") or 1)
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)
    assert order.index("prepare") < order.index("suspend")


def test_ledger_holds_the_target_only_while_it_is_touched(fake_target, tmp_path, monkeypatch):
    """프로세스 밖 기록: 정지 중에만 존재하고, 되살리면 사라진다."""
    from mpauseapp import ledger, winproc

    store = tmp_path / "state"
    monkeypatch.setattr(ledger, "path", lambda: store)
    monkeypatch.setattr(winproc, "identity_token", lambda handle: 4242)

    runner = runner_mod.PauseRunner()
    runner.start("t.exe", 5.0)

    deadline = time.monotonic() + 5.0
    while not ledger.pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ledger.pending() == [ledger.Entry(41, 4242)], "정지했는데 기록이 없다"

    runner.shutdown(timeout=5.0)
    assert ledger.pending() == [], "되살렸는데 기록이 남았다"
    assert not store.exists()


def test_followup_failure_is_reported_without_details(fake_target):
    def after_resume(cancel, prepared, on_tick):
        return "failed"

    runner = runner_mod.PauseRunner(after_resume=after_resume)
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    events = []
    runner.drain(events.append, limit=200)
    done = [e for e in events if e.kind == runner_mod.EVT_DONE]
    assert done and done[0].text == runner_mod.MSG_CHECK_SCREEN


def test_followup_exception_never_leaves_target_suspended(fake_target):
    """마무리에서 터져도 이미 재개는 끝나 있어야 하고, 실행은 정상 종료된다."""
    order = fake_target

    def after_resume(cancel, prepared, on_tick):
        raise RuntimeError("boom")

    runner = runner_mod.PauseRunner(after_resume=after_resume)
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    assert "resume" in order
    assert not runner.busy


def test_followup_is_skipped_when_cancelled(fake_target):
    called = threading.Event()

    def after_resume(cancel, prepared, on_tick):
        called.set()
        return "ok"

    runner = runner_mod.PauseRunner(after_resume=after_resume)
    runner.start("t.exe", 5.0)
    runner.shutdown(timeout=5.0)

    assert not called.is_set(), "취소했는데도 마무리가 돌았다"


def test_prepare_failure_does_not_break_the_run(fake_target):
    """준비는 '있으면 좋은 것'이다 — 실패해도 본 동작은 끝까지 간다."""
    order = fake_target

    def prepare(cancel):
        raise RuntimeError("no device")

    runner = runner_mod.PauseRunner(prepare=prepare, after_resume=lambda *a: "quiet")
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    assert "resume" in order
    events = []
    runner.drain(events.append, limit=200)
    assert any(e.kind == runner_mod.EVT_DONE for e in events)

"""실제 프로세스로 '반드시 재개된다' 불변식을 검증한다 (Windows 전용).

프로세스 열거만 우리 자식으로 한정하고(같은 이름의 다른 python.exe 를 실수로
멈추지 않기 위해), 핸들 확보·NtSuspendProcess·NtResumeProcess 는 전부 진짜다.

정지 여부는 자식이 파일에 쓰는 줄 수로 잰다 — API 반환값이 아니라 실제 실행이
멈췄는지를 봐야 의미가 있다.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

from mpauseapp import runner as runner_mod
from mpauseapp import winproc

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows 전용"
)

CHILD_SOURCE = textwrap.dedent(
    """
    import sys, time
    path = sys.argv[1]
    n = 0
    with open(path, "w", encoding="utf-8", buffering=1) as f:
        while True:
            n += 1
            f.write(f"{n}\\n")
            f.flush()
            time.sleep(0.02)
    """
)

CHILD_NAME = "mpause_test_child.exe"


@pytest.fixture
def child(tmp_path, monkeypatch):
    """카운터를 찍는 자식 프로세스 + 그것만 보이는 프로세스 목록."""
    script = tmp_path / "child.py"
    script.write_text(CHILD_SOURCE, encoding="utf-8")
    out = tmp_path / "counter.txt"
    proc = subprocess.Popen([sys.executable, str(script), str(out)])

    # 이름 매칭이 우리 자식 하나만 고르도록 열거를 한정한다.
    monkeypatch.setattr(
        winproc, "list_processes", lambda: [(proc.pid, CHILD_NAME)]
    )

    def lines() -> int:
        try:
            return sum(1 for _ in out.open("r", encoding="utf-8"))
        except OSError:
            return 0

    # 자식이 실제로 돌기 시작할 때까지 기다린다.
    deadline = time.time() + 5
    while lines() < 3 and time.time() < deadline:
        time.sleep(0.05)
    assert lines() >= 3, "자식 프로세스가 시작되지 않았습니다."

    try:
        yield proc, lines
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def drain(runner: runner_mod.PauseRunner) -> list[runner_mod.Event]:
    events: list[runner_mod.Event] = []
    runner.drain(events.append, limit=500)
    return events


def wait_idle(runner: runner_mod.PauseRunner, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while runner.busy and time.time() < deadline:
        time.sleep(0.02)
    assert not runner.busy, "워커가 끝나지 않았습니다."


def test_suspend_then_auto_resume(child):
    proc, lines = child
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 1.0)

    # 정지가 걸릴 때까지 잠깐 기다린 뒤, 유지 구간 동안 안 자라는지 본다.
    time.sleep(0.3)
    during_start = lines()
    time.sleep(0.5)
    during_end = lines()
    assert during_end - during_start <= 1, (
        f"유지 구간에 {during_end - during_start}줄 증가 — 정지가 안 걸렸다"
    )

    wait_idle(runner)
    after_resume = lines()
    time.sleep(0.4)
    assert lines() - after_resume >= 5, "재개 후에도 멈춰 있다"

    kinds = [event.kind for event in drain(runner)]
    assert runner_mod.EVT_DONE in kinds


def test_cancel_resumes_immediately(child):
    proc, lines = child
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 60.0)  # 아주 긴 유지 시간
    time.sleep(0.4)
    frozen = lines()
    time.sleep(0.3)
    assert lines() - frozen <= 1, "정지가 안 걸렸다"

    runner.cancel()
    wait_idle(runner, timeout=5.0)

    resumed = lines()
    time.sleep(0.4)
    assert lines() - resumed >= 5, "취소 후 재개되지 않았다"


def test_shutdown_resumes_even_mid_countdown(child):
    """창을 닫는 경로 — 60초 유지 중이라도 즉시 되살아나야 한다."""
    proc, lines = child
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 60.0)
    time.sleep(0.4)

    runner.shutdown(timeout=5.0)

    resumed = lines()
    time.sleep(0.4)
    assert lines() - resumed >= 5, "shutdown 후 재개되지 않았다"


def test_force_resume_is_idempotent(child):
    proc, lines = child
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 60.0)
    time.sleep(0.4)
    runner.force_resume()
    runner.force_resume()  # 두 번 불러도 예외가 없어야 한다
    runner.shutdown(timeout=5.0)

    resumed = lines()
    time.sleep(0.4)
    assert lines() - resumed >= 5


def test_is_one_shot_while_busy(child):
    proc, _lines = child
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 1.0) is True
    assert runner.start(CHILD_NAME, 1.0) is False, "실행 중 재시작이 막히지 않았다"
    assert runner.start(CHILD_NAME, 1.0) is False
    wait_idle(runner)
    # 끝난 뒤에는 다시 시작할 수 있어야 한다.
    assert runner.start(CHILD_NAME, 0.2) is True
    wait_idle(runner)
    runner.shutdown()


def test_missing_process_reports_error(monkeypatch):
    monkeypatch.setattr(winproc, "list_processes", lambda: [(1234, "other.exe")])
    runner = runner_mod.PauseRunner()
    assert runner.start("nothing_here.exe", 1.0)
    wait_idle(runner)
    events = drain(runner)
    errors = [e for e in events if e.kind == runner_mod.EVT_ERROR]
    assert errors and errors[0].text == runner_mod.MSG_NOT_RUNNING


def test_open_failure_suspends_nothing(child, monkeypatch):
    """핸들 확보가 하나라도 실패하면 아무것도 멈추지 않아야 한다."""
    proc, lines = child

    def boom(pid, name=""):
        raise winproc.ProcessControlError("접근이 거부되었습니다(테스트).")

    monkeypatch.setattr(winproc, "open_process", boom)
    runner = runner_mod.PauseRunner()
    assert runner.start(CHILD_NAME, 5.0)
    wait_idle(runner)

    before = lines()
    time.sleep(0.4)
    assert lines() - before >= 5, "실패했는데 프로세스가 멈춰 있다"
    errors = [e for e in drain(runner) if e.kind == runner_mod.EVT_ERROR]
    assert errors and "거부" in errors[0].text

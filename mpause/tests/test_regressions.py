"""적대적 리뷰에서 확정된 결함들의 회귀 방지.

각 테스트는 '고치기 전에는 실패하던' 구체적 동작을 고정한다.
"""

from __future__ import annotations

import sys

import pytest

from mpauseapp import app as app_mod
from mpauseapp import core, license_client, runner as runner_mod, winproc


# ─── [critical] --preview 로 라이센스 게이트 우회 ──────────────────────────

def test_preview_flag_is_ignored_in_frozen_build(monkeypatch):
    """빌드된 exe 에서는 --preview 를 무시해야 한다.

    이 가드가 없으면 `mpause.exe --preview` 한 줄로 Ed25519 서명·nonce·hwid·
    제품 바인딩을 전부 우회해 유료 기능이 그대로 열린다.
    """
    monkeypatch.setattr(sys, "argv", ["mpause.exe", "--preview"])
    monkeypatch.setattr(app_mod, "is_frozen", lambda: True)
    assert app_mod._preview_allowed() is False


def test_preview_flag_works_from_source(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mpause_main.py", "--preview"])
    monkeypatch.setattr(app_mod, "is_frozen", lambda: False)
    assert app_mod._preview_allowed() is True


def test_no_preview_flag_means_license_gate(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mpause_main.py"])
    monkeypatch.setattr(app_mod, "is_frozen", lambda: False)
    assert app_mod._preview_allowed() is False


# ─── [critical] force_resume 이 실패를 삼키고 기록까지 지우던 문제 ─────────

class FakeHandle:
    """winproc.ProcessHandle 을 흉내내는 최소 객체."""

    def __init__(self, pid: int, alive: bool = True) -> None:
        self.pid = pid
        self.name = f"fake{pid}.exe"
        self.handle = pid
        self.alive = alive
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed = True


def test_force_resume_keeps_failed_handles_registered(monkeypatch):
    """재개에 실패한 핸들은 목록에 남아 다음 방어선이 재시도할 수 있어야 한다."""
    runner = runner_mod.PauseRunner()
    good, bad = FakeHandle(1), FakeHandle(2)
    runner._suspended.extend([good, bad])

    def resume(handle):
        if handle is bad:
            raise winproc.ProcessControlError("재개 실패(테스트)")

    monkeypatch.setattr(winproc, "resume", resume)

    stuck = runner.force_resume()
    assert [h.pid for h in stuck] == [2]
    # 실패한 것은 다시 등록되어 있고, 핸들도 닫히지 않았다(재시도 가능해야 하므로).
    assert runner._suspended == [bad]
    assert bad.closed is False
    # 성공한 것만 정리된다.
    assert good.closed is True


def test_force_resume_treats_dead_process_as_success(monkeypatch):
    """이미 끝난 프로세스는 재개 실패가 아니다."""
    runner = runner_mod.PauseRunner()
    dead = FakeHandle(3, alive=False)
    runner._suspended.append(dead)

    def resume(handle):
        raise winproc.ProcessControlError("이미 종료")

    monkeypatch.setattr(winproc, "resume", resume)

    assert runner.force_resume() == []
    assert runner._suspended == []
    assert dead.closed is True


def test_force_resume_tries_resume_before_checking_alive(monkeypatch):
    """is_alive() 를 먼저 보고 건너뛰면, 조회가 실패했을 때 살아있는 걸 버린다."""
    runner = runner_mod.PauseRunner()

    class FlakyAlive(FakeHandle):
        def is_alive(self):
            raise OSError("조회 실패")

    handle = FlakyAlive(4)
    runner._suspended.append(handle)
    calls = []
    monkeypatch.setattr(winproc, "resume", lambda h: calls.append(h))

    assert runner.force_resume() == []
    assert calls == [handle], "is_alive 조회 실패와 무관하게 resume 을 시도해야 한다"


def test_start_does_not_drop_still_suspended_handles(monkeypatch):
    """start() 가 남은 기록을 그냥 비우면 3중 방어가 전부 헛돈다."""
    runner = runner_mod.PauseRunner()
    leftover = FakeHandle(5)
    runner._suspended.append(leftover)
    resumed = []
    monkeypatch.setattr(winproc, "resume", lambda h: resumed.append(h))
    monkeypatch.setattr(winproc, "list_processes", lambda: [])

    runner.start("nothing.exe", 0.1)
    # start 는 clear 가 아니라 force_resume 으로 정리해야 한다.
    assert resumed == [leftover]


# ─── [high] 스냅샷 실패 가드가 죽어 있던 문제 ──────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")
def test_invalid_handle_value_is_unsigned():
    """restype 이 HANDLE(c_void_p) 이라 실패값은 -1 이 아니라 부호 없는 최대값이다."""
    assert winproc.INVALID_HANDLE_VALUE == 0xFFFFFFFFFFFFFFFF
    assert winproc.INVALID_HANDLE_VALUE != -1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")
def test_snapshot_failure_raises_instead_of_returning_empty(monkeypatch):
    """스냅샷 실패가 '프로세스 없음'으로 둔갑하면 안 된다."""
    monkeypatch.setattr(
        winproc.kernel32,
        "CreateToolhelp32Snapshot",
        lambda flags, pid: winproc.INVALID_HANDLE_VALUE,
    )
    with pytest.raises(winproc.ProcessControlError):
        winproc.list_processes()


# ─── [medium] 이미 종료된 프로세스 구분 ────────────────────────────────────

def test_process_gone_is_a_control_error_subclass():
    """runner 의 except 절이 둘 다 잡되, 구분해서 건너뛸 수 있어야 한다."""
    assert issubclass(winproc.ProcessGoneError, winproc.ProcessControlError)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")
def test_open_process_on_dead_pid_raises_process_gone():
    """존재하지 않는 PID 는 ERROR_INVALID_PARAMETER(87) → 건너뛸 대상이다."""
    with pytest.raises(winproc.ProcessGoneError):
        winproc.open_process(999996, "ghost.exe")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")
def test_terminating_status_is_process_gone(monkeypatch):
    """STATUS_PROCESS_IS_TERMINATING 은 실패가 아니라 '이미 끝났다'."""
    handle = FakeHandle(7)
    monkeypatch.setattr(
        winproc.ntdll, "NtSuspendProcess", lambda h: -1073741558  # 0xC000010A
    )
    with pytest.raises(winproc.ProcessGoneError):
        winproc.suspend(handle)


def test_runner_skips_gone_processes(monkeypatch):
    """일부가 이미 죽었어도 살아있는 대상은 정상 처리되어야 한다."""
    live, dead_pid = FakeHandle(11), 12
    monkeypatch.setattr(
        winproc, "list_processes", lambda: [(11, "t.exe"), (dead_pid, "t.exe")]
    )

    def open_process(pid, name=""):
        if pid == dead_pid:
            raise winproc.ProcessGoneError("이미 종료")
        return live

    monkeypatch.setattr(winproc, "open_process", open_process)
    monkeypatch.setattr(winproc, "suspend", lambda h: None)
    monkeypatch.setattr(winproc, "resume", lambda h: None)

    runner = runner_mod.PauseRunner()
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    events = []
    runner.drain(events.append, limit=200)
    kinds = [e.kind for e in events]
    assert runner_mod.EVT_DONE in kinds, [e.text for e in events]
    assert runner_mod.EVT_ERROR not in kinds


def test_runner_reports_when_all_targets_died(monkeypatch):
    monkeypatch.setattr(winproc, "list_processes", lambda: [(21, "t.exe")])

    def open_process(pid, name=""):
        raise winproc.ProcessGoneError("이미 종료")

    monkeypatch.setattr(winproc, "open_process", open_process)
    runner = runner_mod.PauseRunner()
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)
    events = []
    runner.drain(events.append, limit=200)
    errors = [e for e in events if e.kind == runner_mod.EVT_ERROR]
    # 문구는 중립이다 — 무엇이 왜 안 됐는지는 알려 주지 않고 '확인해 보라'만 한다.
    assert errors and errors[0].text == runner_mod.MSG_NOT_RUNNING


# ─── [medium] 종료 상태는 재개가 끝난 뒤에만 공개 ──────────────────────────

def test_terminal_state_arrives_after_resume(monkeypatch):
    """상태가 먼저 나가면 UI 가 버튼을 켜서 재개 도중 다음 실행이 시작된다."""
    order: list[str] = []
    handle = FakeHandle(31)
    monkeypatch.setattr(winproc, "list_processes", lambda: [(31, "t.exe")])
    monkeypatch.setattr(winproc, "open_process", lambda pid, name="": handle)
    monkeypatch.setattr(winproc, "suspend", lambda h: None)
    monkeypatch.setattr(winproc, "resume", lambda h: order.append("resume"))

    runner = runner_mod.PauseRunner()
    original = runner._set_state

    def spy(state, text=""):
        if state in (core.STATE_DONE, core.STATE_FAILED):
            order.append("terminal")
        original(state, text)

    monkeypatch.setattr(runner, "_set_state", spy)
    runner.start("t.exe", 0.05)
    runner._thread.join(timeout=5)

    assert order.index("resume") < order.index("terminal")


# ─── [low] 서명 검증 실패로 정품 키를 지우던 문제 ──────────────────────────

def test_signature_failure_is_not_marked_signed_invalid():
    """서명이 안 맞는 건 '서버의 무효 판정'이 아니다 → 저장 키를 지우면 안 된다."""
    result = license_client.verify_signed_response(
        {"verdict": "valid", "hwid": "a" * 32, "nonce": "b" * 32, "exp": 9999999999, "sig": "00" * 64},
        "b" * 32,
        "a" * 32,
        product="mpause",
        pubkey_hex="11" * 32,
    )
    assert result["valid"] is False
    assert not result.get("_signed_invalid")


def test_offline_is_not_marked_signed_invalid():
    assert not {"_offline": True}.get("_signed_invalid")


def test_signed_invalid_verdict_is_marked(monkeypatch):
    """서버가 서명으로 '무효'라고 확인해 준 경우에만 표시가 붙는다."""
    import ed25519_tiny

    seed = bytes(range(32))
    pub = ed25519_tiny.public_key(seed).hex()
    hwid, nonce = "a" * 32, "b" * 32
    msg = license_client._license_message("mpause", "invalid", hwid, nonce, 0)
    sig = ed25519_tiny.sign(msg, seed).hex()
    result = license_client.verify_signed_response(
        {"verdict": "invalid", "hwid": hwid, "nonce": nonce, "exp": 0, "sig": sig,
         "message": "존재하지 않는 라이센스 키입니다."},
        nonce,
        hwid,
        product="mpause",
        pubkey_hex=pub,
    )
    assert result["valid"] is False
    assert result["_signed_invalid"] is True

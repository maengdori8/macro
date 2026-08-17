"""네 번째 방어선(프로세스 밖 기록) 검증.

여기서 지켜야 할 것 두 가지가 정반대 방향이다.
  * 우리가 강제로 죽어도 **반드시 되살아난다**.
  * PID 가 재사용됐으면 **절대 건드리지 않는다** (남의 프로그램을 재개하면
    그쪽 상태가 깨진다).
"""

from __future__ import annotations

import pytest

from mpauseapp import ledger


@pytest.fixture
def store(tmp_path, monkeypatch):
    target = tmp_path / "state"
    monkeypatch.setattr(ledger, "path", lambda: target)
    return target


# ─── 순수 로직 ─────────────────────────────────────────────────────────────


def test_serialize_parse_roundtrip():
    entries = [ledger.Entry(11, 999), ledger.Entry(22, 1000)]
    assert ledger.parse(ledger.serialize(entries)) == entries


def test_broken_lines_do_not_kill_the_rest():
    """강제 종료 도중 반쯤 쓰인 파일에서도 살릴 수 있는 건 살려야 한다."""
    text = "11 999\n쓰레기\n22\n\n33 abc\n44 1234\n"
    assert ledger.parse(text) == [ledger.Entry(11, 999), ledger.Entry(44, 1234)]


def test_duplicate_pid_keeps_one():
    assert ledger.parse("11 1\n11 2\n") == [ledger.Entry(11, 1)]


def test_merged_replaces_same_pid():
    entries = [ledger.Entry(11, 1), ledger.Entry(22, 2)]
    merged = ledger.merged(entries, ledger.Entry(11, 9))
    assert merged == [ledger.Entry(22, 2), ledger.Entry(11, 9)]


@pytest.mark.parametrize(
    "recorded, actual, expected",
    [
        (555, 555, True),    # 같은 프로세스 → 되살린다
        (555, 777, False),   # PID 재사용 → 남의 것이다, 절대 건드리지 않는다
        (555, None, False),  # 못 열었다(이미 종료/권한 없음)
        (0, 555, False),     # 기록 당시 신원을 못 읽었다 → 확인 불가, 안전한 쪽
        (555, 0, False),     # 지금 신원을 못 읽었다 → 확인 불가
    ],
)
def test_should_resume_rules(recorded, actual, expected):
    assert ledger.should_resume(ledger.Entry(1234, recorded), actual) is expected


# ─── 파일 ──────────────────────────────────────────────────────────────────


def test_record_then_forget_leaves_no_trace(store):
    ledger.record(4242, 777)
    assert store.exists()
    assert ledger.pending() == [ledger.Entry(4242, 777)]

    ledger.forget(4242)
    assert ledger.pending() == []
    assert not store.exists(), "마지막 항목이 지워지면 파일도 남기지 않는다"


def test_file_contains_numbers_only(store):
    ledger.record(4242, 777)
    text = store.read_text(encoding="utf-8")
    assert text.strip() == "4242 777"


def test_unreadable_store_is_not_fatal(monkeypatch, tmp_path):
    """기록에 실패해도 본 동작은 계속돼야 한다(있으면 좋은 것일 뿐)."""
    monkeypatch.setattr(ledger, "path", lambda: tmp_path / "없는폴더" / "x" / "state")
    monkeypatch.setattr(
        ledger.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("나쁨"))
    )
    ledger.record(1, 2)      # 예외가 나오면 안 된다
    assert ledger.pending() == []


# ─── 복구 ──────────────────────────────────────────────────────────────────


class FakeHandle:
    def __init__(self, pid, token):
        self.pid = pid
        self.token = token
        self.closed = False

    def close(self):
        self.closed = True


def test_recover_resumes_only_matching_identities(store, monkeypatch):
    from mpauseapp import winproc

    ledger.record(11, 100)   # 살아 있는 같은 프로세스
    ledger.record(22, 200)   # PID 재사용 — 건드리면 안 된다
    ledger.record(33, 300)   # 이미 종료됨

    handles = {11: FakeHandle(11, 100), 22: FakeHandle(22, 999)}
    resumed: list[int] = []

    def open_process(pid, name=""):
        if pid not in handles:
            raise winproc.ProcessGoneError()
        return handles[pid]

    monkeypatch.setattr(winproc, "open_process", open_process)
    monkeypatch.setattr(winproc, "identity_token", lambda h: h.token)
    monkeypatch.setattr(winproc, "resume", lambda h: resumed.append(h.pid))

    assert ledger.recover() == 1
    assert resumed == [11], "재사용된 PID 를 건드렸다"
    assert all(h.closed for h in handles.values()), "핸들을 닫지 않았다"
    assert ledger.pending() == [], "복구 후 기록이 남았다"
    assert not store.exists()


def test_recover_never_raises(store, monkeypatch):
    from mpauseapp import winproc

    ledger.record(11, 100)
    monkeypatch.setattr(
        winproc, "open_process", lambda pid, name="": (_ for _ in ()).throw(RuntimeError("펑"))
    )
    assert ledger.recover() == 0
    assert ledger.pending() == []

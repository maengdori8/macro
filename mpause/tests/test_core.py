"""순수 로직 검증 — Windows 없이도 전부 돈다."""

import pytest

from mpauseapp import core


# ─── 이름 정규화 / 매칭 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("notepad.exe", "notepad.exe"),
        ("  Notepad.EXE  ", "notepad.exe"),
        ('"chrome.exe"', "chrome.exe"),
        (r"C:\Windows\System32\notepad.exe", "notepad.exe"),
        ("C:/Program Files/app/App.exe", "app.exe"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_name(raw, expected):
    assert core.normalize_name(raw) == expected


@pytest.mark.parametrize(
    "query,actual",
    [
        ("notepad", "notepad.exe"),
        ("notepad.exe", "notepad.exe"),
        ("NOTEPAD", "Notepad.exe"),
        (r"C:\x\notepad.exe", "notepad.exe"),
        ("System", "System"),
    ],
)
def test_matches_accepts(query, actual):
    assert core.matches(query, actual)


@pytest.mark.parametrize(
    "query,actual",
    [
        # 부분 일치는 사고로 이어지므로 절대 매치되면 안 된다.
        ("ex", "explorer.exe"),
        ("note", "notepad.exe"),
        ("notepad", "notepad2.exe"),
        ("chrome", "chromedriver.exe"),
        ("", "notepad.exe"),
        ("notepad", ""),
    ],
)
def test_matches_rejects(query, actual):
    assert not core.matches(query, actual)


# ─── 위험 프로세스 판정 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    ["csrss.exe", "CSRSS.EXE", "csrss", "lsass.exe", "svchost.exe", "System", "wininit"],
)
def test_fatal_names_blocked(name):
    verdict = core.classify_name(name)
    assert verdict.verdict == core.VERDICT_FATAL
    assert verdict.blocked


@pytest.mark.parametrize("name", ["dwm.exe", "explorer", "TaskMgr.exe"])
def test_risky_names_need_confirm(name):
    verdict = core.classify_name(name)
    assert verdict.verdict == core.VERDICT_RISKY
    assert verdict.needs_confirm
    assert not verdict.blocked


@pytest.mark.parametrize("name", ["notepad.exe", "chrome", "FIFAOnline4.exe"])
def test_normal_names_pass(name):
    verdict = core.classify_name(name)
    assert verdict.verdict == core.VERDICT_OK
    assert not verdict.blocked and not verdict.needs_confirm


def test_empty_name_blocked():
    verdict = core.classify_name("   ")
    assert verdict.verdict == core.VERDICT_EMPTY
    assert verdict.blocked


def test_fatal_list_is_lowercase_and_normalized():
    # 목록이 대문자로 섞이면 classify_name 이 조용히 통과시킨다.
    for name in core.FATAL_NAMES | core.RISKY_NAMES:
        assert name == name.lower(), name
        assert name.strip() == name, name


# ─── 유지 시간 파싱 ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [("10", 10.0), ("10초", 10.0), (" 2.5 ", 2.5), ("1s", 1.0), ("30 sec", 30.0)],
)
def test_parse_hold_seconds_ok(text, expected):
    value, error = core.parse_hold_seconds(text)
    assert value == expected and error == ""


def test_parse_hold_seconds_defaults_on_empty():
    assert core.parse_hold_seconds("", default=7.0) == (7.0, "")
    assert core.parse_hold_seconds(None, default=7.0) == (7.0, "")


@pytest.mark.parametrize("text", ["abc", "-5", "10 20", "1e5", "０１０"])
def test_parse_hold_seconds_rejects_garbage(text):
    value, error = core.parse_hold_seconds(text)
    assert value is None and error


@pytest.mark.parametrize("text", ["0", "0.05", "601", "99999"])
def test_parse_hold_seconds_enforces_range(text):
    value, error = core.parse_hold_seconds(text)
    assert value is None and error


def test_format_remaining_clamps_negative():
    assert core.format_remaining(-3.0) == "0.0초"
    assert core.format_remaining(9.44) == "9.4초"


# ─── 대상 선택 ─────────────────────────────────────────────────────────────

PROCS = [
    (100, "notepad.exe"),
    (101, "notepad.exe"),
    (102, "chrome.exe"),
    (103, "Notepad.exe"),
    (999, "python.exe"),
]


def test_select_targets_finds_all_matches():
    assert core.select_targets("notepad", PROCS) == [100, 101, 103]


def test_select_targets_is_extension_agnostic():
    assert core.select_targets("notepad.exe", PROCS) == core.select_targets("notepad", PROCS)


def test_select_targets_excludes_self():
    assert core.select_targets("python", PROCS, exclude_pids=[999]) == []


def test_select_targets_empty_when_no_match():
    assert core.select_targets("nothing", PROCS) == []


# ─── 상태 머신 ─────────────────────────────────────────────────────────────

def test_busy_states_block_restart():
    for state in (core.STATE_SUSPENDING, core.STATE_HELD, core.STATE_RESUMING):
        assert not core.can_start(state)
    for state in (core.STATE_IDLE, core.STATE_DONE, core.STATE_FAILED):
        assert core.can_start(state)

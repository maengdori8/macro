"""마무리 계층이 실제로 연결돼 있고, 루프가 의도대로 도는지 검증한다.

기존 러너 테스트는 테스트가 만든 가짜 콜러블을 주입해서, **진짜 훅**
(followup.prepare / followup.after_resume)은 한 줄도 실행되지 않았다.
실측으로 확인한 결과 gui 의 배선을 통째로 지워도 전체 스위트가 초록색이었다.
여기서는 (1) 배선 (2) 호출 규약 (3) 결과 코드 표 (4) 실제 루프를 고정한다.

Windows 없이 돌리기 위해 화면/입력 계층만 가짜로 바꾸고, followup.run 의
판정 흐름 자체는 진짜를 돌린다.
"""

from __future__ import annotations

import inspect
import threading
import time

import pytest

from mpauseapp import followup, press, runner as runner_mod, screen


# ─── 1) 배선 · 호출 규약 · 결과 코드 표 ────────────────────────────────────

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    yield window
    try:
        window.destroy()
    except Exception:
        pass


def test_gui_wires_the_real_hooks(root):
    """배선이 빠지면 이번 변경의 핵심 기능이 통째로 사라진다."""
    from mpauseapp.gui import PauseApp

    app = PauseApp(root, preview=True)
    assert app.runner._prepare is followup.prepare
    assert app.runner._after_resume is followup.after_resume


def test_app_close_releases_the_pad(root, monkeypatch):
    """패드는 실행 중엔 유지하고(mAuto 와 동일), 앱을 닫을 때 뽑는다."""
    from mpauseapp.gui import PauseApp

    released = []
    monkeypatch.setattr(press, "release_pad", lambda: released.append(True))
    app = PauseApp(root, preview=True)
    app._on_close()
    assert released, "앱 종료 경로가 패드를 뽑지 않았다"


def test_hooks_match_the_signature_the_runner_calls_with():
    """러너는 위치 인자로 부른다 — 시그니처가 어긋나면 조용히 '실패'가 된다."""
    inspect.signature(followup.prepare).bind(threading.Event())
    inspect.signature(followup.after_resume).bind(
        threading.Event(), None, lambda progress: None
    )


def test_every_result_code_has_a_message():
    """결과 코드와 문구 표가 어긋나면 실패가 '완료되었습니다.'로 보고된다."""
    codes = {
        value
        for name, value in vars(followup).items()
        if name.startswith("RESULT_") and isinstance(value, str)
    }
    assert codes, "결과 코드를 못 찾았다"
    assert codes == set(runner_mod._FOLLOWUP_MESSAGES), (
        f"코드 표 불일치: {codes ^ set(runner_mod._FOLLOWUP_MESSAGES)}"
    )


# ─── 2) 실제 루프 (화면·입력만 가짜) ───────────────────────────────────────


class FakeWatcher:
    """정해진 순서대로 프레임을 내주는 가짜 화면.

    목록이 떨어지면 **마지막 화면을 계속 유지한다** — 실제 게임도 화면이 바뀌기
    전까지는 같은 그림을 계속 보여 준다. 목록이 처음부터 비어 있으면 새 프레임이
    없는 상태(None)를 흉내 낸다.
    """

    def __init__(self, frames):
        self.hwnd = 4242
        self._frames = list(frames)
        self._last = None
        self.stopped = False

    def start(self):
        return True

    def stop(self):
        self.stopped = True

    def alive(self):
        return True

    def frame(self, timeout=0.1):
        if self._frames:
            self._last = self._frames.pop(0)
        return self._last

    def to_client(self, x, y):
        return int(x), int(y)

    def client_size(self):
        return (1000, 800)


@pytest.fixture
def fake_stack(monkeypatch):
    """screen/press 를 가짜로 바꾸고, 무슨 일이 있었는지 기록한다."""
    log: list[str] = []

    class FakeTemplate:
        gray = None

    monkeypatch.setattr(screen, "load_template", lambda threshold: FakeTemplate())
    monkeypatch.setattr(screen, "window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(
        screen, "wait_for_window", lambda timeout, cancel=None, poll=0.25: 4242
    )
    # '보임' 프레임은 문자열 '보임' 으로 표현한다.
    monkeypatch.setattr(
        screen, "locate", lambda frame, template: (((10, 20), 0.95) if frame == "보임" else (None, 0.1))
    )
    monkeypatch.setattr(
        press, "curved_click", lambda hwnd, start, end: log.append("click") or True
    )
    monkeypatch.setattr(press, "pad_press", lambda name: log.append(f"pad:{name}") or True)
    monkeypatch.setattr(press, "fake_focus", lambda hwnd: log.append("focus") or True)
    monkeypatch.setattr(press, "release_pad", lambda: log.append("release"))
    # 진짜 가상 패드가 만들어지면 테스트 PC 장치 목록에 컨트롤러가 꽂힌다.
    # 기록도 남긴다 — '열기 전에 미리 꽂는다'는 순서를 양성 단언으로 고정하기 위함.
    monkeypatch.setattr(press, "ensure_pad", lambda: log.append("ensure") or True)
    monkeypatch.setattr(followup, "FOLLOWUP_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(followup, "FOLLOWUP_VERIFY_SECONDS", 0.05)
    monkeypatch.setattr(followup, "FOLLOWUP_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(followup, "FOLLOWUP_LOOP_SLEEP_SECONDS", 0.0)
    monkeypatch.setattr(followup, "FOLLOWUP_OPEN_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(followup, "FOLLOWUP_OPEN_RETRY_SECONDS", 0.3)
    monkeypatch.setattr(followup, "FOLLOWUP_MAX_OPENS", 2)
    # 일반 루프 테스트는 감지 즉시 누르는 종전 동작으로 고정한다.
    # 첫 누름 대기는 전용 테스트에서 0이 아닌 값으로 검증한다.
    monkeypatch.setattr(followup, "FOLLOWUP_FIRST_PRESS_DELAY_SECONDS", 0.0)
    return log


def use_frames(monkeypatch, frames):
    watcher = FakeWatcher(frames)
    monkeypatch.setattr(screen, "Watcher", lambda hwnd, logger=None: watcher)
    return watcher


def test_press_then_disappear_is_success(fake_stack, monkeypatch):
    log = fake_stack
    watcher = use_frames(monkeypatch, ["보임", "보임", "빈화면"])  # 이후 빈화면 유지
    result = followup.run(threading.Event(), hwnd=4242)
    assert result == followup.RESULT_OK
    assert log.count("click") == 1, f"눌린 횟수가 이상하다: {log}"
    assert watcher.stopped, "캡처를 정리하지 않았다"
    # 패드는 실행이 끝나도 뽑지 않는다(mAuto 와 동일 — 앱 종료 시에만 뽑는다).
    # 매 실행 꽂았다 뽑으면 게임이 장치를 매번 다시 잡아야 한다.
    assert "release" not in log, "실행 중에 패드를 뽑았다"


def test_still_visible_switches_to_the_pad(fake_stack, monkeypatch):
    """첫 방법이 안 먹으면 두 번째 방법으로 바꿔서 다시 눌러야 한다."""
    log = fake_stack
    use_frames(monkeypatch, ["보임"] * 60)
    monkeypatch.setattr(followup, "FOLLOWUP_MAX_PRESSES", 2)
    result = followup.run(threading.Event(), hwnd=4242)
    assert result == followup.RESULT_FAILED
    presses = [entry for entry in log if entry == "click" or entry.startswith("pad:")]
    assert presses[0] == "click"
    assert presses[1].startswith("pad:"), f"패드로 넘어가지 않았다: {log}"
    # 확정 패드 입력도 가짜 포커스가 앞서야 한다(배경 게임은 없으면 무시).
    pad_index = next(i for i, entry in enumerate(log) if entry.startswith("pad:"))
    assert "focus" in log[:pad_index], f"패드 입력 전에 가짜 포커스가 없다: {log}"


def test_opened_but_never_appearing_reports_no_show(fake_stack, monkeypatch):
    """열기까지 했는데 끝내 안 떴다 = no_show — '완료'로 붕괴하면 열기 실패
    (버튼 오매핑·장치 미인식·패드 런타임 부재)가 영영 관측되지 않는다."""
    log = fake_stack
    use_frames(monkeypatch, [])
    result = followup.run(threading.Event(), hwnd=4242)
    assert result == followup.RESULT_NO_SHOW
    # 그림을 띄우려는 열기 입력은 나가지만, 확정 입력(click / 확정 패드)은 없어야 한다.
    assert "click" not in log
    assert f"pad:{followup.FOLLOWUP_PAD_BUTTON}" not in log
    open_entry = f"pad:{followup.FOLLOWUP_OPEN_BUTTON}"
    assert log.count(open_entry) == 2, log  # max_opens 만큼
    # 패드는 첫 열기 입력보다 먼저 꽂혀 있어야 한다(장치 인식 여유 — 설계 의도).
    assert "ensure" in log, "패드를 미리 꽂지 않았다"
    assert log.index("ensure") < log.index(open_entry)
    # 게임은 활성일 때만 패드를 읽는다 — 열기마다 가짜 포커스가 앞서야 한다
    # (mAuto dispatch_key_press 와 동일. 빠지면 배경 게임이 START 를 무시한다).
    assert log.count("focus") == 2, log
    assert log.index("focus") < log.index(open_entry)


def test_open_press_reveals_the_target(fake_stack, monkeypatch):
    """빈 화면 → 열기 입력 → 그림 등장 → 확정 → 사라짐 = 성공."""
    log = fake_stack
    watcher = use_frames(monkeypatch, ["빈화면"])

    def pad(name):
        log.append(f"pad:{name}")
        if name == followup.FOLLOWUP_OPEN_BUTTON:
            # 열기 입력이 들어가야 비로소 그림이 뜬다(사용자 실측 흐름).
            watcher._frames.extend(["보임", "보임", "빈화면"])
        return True

    monkeypatch.setattr(press, "pad_press", pad)
    result = followup.run(threading.Event(), hwnd=4242)
    assert result == followup.RESULT_OK
    open_entry = f"pad:{followup.FOLLOWUP_OPEN_BUTTON}"
    assert log.count(open_entry) == 1, f"열기가 반복됐다(토글 위험): {log}"
    assert log.index(open_entry) < log.index("click"), f"열기 전에 눌렀다: {log}"


def test_open_is_skipped_when_the_pad_method_is_dropped(fake_stack, monkeypatch):
    """감독모드 도구가 같이 돌 때(패드 제외)는 열기 입력도, 패드 생성도 없어야 한다."""
    log = fake_stack
    use_frames(monkeypatch, [])
    created = []
    monkeypatch.setattr(press, "ensure_pad", lambda: created.append(True) or True)
    result = followup.run(threading.Event(), hwnd=4242, methods=("click",))
    assert result == followup.RESULT_QUIET
    assert not any(entry.startswith("pad") for entry in log), log
    assert not created, "패드 방법을 뺐는데 패드를 만들었다"
    assert "focus" not in log, "패드를 안 쓰는데 가짜 포커스를 보냈다"


def test_cancel_stops_immediately(fake_stack, monkeypatch):
    use_frames(monkeypatch, ["보임"] * 10)
    cancel = threading.Event()
    cancel.set()
    assert followup.run(cancel, hwnd=4242) == followup.RESULT_CANCELLED


def test_missing_window_is_reported(fake_stack, monkeypatch):
    use_frames(monkeypatch, [])
    monkeypatch.setattr(screen, "window_alive", lambda hwnd: False)
    monkeypatch.setattr(screen, "wait_for_window", lambda timeout, cancel=None, poll=0.25: None)
    assert followup.run(threading.Event(), hwnd=None) == followup.RESULT_NO_WINDOW


def test_release_pad_frees_exactly_once(monkeypatch):
    """해제는 정확히 한 번이어야 한다 — 두 번이면 네이티브 이중 해제다."""
    calls: list[str] = []

    class FakePad:
        def reset(self):
            calls.append("reset")

        def update(self):
            calls.append("update")

        def __del__(self):
            calls.append("del")

    monkeypatch.setattr(press, "_pad", FakePad())
    press.release_pad()
    press.release_pad()  # 두 번 불러도 안전해야 한다
    assert press._pad is None
    assert calls.count("del") == 1, calls
    assert calls[:2] == ["reset", "update"], "뽑기 전에 중립 상태를 안 보냈다"


def test_prepare_does_not_create_a_pad(monkeypatch):
    """준비 단계는 장치를 만들면 안 된다 — 쓰지도 않는 컨트롤러가 꽂힌다."""
    created = []
    monkeypatch.setattr(press, "ensure_pad", lambda: created.append(True) or True)
    monkeypatch.setattr(screen, "find_window", lambda: 777)
    monkeypatch.setattr(followup, "companion_running", lambda: False)
    plan = followup.prepare(threading.Event())
    assert plan.hwnd == 777
    assert not created, "준비 단계에서 패드를 만들었다"


# ─── 3) 감독모드 도구와 같이 돌 때 ─────────────────────────────────────────


def test_pad_is_dropped_while_the_companion_runs(monkeypatch):
    """패드가 이미 하나 꽂혀 있으면 또 만들지 않는다.

    가상 패드는 만든 프로세스만 쓸 수 있어 남의 것을 빌릴 수 없고, 두 개가
    꽂히면 게임이 어느 쪽을 1번 컨트롤러로 볼지 달라진다.
    """
    monkeypatch.setattr(screen, "find_window", lambda: 777)
    monkeypatch.setattr(followup, "companion_running", lambda: True)
    plan = followup.prepare(threading.Event())
    assert "pad" not in plan.methods
    assert plan.methods == ("click",)


def test_companion_detection_ignores_our_own_process(monkeypatch):
    from mpauseapp import winproc

    monkeypatch.setattr(winproc, "current_pid", lambda: 999)
    monkeypatch.setattr(winproc, "list_processes", lambda: [(999, "macro.exe")])
    assert followup.companion_running() is False, "자기 자신을 상대로 오인했다"

    monkeypatch.setattr(winproc, "list_processes", lambda: [(12, "macro.exe")])
    assert followup.companion_running() is True

    monkeypatch.setattr(winproc, "list_processes", lambda: [(12, "notepad.exe")])
    assert followup.companion_running() is False


def test_companion_detection_survives_enumeration_failure(monkeypatch):
    from mpauseapp import winproc

    def boom():
        raise winproc.ProcessControlError("안 됨", reason="enumerate")

    monkeypatch.setattr(winproc, "list_processes", boom)
    assert followup.companion_running() is False


def test_run_uses_the_methods_from_the_plan(fake_stack, monkeypatch):
    """계획에서 패드를 뺐으면 루프도 패드를 쓰면 안 된다."""
    log = fake_stack
    use_frames(monkeypatch, ["보임"] * 200)
    monkeypatch.setattr(followup, "FOLLOWUP_MAX_PRESSES", 3)
    result = followup.run(threading.Event(), hwnd=4242, methods=("click",))
    assert result == followup.RESULT_FAILED
    assert not any(entry.startswith("pad") for entry in log), log
    assert log.count("click") == 3


def test_after_resume_passes_the_plan_methods_to_run(fake_stack, monkeypatch):
    """러너의 실전 경로(after_resume→run)가 계획의 methods 를 그대로 쓰는지 고정한다.

    이 전달이 끊기면 run() 이 기본 methods(pad 포함)로 폴백해, 감독모드 도구
    동행 시에도 패드가 만들어진다 — 적대적 리뷰에서 변이 생존으로 확정된 구멍
    (패드 2개 방지 정책이 실전 경로에서 무보호였다).
    """
    log = fake_stack
    use_frames(monkeypatch, [])
    created = []
    monkeypatch.setattr(press, "ensure_pad", lambda: created.append(True) or True)
    plan = followup.Plan(hwnd=4242, methods=("click",))
    result = followup.after_resume(threading.Event(), plan, lambda progress: None)
    assert result == followup.RESULT_QUIET
    assert not any(entry.startswith("pad") for entry in log), log
    assert not created, "계획에서 패드를 뺐는데 실전 경로가 패드를 만들었다"


def test_run_wires_the_config_into_the_sequence(fake_stack, monkeypatch):
    """시간 상수(열기 지연·간격·횟수, verify 등)가 판정기에 그대로 배선되는지 고정한다.

    core 단위 테스트는 kwargs 를 직접 넣으므로, run() 쪽 배선이 끊겨도(예: 0.0
    하드코딩) 전체 스위트가 초록이었다 — 적대적 리뷰에서 변이 생존으로 확정된 구멍.
    열기 간격은 토글 방지 안전장치라 특히 중요하다.
    """
    captured = {}
    real = followup.core.ConfirmSequence

    def spy(started, **kwargs):
        captured.update(kwargs)
        return real(started, **kwargs)

    monkeypatch.setattr(followup.core, "ConfirmSequence", spy)
    use_frames(monkeypatch, [])
    followup.run(threading.Event(), hwnd=4242)
    assert captured["open_delay_seconds"] == followup.FOLLOWUP_OPEN_DELAY_SECONDS
    assert captured["open_retry_seconds"] == followup.FOLLOWUP_OPEN_RETRY_SECONDS
    assert captured["max_opens"] == followup.FOLLOWUP_MAX_OPENS
    assert captured["verify_seconds"] == followup.FOLLOWUP_VERIFY_SECONDS
    assert captured["settle_seconds"] == followup.FOLLOWUP_SETTLE_SECONDS
    assert captured["timeout_seconds"] == followup.FOLLOWUP_TIMEOUT_SECONDS
    assert captured["max_presses"] == followup.FOLLOWUP_MAX_PRESSES
    assert captured["first_press_delay_seconds"] == followup.FOLLOWUP_FIRST_PRESS_DELAY_SECONDS


def test_first_press_delay_is_wired_into_the_loop(fake_stack, monkeypatch):
    """감지 즉시 누르지 않고 설정한 대기 뒤에 누른다(실제 루프 기준)."""
    log = fake_stack
    watcher = use_frames(monkeypatch, ["보임"])  # 이후 계속 보임
    monkeypatch.setattr(followup, "FOLLOWUP_FIRST_PRESS_DELAY_SECONDS", 0.2)

    started = time.monotonic()
    clicked_at = {}

    def click(hwnd, start, end):
        clicked_at.setdefault("t", time.monotonic() - started)
        log.append("click")
        watcher._frames.extend(["빈화면"])  # 눌렀으니 사라진다
        return True

    monkeypatch.setattr(press, "curved_click", click)
    result = followup.run(threading.Event(), hwnd=4242)
    assert result == followup.RESULT_OK
    # 감지는 사실상 시작 직후다 — 누름이 대기(0.2초)보다 빨랐다면 배선이 끊긴 것.
    assert clicked_at["t"] >= 0.18, clicked_at


def test_configured_buttons_exist_in_the_pad_table(monkeypatch):
    """config 의 버튼 이름이 press 의 표에 실재해야 한다 — 오타는 조용한 no-op 이 된다.

    pad_press 는 모르는 이름이면 False 만 돌려주고 아무 입력도 안 나간다.
    FOLLOWUP_OPEN_BUTTON 은 실측 중 교체될 예정(config 주석의 "rs" 안내)이라
    특히 오타 위험이 크다.
    """
    import types

    from mpauseapp import config, deps

    names = (
        "A", "B", "X", "Y", "START", "BACK",
        "LEFT_SHOULDER", "RIGHT_SHOULDER", "LEFT_THUMB", "RIGHT_THUMB",
        "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    )
    table = types.SimpleNamespace(**{f"XUSB_GAMEPAD_{name}": name for name in names})
    monkeypatch.setattr(deps, "vg", types.SimpleNamespace(XUSB_BUTTON=table))
    assert press._button(config.FOLLOWUP_OPEN_BUTTON) is not None
    assert press._button(config.FOLLOWUP_PAD_BUTTON) is not None


def test_fake_focus_posts_wm_activate(monkeypatch):
    """가짜 포커스는 WM_ACTIVATE(0x0006)/WA_ACTIVE(1) 그대로여야 한다 —
    mAuto dispatch_key_press 가 실측으로 검증한 규격이다."""
    from mpauseapp import deps

    calls = []

    class FakeGui:
        def IsWindow(self, hwnd):
            return True

        def PostMessage(self, hwnd, message, wparam, lparam):
            calls.append((hwnd, message, wparam, lparam))

    monkeypatch.setattr(deps, "win32gui", FakeGui())
    assert press.fake_focus(4242) is True
    assert calls == [(4242, 0x0006, 1, 0)]

    # 창이 없거나(hwnd=None) win32 가 없으면 조용히 False.
    assert press.fake_focus(None) is False
    monkeypatch.setattr(deps, "win32gui", None)
    assert press.fake_focus(4242) is False

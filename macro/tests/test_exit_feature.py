"""프로 전용 즉시 종료 — 티어 잠금과 배선을 고정한다.

여기서 막으려는 사고 세 가지:
  1. 일반 키에서 기능이 열린다(= 티어가 무의미해진다)
  2. 배선이 빠져 버튼이 죽은 채로 배포된다(테스트는 초록불)
  3. 자동화 루프와 동시에 돌아 같은 화면을 둘이 누른다
"""

from __future__ import annotations

import inspect
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp import edition, exit_core, exit_followup, exit_runner  # noqa: E402

tk = pytest.importorskip("tkinter")


def make_app(product: str, **kwargs):
    """지정한 제품으로 앱을 만든다(진입점이 하는 일을 테스트에서 재현)."""
    from macroapp.gui import AutomationApp

    edition.set_product(product)
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # 디스플레이 없는 환경
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    instance = AutomationApp(root, preview=True, **kwargs)
    root.update_idletasks()
    return root, instance


@pytest.fixture(autouse=True)
def restore_edition():
    """제품 전역값이 다른 테스트로 새지 않게 되돌린다."""
    yield
    edition.set_product(edition.PRODUCT_STANDARD)


@pytest.fixture
def app():
    """프로 빌드 — 이 기능이 존재하는 쪽."""
    root, instance = make_app(edition.PRODUCT_PRO)
    yield instance
    try:
        root.destroy()
    except Exception:
        pass


# ─── 티어 잠금 ─────────────────────────────────────────────────────────────


def test_standard_build_has_no_such_button():
    """일반 프로그램에는 기능이 **없다** — 잠근 게 아니라 아예 없다."""
    root, instance = make_app(edition.PRODUCT_STANDARD)
    try:
        assert instance.exit_button is None
        assert instance._is_pro is False
        # 눌릴 버튼이 없으니 호출돼도 아무 일이 없어야 한다.
        instance.run_quick_exit()
        assert instance._exit_runner is None
        assert not instance._exit_gate.is_set()
    finally:
        root.destroy()


def test_pro_key_in_standard_build_still_unlocks_nothing():
    """프로 키를 일반 프로그램에 넣어도 그 기능은 일반 빌드에 없다."""
    root, instance = make_app(edition.PRODUCT_STANDARD)
    try:
        instance._apply_tier({"valid": True, "pro": True})
        assert instance._is_pro is False
        assert instance.exit_button is None
    finally:
        root.destroy()


def test_standard_key_leaves_the_feature_locked(app):
    app._apply_tier({"valid": True, "pro": False})
    assert app._is_pro is False


def test_pro_key_unlocks_the_feature(app):
    app._apply_tier({"valid": True, "pro": True})
    assert app._is_pro is True


def test_missing_pro_flag_is_treated_as_standard(app):
    """fail-closed — 알 수 없으면 잠근다."""
    app._apply_tier({"valid": True})
    assert app._is_pro is False


# ─── 서버 운영 비율(0:2 자동 종료) 반영 ────────────────────────────────────
# license_client 가 파싱한 auto_exit_ratio 가 쿼터까지 실제로 닿는지(배선)와,
# 프로 티어 아님·값 없음·불량 값에서 조용히 기본값이 유지되는지를 고정한다.


def test_verified_ratio_reaches_the_quota(app):
    app._apply_tier({"valid": True, "pro": True, "auto_exit_ratio": 0.7})
    assert app._exit_quota.ratio == 0.7


def test_ratio_without_pro_tier_is_ignored(app):
    """일반 티어 응답에 비율이 실려 와도 무시된다(프로 전용 운영값)."""
    app._apply_tier({"valid": True, "pro": False, "auto_exit_ratio": 0.7})
    assert app._exit_quota.ratio == 0.4


def test_missing_ratio_keeps_the_last_value(app):
    """재검증 응답에 비율이 빠져도(서버 읽기 실패) 마지막 값을 유지한다."""
    app._apply_tier({"valid": True, "pro": True, "auto_exit_ratio": 0.7})
    app._apply_tier({"valid": True, "pro": True})
    assert app._exit_quota.ratio == 0.7


def test_bad_ratio_does_not_break_tier_application(app):
    """방어선 이중화 — license_client 를 우회해 불량 값이 와도 티어 반영은 산다."""
    app._apply_tier({"valid": True, "pro": True, "auto_exit_ratio": 5.0})
    assert app._is_pro is True
    assert app._exit_quota.ratio == 0.4


def test_locked_feature_does_not_start_a_worker(app):
    app._apply_tier({"valid": True, "pro": False})
    app.run_quick_exit()
    assert app._exit_runner is None, "일반 키인데 워커가 만들어졌다"
    assert not app._exit_gate.is_set(), "일반 키인데 자동화 루프가 멈췄다"


def test_response_field_alone_cannot_unlock(app):
    """응답 본문의 product 로는 안 열린다 — 서명으로 확인된 pro 만 본다."""
    app._apply_tier({"valid": True, "product": "macro_pro", "pro": False})
    assert app._is_pro is False


def test_button_is_locked_before_any_verification(app):
    """확인 전에는 잠겨 있어야 하고, 수동 버튼은 프로 빌드에도 **없다**.

    즉시 종료는 2점차 자동 종료로만 실행된다(사용자 결정, 2026-08-17). 버튼이
    되살아나면 이 테스트가 막는다 — UI 를 고치다 무심코 다시 넣는 사고 방지.
    """
    assert app._is_pro is False
    assert app.exit_button is None, "즉시 종료 버튼은 제거됐다(자동 전용)"


def test_verified_pro_key_unlocks_without_pressing_start():
    """프로 고객이 '시작'을 누르기 전에도 티어가 반영돼야 한다."""
    root, instance = make_app(
        edition.PRODUCT_PRO,
        license_info={"valid": True, "pro": True, "remaining_seconds": 3600, "days": 1},
    )
    try:
        assert instance._is_pro is True
    finally:
        root.destroy()


def test_expired_license_locks_the_feature(app):
    """시간권 프로 키 하나로 만료 뒤에도 계속 쓰이면 안 된다."""
    import time as _time

    app._apply_tier({"valid": True, "pro": True})
    app._license_deadline = _time.time() - 10   # 이미 만료
    app.run_quick_exit()
    assert app._exit_runner is None, "만료됐는데 워커가 시작됐다"
    assert app._is_pro is False, "만료 뒤에도 프로가 켜져 있다"


@pytest.mark.parametrize(
    "result", [{"_offline": True}, {"valid": False, "message": "만료"}]
)
def test_failed_reverification_locks_the_tier(app, result):
    """확인하지 못했으면 잠근다(fail-closed) — 한 번 켠 티어가 남으면 안 된다."""
    app._apply_tier({"valid": True, "pro": True})
    app._after_verify(result)
    assert app._is_pro is False


# ─── 0:2 자동 종료 배선 ────────────────────────────────────────────────────


def arm(app, monkeypatch):
    """자동 종료가 실행될 수 있는 상태를 만든다(프로 + 활성 + 자동화 생존)."""
    app._apply_tier({"valid": True, "pro": True})
    app._auto_exit_active = True
    app.stop_event.clear()
    # _run_auto_exit 는 자동화 워커 생존을 요구한다 — 살아 있는 스레드를 꽂는다.
    app.worker_thread = threading.current_thread()


def test_auto_exit_triggers_through_the_quota(app, monkeypatch):
    """패배 확정 → 쿼터 판정 → UI 큐로 종료 요청. 3번째 판에서 첫 종료가 나간다."""
    from macroapp import auto_exit as auto_exit_mod

    arm(app, monkeypatch)
    monkeypatch.setattr(
        auto_exit_mod, "read_score_from_frame", lambda *a, **k: (0, 2)
    )
    triggered = []
    monkeypatch.setattr(app, "run_quick_exit", lambda: triggered.append(True))

    for game in range(1, 4):
        # 경기마다 tracker 를 새로 — 실제로는 경기 종료 리셋이 하는 일이다.
        app._loss_tracker = auto_exit_mod.LossTracker(
            confirm_count=1, require_prior_score=False
        )
        app._observe_match_score(object(), float(game))

    assert app._exit_quota.lost_games == 3
    assert app._exit_quota.exits_done == 1, "40% 쿼터면 3번째에 첫 종료"
    # 큐 메시지가 UI 스레드에서 실제로 종료를 부르는지까지 본다.
    app._poll_ui_queue()
    assert triggered == [True]


def test_auto_exit_observation_stops_while_the_gate_is_up(app, monkeypatch):
    """이미 종료가 도는 동안의 프레임은 세지 않는다(자기 자신을 재트리거 금지)."""
    from macroapp import auto_exit as auto_exit_mod

    arm(app, monkeypatch)
    app._loss_tracker = auto_exit_mod.LossTracker(
        confirm_count=1, require_prior_score=False
    )
    monkeypatch.setattr(
        auto_exit_mod, "read_score_from_frame", lambda *a, **k: (0, 2)
    )
    app._exit_gate.set()
    app._observe_match_score(object(), 1.0)
    assert app._exit_quota.lost_games == 0


def test_auto_exit_is_wgc_only(app, monkeypatch):
    """region 캡처 모드에서는 켜지지 않는다(리뷰 확정 결함).

    region 모드는 프레임이 게임 클라이언트가 아니라 임의 사각형이고, 정지 화면도
    매번 새 seq 를 받아 '연속 3회 확인'이 같은 픽셀 재판독으로 채워진다."""
    from macroapp import ocr as rank_ocr_mod

    monkeypatch.setattr(rank_ocr_mod, "ocr_available", lambda: True)
    app._apply_tier({"valid": True, "pro": True})
    assert app._auto_exit_allowed("wgc") is True
    assert app._auto_exit_allowed("region") is False
    app._apply_tier({"valid": True, "pro": False})
    assert app._auto_exit_allowed("wgc") is False


def test_inactive_flag_blocks_observation(app, monkeypatch):
    """자동화 실행이 활성 판정을 안 내렸으면 관측 자체가 무시된다."""
    from macroapp import auto_exit as auto_exit_mod

    app._auto_exit_active = False
    monkeypatch.setattr(
        auto_exit_mod, "read_score_from_frame", lambda *a, **k: (0, 2)
    )
    app._loss_tracker = auto_exit_mod.LossTracker(
        confirm_count=1, require_prior_score=False
    )
    app._observe_match_score(object(), 1.0)
    assert app._exit_quota.lost_games == 0


def test_stale_trigger_after_stop_is_ignored(app):
    """정지 후 큐에 남은 자동 종료 요청은 실행되지 않는다(리뷰 확정 결함)."""
    called = []
    app.run_quick_exit = lambda: called.append(True)
    app._apply_tier({"valid": True, "pro": True})

    app.stop_event.set()                        # 정지된 상태
    app.worker_thread = threading.current_thread()
    app._run_auto_exit()
    assert called == [], "정지 후에 종료가 실행됐다"

    app.stop_event.clear()
    app.worker_thread = None                    # 자동화를 돌린 적 없음
    app._run_auto_exit()
    assert called == [], "자동화가 없는데 종료가 실행됐다"


def test_observation_is_dropped_when_stop_arrives_mid_ocr(app, monkeypatch):
    """OCR 호출 도중 정지가 눌리면 확정을 큐에 넣지 않는다."""
    from macroapp import auto_exit as auto_exit_mod

    arm(app, monkeypatch)
    app._loss_tracker = auto_exit_mod.LossTracker(
        confirm_count=1, require_prior_score=False
    )

    def read_and_stop(*a, **k):
        app.stop_event.set()                    # winocr 도중 정지 버튼
        return (0, 2)

    monkeypatch.setattr(auto_exit_mod, "read_score_from_frame", read_and_stop)
    app._observe_match_score(object(), 1.0)
    assert app.ui_queue.empty(), "정지 후에 종료 요청이 큐에 남았다"


def test_auto_exit_request_is_ignored_without_pro(app):
    """큐에 요청이 남아 있어도 프로가 아니면 실행하지 않는다(fail-closed)."""
    called = []
    app.run_quick_exit = lambda: called.append(True)
    app._is_pro = False
    app._run_auto_exit()
    assert called == []


# ─── 배선 ──────────────────────────────────────────────────────────────────


def test_runner_hooks_match_the_expected_signatures():
    inspect.signature(exit_followup.prepare).bind(threading.Event())
    inspect.signature(exit_followup.after_resume).bind(
        threading.Event(), None, lambda progress: None
    )


def test_every_result_code_has_a_message():
    codes = {
        value
        for name, value in vars(exit_followup).items()
        if name.startswith("RESULT_") and isinstance(value, str)
    }
    assert codes, "결과 코드를 못 찾았다"
    assert codes == set(exit_runner._FOLLOWUP_MESSAGES), (
        f"코드 표 불일치: {codes ^ set(exit_runner._FOLLOWUP_MESSAGES)}"
    )


def test_runner_uses_the_fixed_target_and_hold():
    from macroapp.config import EXIT_HOLD_SECONDS, EXIT_TARGET_PROCESS_NAME

    assert exit_runner.TARGET_PROCESS_NAME == EXIT_TARGET_PROCESS_NAME
    assert exit_runner.HOLD_SECONDS == EXIT_HOLD_SECONDS


def test_followup_passes_every_timing_to_the_sequence(monkeypatch):
    """ConfirmSequence 에 넘기는 타이밍 인자를 고정한다.

    ⚠️ 이 테스트가 있는 이유: first_press_delay_seconds 를 **안 넘기면 기본값 0**
    이라 그림을 보자마자 눌러 버린다. mPause 에는 있던 배선이 macro 로 옮길 때
    조용히 빠져 있었고(2026-08-17 실측), 인자 하나가 사라져도 아무 데서도 안 터졌다.
    """
    from macroapp import exit_core, exit_followup
    from macroapp.config import (
        EXIT_FIRST_PRESS_DELAY_SECONDS,
        EXIT_OPEN_DELAY_SECONDS,
        EXIT_OPEN_RETRY_SECONDS,
        EXIT_SETTLE_SECONDS,
        EXIT_VERIFY_SECONDS,
    )

    captured = {}
    real = exit_core.ConfirmSequence

    def spy(started_at, **kwargs):
        captured.update(kwargs)
        return real(started_at, **kwargs)

    monkeypatch.setattr(exit_core, "ConfirmSequence", spy)

    class _Target:
        name = "target_H"
        threshold = 0.8
        image_gray = None
        _last_match = None
        _last_match_misses = 0

    class _Manager:
        def find_window(self):
            return True

        def capture_client_area(self):
            return None

        def stop_capture(self):
            pass

    cancel = threading.Event()
    cancel.set()   # 첫 판정 직전에 빠져나온다 - 입력은 한 번도 나가지 않는다
    exit_followup.run(cancel, manager=_Manager(), targets=[_Target()])

    assert captured.get("first_press_delay_seconds") == EXIT_FIRST_PRESS_DELAY_SECONDS
    assert captured.get("open_delay_seconds") == EXIT_OPEN_DELAY_SECONDS
    assert captured.get("open_retry_seconds") == EXIT_OPEN_RETRY_SECONDS
    assert captured.get("verify_seconds") == EXIT_VERIFY_SECONDS
    assert captured.get("settle_seconds") == EXIT_SETTLE_SECONDS


def test_exit_timings_match_the_user_spec():
    """사용자 실측 요구: 되살아나면 곧바로 열고, 그림을 본 뒤 3초 기다렸다 누른다."""
    from macroapp.config import EXIT_FIRST_PRESS_DELAY_SECONDS, EXIT_OPEN_DELAY_SECONDS

    assert EXIT_OPEN_DELAY_SECONDS == 0.0, "재개 직후 바로 눌러야 한다"
    assert EXIT_FIRST_PRESS_DELAY_SECONDS == 3.0, "그림을 본 뒤 3초 대기"


# ─── 자동화 루프와의 배타 ──────────────────────────────────────────────────


def test_gate_pauses_the_automation_loop(app, monkeypatch):
    """도는 동안 루프가 멈추고, 끝나면 저절로 이어져야 한다."""
    started = threading.Event()

    class FakeRunner:
        busy = False   # 시작 전에는 놀고 있어야 run_quick_exit 가 진행한다

        def start(self, *args, **kwargs):
            started.set()
            return True

        def drain(self, handler, limit=64):
            # 종료 상태를 한 번에 올려 마무리 경로를 태운다.
            handler(exit_runner.Event(exit_runner.EVT_STATE, state=exit_core.STATE_DONE))

    app._apply_tier({"valid": True, "pro": True})
    app._exit_runner = FakeRunner()
    app.run_quick_exit()

    assert started.is_set()
    # drain 이 종료 상태를 올렸으므로 게이트가 풀려 있어야 한다.
    assert not app._exit_gate.is_set(), "끝났는데 자동화 루프가 계속 멈춰 있다"


def test_both_threads_check_the_gate():
    """⚠️ 입력을 내보내는 스레드는 **둘**이다 — 매칭 루프와 OCR/SKIP 워커.

    매칭 루프만 막으면 SKIP 워커가 0.3초마다 패드를 계속 눌러, 마무리 단계의
    확인 입력과 겹쳐 다음 화면까지 넘어간다. 두 곳 모두 게이트를 봐야 한다.
    """
    source = (ROOT / "macroapp" / "gui.py").read_text(encoding="utf-8")
    assert source.count("if self._exit_gate.is_set():") >= 2, (
        "게이트 검사가 한 곳뿐이다 — 입력을 내보내는 다른 스레드가 안 막힌다"
    )


def test_ocr_worker_makes_no_input_while_the_gate_is_up(app, monkeypatch):
    """게이트가 선 동안 SKIP 판정·입력이 한 번도 일어나지 않아야 한다."""
    import threading as _threading

    calls = []
    monkeypatch.setattr(app, "_try_skip", lambda *a, **k: calls.append("skip"))
    app._latest_frame = (1, object())
    app._ocr_manager = None
    app._exit_gate.set()
    app.stop_event = _threading.Event()

    worker = _threading.Thread(target=app._ocr_worker_loop, daemon=True)
    worker.start()
    _threading.Event().wait(0.3)     # 게이트가 선 동안 여러 주기를 돈다
    app.stop_event.set()
    worker.join(timeout=2)

    assert calls == [], f"게이트가 섰는데 입력 경로가 돌았다: {calls}"


def test_matching_loop_gate_runs_before_capture():
    """게이트 검사가 캡처·프레임 공개보다 **앞**이어야 한다.

    뒤에 있으면 정지된 화면을 계속 캡처해 OCR 워커에 공개하고, 마무리 단계와
    같은 WGC 엔진의 프레임을 나눠 갖게 된다(단일 소비자 구조라 서로 굶는다).
    """
    source = (ROOT / "macroapp" / "gui.py").read_text(encoding="utf-8")
    gate = source.index("if self._exit_gate.is_set():\n                    self.interruptible_sleep")
    capture = source.index("self._latest_frame = (self._frame_seq, screen_gray)")
    assert gate < capture, "게이트 검사가 캡처보다 뒤에 있다"


def test_second_press_is_ignored_while_busy(app):
    class BusyRunner:
        busy = True
        started = 0

        def start(self, *args, **kwargs):
            BusyRunner.started += 1
            return True

        def drain(self, handler, limit=64):
            pass

    app._apply_tier({"valid": True, "pro": True})
    app._exit_runner = BusyRunner()
    app.run_quick_exit()
    app.run_quick_exit()
    assert BusyRunner.started == 0, "이미 도는데 또 시작했다"


# ─── 서버 구매자별 종료 규칙(auto_exit_settings) 반영 ─────────────────────────
# 규칙은 트래커에, 비율은 쿼터에, late 비율은 별도 쿼터에 닿는지(배선)와
# 프로 아님·불량 필드에서 기본값이 유지되는지를 고정한다.


def test_verified_settings_reach_tracker_and_quotas(app):
    app._apply_tier({
        "valid": True, "pro": True,
        "auto_exit_settings": {
            "ratio": 0.5, "base_deficit": 2, "hard_deficit": 4,
            "late_minute": 75, "late_deficit": 1, "late_ratio": 1.0,
        },
    })
    rules = app._loss_tracker.rules
    assert (rules.base_deficit, rules.hard_deficit, rules.late_minute, rules.late_deficit) == (2, 4, 75, 1)
    assert app._exit_quota.ratio == 0.5
    assert app._late_quota is not None and app._late_quota.ratio == 1.0

    # late_ratio 를 빼면 별도 쿼터가 사라지고 base 쿼터를 같이 쓴다.
    app._apply_tier({"valid": True, "pro": True, "auto_exit_settings": {"ratio": 0.5}})
    assert app._late_quota is None
    # 보내지 않은 필드는 직전 값을 유지한다(필드 단위 fail-safe).
    assert app._loss_tracker.rules.hard_deficit == 4


def test_settings_without_pro_tier_are_ignored(app):
    app._apply_tier({"valid": True, "pro": False, "auto_exit_settings": {"hard_deficit": 9}})
    assert app._loss_tracker.rules.hard_deficit == 3


def test_bad_settings_keep_defaults_and_do_not_break_tier(app):
    app._apply_tier({"valid": True, "pro": True, "auto_exit_settings": {"hard_deficit": "x", "late_minute": -5}})
    assert app._is_pro is True
    assert app._loss_tracker.rules.hard_deficit == 3
    assert app._loss_tracker.rules.late_minute == 70
    app._apply_tier({"valid": True, "pro": True, "auto_exit_settings": "junk"})
    assert app._loss_tracker.rules.hard_deficit == 3


def test_default_rules_match_config(app):
    from macroapp import config

    rules = app._loss_tracker.rules
    assert rules.base_deficit == config.AUTO_EXIT_DEFICIT_GOALS
    assert rules.hard_deficit == config.AUTO_EXIT_HARD_DEFICIT_GOALS
    assert rules.late_minute == config.AUTO_EXIT_LATE_MINUTE
    assert rules.late_deficit == config.AUTO_EXIT_LATE_DEFICIT_GOALS
    assert app._late_quota is None


# ─── 마무리 단계의 타겟 로드 경로 ─────────────────────────────────────────────
# after_resume 는 targets 를 넘기지 않으므로 _load_target(None) 경로가 **항상** 돈다.
# 예전엔 존재하지 않는 이름을 import 해 ImportError 로 죽었고 러너가 '실패'로 삼켰다.


def test_followup_loads_the_exit_target_without_automation_targets():
    from macroapp import exit_followup

    target = exit_followup._load_target(None, lambda _m: None)
    assert target is not None, "마무리 단계가 타겟을 못 읽는다(import 경로 깨짐)"
    assert target.name == exit_followup.EXIT_TARGET_NAME
    assert target.threshold == exit_followup.EXIT_MATCH_THRESHOLD


# ─── 2026-08-23: 재개 직후 START 가 씹혀 종료가 통째로 실패하던 것 ─────────────


def test_start_is_spammed_until_the_confirm_button_appears() -> None:
    """확인 버튼이 보일 때까지 짧은 간격으로 START 를 연타한다(사용자 지시).

    예전엔 0·5·10초에 딱 3번이었다. 게임은 10초 얼어 있다가 막 깨어난 참이라 **재개 직후
    첫 START 가 자주 씹히고**, 그러면 다음 기회가 5초 뒤라 종료가 통째로 실패했다
    (실측 15:33~15:35 재시도 4회 연속 실패).
    """

    from macroapp import config, exit_core

    assert config.EXIT_OPEN_RETRY_SECONDS <= 0.6, "연타 간격이 너무 길다"
    assert config.EXIT_OPEN_RETRY_SECONDS >= 0.2, "너무 짧으면 화면 반응을 못 본다"
    assert config.EXIT_MAX_OPENS >= 30, "시한 동안 계속 누를 만큼 넉넉해야 한다"

    seq = exit_core.ConfirmSequence(
        0.0,
        methods=("click",),
        verify_seconds=config.EXIT_VERIFY_SECONDS,
        settle_seconds=config.EXIT_SETTLE_SECONDS,
        timeout_seconds=config.EXIT_TIMEOUT_SECONDS,
        max_presses=config.EXIT_MAX_PRESSES,
        open_delay_seconds=config.EXIT_OPEN_DELAY_SECONDS,
        open_retry_seconds=config.EXIT_OPEN_RETRY_SECONDS,
        max_opens=config.EXIT_MAX_OPENS,
        first_press_delay_seconds=config.EXIT_FIRST_PRESS_DELAY_SECONDS,
    )
    # 확인 버튼이 안 보이는 동안 10초에 몇 번 누르나 — 예전 설정이면 2~3번뿐이었다.
    opens = 0
    t = 0.0
    while t < 10.0:
        if seq.decide(t, False).action == "open":
            opens += 1
        t += 0.05
    assert opens >= 12, f"10초 동안 {opens}번밖에 안 눌렀다(연타가 아니다)"

    # ⚠️ 그림이 한 번이라도 보이면 더 누르지 않는다 — 열린 메뉴를 도로 닫으면 안 된다.
    before = opens
    seq.decide(t, True)
    t += 0.05
    extra = 0
    while t < 20.0:
        if seq.decide(t, False).action == "open":
            extra += 1
        t += 0.05
    assert extra == 0, f"확인 버튼을 본 뒤에도 {extra}번 더 열었다(토글로 닫힌다)"
    assert before > 0

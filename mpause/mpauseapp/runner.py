"""1회성 실행 워커 — 정지 → 유지 → 재개 → 마무리.

이 모듈의 유일한 불변식: **한 번이라도 정지시킨 대상은 반드시 다시 돌린다.**
정지된 채로 남으면 사용자 입장에서는 프로그램이 죽은 것과 같고, 스스로 복구할
방법도 없다. 그래서 재개 경로를 세 겹으로 깐다.

  1) 워커 스레드의 try/finally  — 예외·취소 어느 쪽이든 반드시 통과
  2) 창 닫기 훅(cancel)          — 진행 중 종료해도 즉시 재개
  3) atexit 등록                 — 정상 종료 경로 전부에서 마지막 방어

여는 것과 정지시키는 것을 2단계로 나눈 이유: 실패는 거의 전부 OpenProcess
(권한 부족)에서 난다. 핸들을 **전부** 먼저 확보한 뒤에 정지시키면, '절반만
멈춘' 어정쩡한 상태 없이 깨끗하게 실패할 수 있다.

마무리 단계(after_resume)는 **재개가 끝난 뒤에만** 돈다. 이 순서를 바꾸면
안 된다 — 마무리가 아무리 오래 걸려도 대상은 이미 돌아가고 있어야 한다.
러너 자신은 마무리가 무엇을 하는지 모른다(주입받은 콜러블만 부른다).
그래서 이 파일의 테스트는 Windows 없이도 전부 돈다.
"""

from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable, Optional

from mpauseapp import core, ledger, winproc
from mpauseapp.config import HOLD_SECONDS, TARGET_PROCESS_NAME, TICK_SECONDS


# ─── UI 로 올려보내는 이벤트 ────────────────────────────────────────────────

EVT_STATE = "state"    # (상태, 문구)
EVT_TICK = "tick"      # (진행률 0.0~1.0,)
EVT_DONE = "done"      # (문구,)
EVT_ERROR = "error"    # (문구,)


@dataclass
class Event:
    kind: str
    text: str = ""
    progress: float = 0.0
    state: str = ""


# ─── 사용자에게 보이는 문구 ────────────────────────────────────────────────
#
# ⚠️ 여기 있는 문구는 전부 **무엇을 어떻게 하는지 드러내지 않는다.** 상태만
# 알려 준다. 문구 한 줄이 동작을 설명해 버리면 UI 를 아무리 비워도 소용없다.
MSG_PREPARING = "준비 중…"
MSG_WORKING = "진행 중…"
MSG_FINISHING = "마무리 중…"
MSG_DONE = "완료되었습니다."
MSG_NOT_RUNNING = "게임이 실행 중인지 확인해 주세요."
MSG_CHECK_SCREEN = "완료했습니다. 게임 화면을 확인해 주세요."
MSG_RECOVER_FAILED = "정상적으로 마치지 못했습니다. 게임을 껐다가 다시 실행해 주세요."


# ─── 마지막 방어선: 종료 시 남은 것 되살리기 ────────────────────────────────

_live_runners: "set[PauseRunner]" = set()
_live_lock = threading.Lock()


def _resume_everything_at_exit() -> None:
    """인터프리터 종료 시, 아직 정지 상태로 남은 것을 전부 되살린다."""
    with _live_lock:
        runners = list(_live_runners)
    for runner in runners:
        try:
            runner.force_resume()
        except Exception:
            pass


atexit.register(_resume_everything_at_exit)


class PauseRunner:
    """버튼 한 번 = 이 객체의 start() 한 번.

    UI 스레드는 events 큐만 읽는다(tkinter 위젯을 워커에서 만지면 안 되므로).

    prepare(cancel) -> object | None
        **정지하기 전에** 한 번 불린다. 오래 걸리는 준비를 여기서 해 두면 마무리
        단계의 지연이 사라진다. 실패해도 본 동작에 영향을 주면 안 된다.
        (정지 뒤로 옮기면 준비 시간이 그대로 정지 시간에 더해진다 — 아래 2.5단계 참고)

    after_resume(cancel, prepared, on_tick) -> str
        재개가 끝난 뒤 불린다. 결과 코드를 돌려주고, 그 코드는 _FOLLOWUP_MESSAGES
        를 통해 사용자 문구가 된다.
    """

    def __init__(
        self,
        *,
        prepare: Optional[Callable[[threading.Event], object]] = None,
        after_resume: Optional[Callable[..., str]] = None,
    ) -> None:
        self.events: "Queue[Event]" = Queue()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        # 정지에 성공한 핸들. 이 리스트에 든 것은 무슨 일이 있어도 재개한다.
        self._suspended: list[winproc.ProcessHandle] = []
        self._suspended_lock = threading.Lock()
        self._state = core.STATE_IDLE
        self._state_lock = threading.Lock()
        self._prepare = prepare
        self._after_resume = after_resume

    # ── 상태 ──────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def _set_state(self, state: str, text: str = "") -> None:
        with self._state_lock:
            self._state = state
        self.events.put(Event(EVT_STATE, text=text, state=state))

    @property
    def busy(self) -> bool:
        return self.state in core.BUSY_STATES

    # ── 실행 ──────────────────────────────────────────────────────────────

    def start(
        self,
        query: Optional[str] = None,
        hold_seconds: Optional[float] = None,
    ) -> bool:
        """1회성 실행을 시작한다. 이미 실행 중이면 아무것도 하지 않고 False.

        인자는 테스트에서만 쓴다. 실제 실행은 항상 고정값으로 돈다.
        """
        if self.busy:
            return False
        # 앞선 실행에서 재개에 실패해 남은 것이 있으면 먼저 되살린다.
        # 여기서 그냥 clear() 하면 아직 정지 상태인 것의 유일한 기록이
        # 사라져 3중 방어가 전부 헛돈다.
        self.force_resume()
        self._cancel.clear()
        with _live_lock:
            _live_runners.add(self)
        self._set_state(core.STATE_SUSPENDING, MSG_PREPARING)
        self._thread = threading.Thread(
            target=self._run,
            args=(
                TARGET_PROCESS_NAME if query is None else query,
                HOLD_SECONDS if hold_seconds is None else float(hold_seconds),
            ),
            name="mpause-runner",
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        """진행을 중단하고 즉시 재개시킨다(창을 닫을 때 등)."""
        self._cancel.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """앱 종료 경로 — 취소 후 워커가 재개를 마칠 때까지 기다린다."""
        self.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        # 워커가 시간 안에 못 끝냈으면 이 스레드에서 직접 되살린다.
        stuck = self.force_resume()
        if not stuck:
            with _live_lock:
                _live_runners.discard(self)
        # 아직 정지된 게 남아 있으면 등록을 유지한다 — atexit 최후 방어가
        # 한 번 더 시도할 수 있어야 하므로 여기서 빼면 안 된다.

    def force_resume(self) -> list[winproc.ProcessHandle]:
        """정지 상태로 남은 것을 전부 되살린다(여러 번 불러도 안전).

        반환: **되살리지 못하고 여전히 정지 상태인** 핸들 목록.
        성공한 것만 목록에서 빼고 핸들을 닫는다. 실패한 것은 다시 등록해
        다음 방어선(창 닫기 → atexit)이 재시도할 수 있게 남긴다.
        여기서 무조건 비우고 핸들까지 닫아 버리면, 되살릴 방법이 사라진
        채로 '성공'이 되어 3중 방어가 전부 no-op 이 된다.
        """
        with self._suspended_lock:
            pending = list(self._suspended)
            self._suspended.clear()

        stuck: list[winproc.ProcessHandle] = []
        for handle in pending:
            resumed = False
            try:
                winproc.resume(handle)
                resumed = True
            except Exception:
                # 실패했다 — 대상이 이미 끝났으면 정상이다.
                # is_alive() 를 **먼저** 보고 건너뛰면, 조회가 실패했을 때
                # 살아 있는 것을 정지된 채로 버리게 된다. 그래서 순서를
                # 뒤집어 '일단 시도 → 실패하면 죽었는지 확인'으로 둔다.
                try:
                    resumed = not handle.is_alive()
                except Exception:
                    resumed = False
            if resumed:
                try:
                    ledger.forget(handle.pid)
                except Exception:
                    pass
                try:
                    handle.close()
                except Exception:
                    pass
            else:
                stuck.append(handle)

        if stuck:
            with self._suspended_lock:
                self._suspended.extend(stuck)
        return stuck

    # ── 워커 본체 ─────────────────────────────────────────────────────────

    def _run(self, query: str, hold_seconds: float) -> None:
        opened: list[winproc.ProcessHandle] = []
        prepared: object = None
        # 종료 상태는 재개를 마친 뒤(finally)에만 공개한다. 여기서 먼저 알리면
        # UI 가 버튼을 다시 켜서, 아직 재개가 안 끝난 상태로 다음 실행이 시작된다.
        final_state = core.STATE_DONE
        try:
            # 1단계 — 대상 찾기
            processes = winproc.list_processes()
            pids = core.select_targets(
                query, processes, exclude_pids=[winproc.current_pid()]
            )
            if not pids:
                self.events.put(Event(EVT_ERROR, text=MSG_NOT_RUNNING))
                final_state = core.STATE_FAILED
                return

            names = {pid: name for pid, name in processes}

            # 2단계 — 핸들을 전부 먼저 연다.
            # 권한 부족이면 아무것도 건드리지 않고 통째로 실패한다.
            # 다만 '이미 끝난 것'은 건너뛴다 — 열거와 오픈 사이의 경합일 뿐이라
            # 이걸로 실행 전체를 실패시키면 대상이 여러 개일 때 거의 못 쓴다.
            for pid in pids:
                try:
                    opened.append(winproc.open_process(pid, names.get(pid, "")))
                except winproc.ProcessGoneError:
                    pass
            if not opened:
                self.events.put(Event(EVT_ERROR, text=MSG_NOT_RUNNING))
                final_state = core.STATE_FAILED
                return

            # 2.5단계 — 마무리 준비를 **정지 전에** 끝낸다.
            # ⚠️ 정지한 뒤에 부르면 안 된다. prepare 안에는 시간이 얼마나 걸릴지 모르는
            # 네이티브 호출이 들어갈 수 있고(장치 생성 등) 취소도 듣지 않는다. 그걸
            # 정지 구간 안에 두면 유지 시간이 그만큼 늘어나고, 그 사이에 창을 닫아도
            # 워커가 취소를 못 봐서 '진행 중 종료해도 즉시 재개'가 성립하지 않는다.
            # 준비 내용(창 찾기·장치 준비)은 정지 전후로 결과가 같으므로 옮겨도 무해하다.
            if self._prepare is not None:
                try:
                    prepared = self._prepare(self._cancel)
                except Exception:
                    prepared = None
            if self._cancel.is_set():
                # 준비 도중에 창이 닫혔다 — 아무것도 건드리지 않고 끝낸다.
                return

            # 3단계 — 정지
            self._set_state(core.STATE_SUSPENDING, MSG_WORKING)
            for handle in opened:
                # 창을 닫아 shutdown() 이 이미 지나간 뒤에 새로 거는 것을 막는다.
                if self._cancel.is_set():
                    break
                try:
                    winproc.suspend(handle)
                except winproc.ProcessGoneError:
                    try:
                        handle.close()
                    except Exception:
                        pass
                    continue
                with self._suspended_lock:
                    self._suspended.append(handle)
                # 프로세스 밖까지 남는 기록 — 우리가 강제 종료돼도 다음 실행이
                # 이걸 보고 되살린다(ledger.py 참고). 실패해도 무시한다.
                try:
                    ledger.record(handle.pid, winproc.identity_token(handle))
                except Exception:
                    pass
            # 정지에 들어간 것은 전부 _suspended 가 소유한다(중복 close 방지).
            opened = []

            with self._suspended_lock:
                held = len(self._suspended)
            if not held:
                self.events.put(Event(EVT_ERROR, text=MSG_NOT_RUNNING))
                final_state = core.STATE_FAILED
                return

            # 4단계 — 유지 (취소에 즉시 반응하도록 잘게 쪼개서 대기)
            self._set_state(core.STATE_HELD, MSG_WORKING)
            deadline = time.monotonic() + hold_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._cancel.is_set():
                    break
                self.events.put(
                    Event(
                        EVT_TICK,
                        progress=core.run_progress(
                            "hold", hold_seconds - remaining, hold_seconds
                        ),
                    )
                )
                self._cancel.wait(timeout=min(TICK_SECONDS, remaining))

            # 5단계 — 재개. 실제 결과를 보고 보고한다(무조건 '성공'이라 하지 않는다).
            self._set_state(core.STATE_RESUMING, MSG_FINISHING)
            stuck = self.force_resume()
            if stuck:
                self.events.put(Event(EVT_ERROR, text=MSG_RECOVER_FAILED))
                final_state = core.STATE_FAILED
                return

            # 6단계 — 마무리. 여기까지 왔으면 대상은 이미 정상으로 돌아갔다.
            message = MSG_DONE
            if self._after_resume is not None and not self._cancel.is_set():
                self._set_state(core.STATE_FOLLOWUP, MSG_FINISHING)
                try:
                    result = self._after_resume(self._cancel, prepared, self._tick)
                except Exception:
                    result = "failed"
                message = _FOLLOWUP_MESSAGES.get(result, MSG_DONE)
                if result == "cancelled":
                    message = ""

            self.events.put(Event(EVT_TICK, progress=1.0))
            if message:
                self.events.put(Event(EVT_DONE, text=message))

        except winproc.ProcessControlError as exc:
            # winproc 의 문구는 이미 중립(대상 이름·PID·동작이 없다)이라 그대로 쓴다.
            self.events.put(Event(EVT_ERROR, text=str(exc)))
            final_state = core.STATE_FAILED
        except Exception:
            # 예기치 못한 오류의 내용은 사용자에게 보여 주지 않는다(내부 구조가 드러난다).
            self.events.put(Event(EVT_ERROR, text=MSG_RECOVER_FAILED))
            final_state = core.STATE_FAILED
        finally:
            # 어떤 경로로 빠져나가든 정지된 것은 전부 되살린다.
            remaining_stuck = self.force_resume()
            # 정지에 못 간 채 열려 있던 핸들 정리.
            for handle in opened:
                try:
                    handle.close()
                except Exception:
                    pass
            if not remaining_stuck:
                with _live_lock:
                    _live_runners.discard(self)
            # 재개가 끝난 뒤에야 UI 에 종료를 알린다.
            self._set_state(final_state)

    def _tick(self, progress: float) -> None:
        """마무리 단계가 진행률을 보고할 때 쓰는 콜백."""
        self.events.put(Event(EVT_TICK, progress=float(progress)))

    # ── UI 폴링 ───────────────────────────────────────────────────────────

    def drain(self, handler: Callable[[Event], None], limit: int = 64) -> None:
        """큐에 쌓인 이벤트를 UI 스레드에서 꺼내 처리한다."""
        for _ in range(limit):
            try:
                event = self.events.get_nowait()
            except Empty:
                return
            handler(event)


#: 마무리 결과 코드 → 사용자 문구. followup.py 의 RESULT_* 와 짝이다.
#: 러너가 followup 을 직접 import 하지 않는 이유: 그러면 러너 테스트가
#: Windows 의존성을 끌고 들어온다.
_FOLLOWUP_MESSAGES = {
    "ok": MSG_DONE,
    "quiet": MSG_DONE,
    # 열기까지 했는데 끝내 안 떴다 — '완료'라고 하면 열기 실패(버튼 오매핑·장치
    # 미인식 등)가 영영 관측되지 않으므로 화면 확인을 안내한다.
    "no_show": MSG_CHECK_SCREEN,
    "skipped": MSG_DONE,
    "cancelled": "",
    "no_window": MSG_CHECK_SCREEN,
    "failed": MSG_CHECK_SCREEN,
}

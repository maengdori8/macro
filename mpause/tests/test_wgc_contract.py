"""캡처 라이브러리와의 계약 — 이름 하나 때문에 기능 전체가 죽는 것을 막는다.

windows-capture 의 `event()` 는 핸들러를 **함수 이름으로** 구분한다.
이름이 'on_frame_arrived'/'on_closed' 가 아니면 등록하는 그 자리에서 예외다.
실제로 이식하면서 앞에 밑줄을 붙였다가 캡처가 100% 시작 실패했는데,
실패가 가드에 삼켜져 '한 번도 인식하지 않고 완료로 보고'되는 상태가 됐다.
"""

from __future__ import annotations

import pytest

from mpauseapp import deps
from mpauseapp.wgc import CaptureEngine


def test_handler_names_match_the_library_contract():
    assert CaptureEngine.on_frame_arrived.__name__ == "on_frame_arrived"
    assert CaptureEngine.on_closed.__name__ == "on_closed"


class NameCheckingCapture:
    """라이브러리와 같은 규칙으로 핸들러를 받는 가짜.

    실제 windows-capture 는 창 핸들이 필요해서 테스트에서 만들 수 없다.
    대신 **문제의 규칙만** 그대로 흉내 내 등록 경로를 검증한다.
    """

    ALLOWED = ("on_frame_arrived", "on_closed")
    last: "NameCheckingCapture | None" = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handlers = {}
        self.stopped = False
        NameCheckingCapture.last = self

    def event(self, handler):
        if handler.__name__ not in self.ALLOWED:
            raise Exception("Invalid Event Handler Use on_frame_arrived Or on_closed")
        self.handlers[handler.__name__] = handler
        return handler

    def start_free_threaded(self):
        engine = self

        class Control:
            def is_finished(self):
                return engine.stopped

            def stop(self):
                engine.stopped = True

        return Control()


@pytest.fixture
def fake_capture(monkeypatch):
    monkeypatch.setattr(deps, "WindowsCapture", NameCheckingCapture)
    # 테두리 마스크는 실제 창이 필요하므로 이 테스트에서는 건너뛴다.
    monkeypatch.setattr("mpauseapp.wgc._supports_native_borderless_wgc", lambda: True)
    return NameCheckingCapture


def test_capture_session_starts_and_registers_both_handlers(fake_capture):
    engine = CaptureEngine(1234)
    assert engine.start() is True, "핸들러 등록이 거부되면 인식 기능이 통째로 죽는다"
    registered = NameCheckingCapture.last.handlers
    assert set(registered) == {"on_frame_arrived", "on_closed"}
    engine.stop()


class FakeFrame:
    def __init__(self, buffer, width, height):
        self.frame_buffer = buffer
        self.width = width
        self.height = height


@pytest.mark.skipif(
    deps.cv2 is None or deps.np is None, reason="cv2/numpy 없으면 변환 경로가 없다"
)
def test_arrived_frame_becomes_a_consumable_gray_image(fake_capture):
    np = deps.np
    engine = CaptureEngine(1234)
    assert engine.start()

    buffer = np.zeros((6, 8, 4), dtype=np.uint8)
    buffer[2, 3] = (255, 255, 255, 255)
    engine.on_frame_arrived(FakeFrame(buffer, 8, 6))

    gray = engine.latest()
    assert gray is not None and gray.ndim == 2 and gray.shape == (6, 8)
    assert int(gray[2, 3]) > 200
    # 한 번 소비하면 비어야 한다(같은 프레임을 두 번 판정하지 않는다).
    assert engine.latest() is None
    assert engine.frame_size() == (8, 6)
    engine.stop()


def test_closed_session_wakes_up_the_consumer(fake_capture):
    engine = CaptureEngine(1234)
    assert engine.start()
    engine.on_closed()
    assert engine.closed_event.is_set()
    # 소비자가 timeout 만큼 잠들어 있지 않고 즉시 깨어나야 한다.
    assert engine.frame_ready_event.is_set()
    engine.stop()

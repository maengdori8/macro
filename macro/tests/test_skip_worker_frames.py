"""실제 OCR 워커에서 중복·역순·입력 중 프레임을 재사용하지 않는지 검증한다."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from macroapp import gui


class _FramePump:
    """워커의 wait마다 다음 프레임을 공급하고 마지막 프레임 뒤 종료한다."""

    def __init__(self, app, sequences):
        self.app = app
        self.sequences = iter(sequences)
        self.finished = False
        self.waits = []
        self.advance()

    def advance(self):
        try:
            sequence = next(self.sequences)
        except StopIteration:
            self.finished = True
        else:
            self.app._latest_frame = (sequence, object())

    def is_set(self):
        return self.finished

    def wait(self, seconds):
        self.waits.append(seconds)
        self.advance()
        return self.finished


def _run_worker(monkeypatch, sequences, *, initial_floor=-1, after_observation=None):
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._ocr_generation = 7
    app._ocr_manager = SimpleNamespace(hwnd=123)
    app._exit_gate = SimpleNamespace(is_set=lambda: False)
    app._skip_tap_frame_floor = initial_floor
    app._auto_exit_active = False
    app._current_skip_ocr_interval = Mock(return_value=0.3)
    app._log_to_file_only = Mock()
    app._try_skip = Mock(side_effect=after_observation)
    app.stop_event = _FramePump(app, sequences)
    monkeypatch.setattr(gui, "SKIP_ENABLED", True)
    monkeypatch.setattr(gui, "RANK_OCR_ENABLED", False)
    monkeypatch.setattr(gui.rank_ocr, "ocr_available", Mock(return_value=False))
    # 충분한 시간 간격이 흘러도 같은 프레임에는 다시 입력하지 않아야 한다.
    clock = iter(10.0 + index * 0.5 for index in range(100))
    monkeypatch.setattr(gui.time, "monotonic", lambda: next(clock))
    app._ocr_worker_loop(generation=7)
    app._log_to_file_only.assert_not_called()
    return app


@pytest.mark.parametrize(
    ("sequences", "expected"),
    [
        ([4, 4, 4, 4], [4]),
        ([4, 3, 2, 4, 5], [4, 5]),
        ([1, 2, 3], [1, 2, 3]),
    ],
)
def test_worker_accepts_only_increasing_frame_sequences(monkeypatch, sequences, expected):
    app = _run_worker(monkeypatch, sequences)
    assert [item.kwargs["frame_seq"] for item in app._try_skip.call_args_list] == expected


def test_worker_rejects_frames_at_or_before_input_floor(monkeypatch):
    app = _run_worker(monkeypatch, [2, 3, 4, 5, 5, 6], initial_floor=4)
    assert [item.kwargs["frame_seq"] for item in app._try_skip.call_args_list] == [5, 6]


def test_worker_rechecks_floor_advanced_during_tap(monkeypatch):
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._ocr_generation = 7
    app._ocr_manager = SimpleNamespace(hwnd=123)
    app._exit_gate = SimpleNamespace(is_set=lambda: False)
    app._skip_tap_frame_floor = -1
    app._auto_exit_active = False
    app._current_skip_ocr_interval = Mock(return_value=0.3)
    app._log_to_file_only = Mock()

    def observed(_gray, _manager, *, frame_seq):
        if frame_seq == 1:
            # 1번 프레임으로 입력하는 동안 2·3번 프레임이 수집된 상황.
            app._skip_tap_frame_floor = 3

    app._try_skip = Mock(side_effect=observed)
    app.stop_event = _FramePump(app, [1, 2, 3, 4])
    monkeypatch.setattr(gui, "SKIP_ENABLED", True)
    monkeypatch.setattr(gui, "RANK_OCR_ENABLED", False)
    clock = iter([10.0, 10.5, 11.0, 11.5])
    monkeypatch.setattr(gui.time, "monotonic", lambda: next(clock))
    app._ocr_worker_loop(generation=7)
    app._log_to_file_only.assert_not_called()
    assert [item.kwargs["frame_seq"] for item in app._try_skip.call_args_list] == [1, 4]


def test_retired_worker_generation_never_observes_frame():
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._ocr_generation = 8
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    app._try_skip = Mock()
    app._ocr_worker_loop(generation=7)
    app._try_skip.assert_not_called()

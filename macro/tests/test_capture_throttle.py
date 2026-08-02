from types import SimpleNamespace

import numpy as np

from macroapp import capture


def _frame(value: int):
    buffer = np.full((8, 12, 4), value, dtype=np.uint8)
    return SimpleNamespace(frame_buffer=buffer, width=12, height=8)


def test_wgc_throttle_drops_before_gray_conversion(monkeypatch):
    timestamps = iter((10.0, 10.01, 10.034))
    monkeypatch.setattr(capture.time, "perf_counter", lambda: next(timestamps))
    engine = capture.WGCCaptureEngine(123, logger=lambda _message: None, max_fps=30)

    engine.on_frame_arrived(_frame(10))
    engine.on_frame_arrived(_frame(20))
    engine.on_frame_arrived(_frame(30))

    assert engine.get_throttled_frame_count() == 1
    assert engine.get_replaced_frame_count() == 1
    image = engine.get_latest_frame()
    assert image is not None
    assert int(image[0, 0]) == 30


def test_wgc_throttle_can_be_disabled(monkeypatch):
    timestamps = iter((20.0, 20.001))
    monkeypatch.setattr(capture.time, "perf_counter", lambda: next(timestamps))
    engine = capture.WGCCaptureEngine(123, logger=lambda _message: None, max_fps=0)

    engine.on_frame_arrived(_frame(40))
    engine.on_frame_arrived(_frame(50))

    assert engine.get_throttled_frame_count() == 0
    assert engine.get_replaced_frame_count() == 1


def test_60_fps_stream_is_reduced_to_30_without_float_boundary_drift(monkeypatch):
    timestamps = iter(100.0 + index / 60.0 for index in range(600))
    monkeypatch.setattr(capture.time, "perf_counter", lambda: next(timestamps))
    engine = capture.WGCCaptureEngine(123, logger=lambda _message: None, max_fps=30)
    frame = _frame(70)

    for _ in range(600):
        engine.on_frame_arrived(frame)

    assert 299 <= engine._frame_seq <= 301
    assert 299 <= engine.get_throttled_frame_count() <= 301

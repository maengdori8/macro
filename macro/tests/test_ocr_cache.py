from types import SimpleNamespace

import numpy as np

from macroapp import gui


class _Consensus:
    def __init__(self):
        self.items = []

    def observe(self, info, now):
        self.items.append((dict(info), now))


def _app():
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._ocr_box = (0.35, 0.5, 0.48, 0.64)
    app._rank_consensus = _Consensus()
    app._rank_ocr_cache_key = None
    app._rank_ocr_cache_info = None
    app._rank_ocr_cache_at = 0.0
    app._rank_ocr_cache_hits = 0
    app._gate_fail_log_at = 0.0
    app._log_to_file_only = lambda _message: None
    return app


def test_same_ocr_regions_reuse_result_without_changing_votes(monkeypatch):
    calls = SimpleNamespace(gate=0, rank=0)

    def on_match(_crop, _tokens):
        calls.gate += 1
        return True

    def read_rank(_crop, logger=None):
        calls.rank += 1
        return {
            "rank": 12,
            "is_champion": True,
            "tier": "챌린저 1부",
            "score": 2200,
            "has_panel": True,
        }

    monkeypatch.setattr(gui.rank_ocr, "on_match_screen", on_match)
    monkeypatch.setattr(gui.rank_ocr, "read_rank_panel", read_rank)
    app = _app()
    frame = np.full((720, 1280), 80, dtype=np.uint8)

    app._observe_rank(frame, 10.0)
    app._observe_rank(frame, 10.1)

    assert calls.gate == 1
    assert calls.rank == 1
    assert app._rank_ocr_cache_hits == 1
    assert len(app._rank_consensus.items) == 2
    assert app._rank_consensus.items[0][0] == app._rank_consensus.items[1][0]


def test_ocr_cache_refreshes_after_timeout(monkeypatch):
    calls = SimpleNamespace(gate=0, rank=0)
    monkeypatch.setattr(
        gui.rank_ocr,
        "on_match_screen",
        lambda _crop, _tokens: setattr(calls, "gate", calls.gate + 1) or True,
    )
    monkeypatch.setattr(
        gui.rank_ocr,
        "read_rank_panel",
        lambda _crop, logger=None: setattr(calls, "rank", calls.rank + 1) or {
            "rank": None,
            "is_champion": False,
            "tier": None,
            "score": None,
            "has_panel": False,
        },
    )
    app = _app()
    frame = np.full((720, 1280), 60, dtype=np.uint8)

    app._observe_rank(frame, 20.0)
    app._observe_rank(frame, 20.0 + gui.RANK_OCR_CACHE_SECONDS + 0.01)

    assert calls.gate == 2
    assert calls.rank == 2

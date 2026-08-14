from macroapp import session
from macroapp.gui import AutomationApp
from macroapp.ocr import RankConsensus, _canon_tier, _parse_rank_text


def test_master_tier_is_parsed() -> None:
    info = _parse_rank_text("마스터 감독")

    assert info["tier"] == "마스터 감독"
    assert info["has_panel"] is True


def test_master_division_and_result_fields_are_parsed_together() -> None:
    info = _parse_rank_text("11056위 마스터 1부 감독 3036점")

    assert info["rank"] == 11056
    assert info["tier"] == "마스터 1부 감독"
    assert info["score"] == 3036
    assert info["has_panel"] is True


def test_master_one_character_ocr_error_is_corrected() -> None:
    assert _canon_tier("마스더 감독") == "마스터 감독"


def test_unrelated_director_text_is_not_promoted_to_master() -> None:
    assert _canon_tier("공식경기 감독") is None


def test_rank_consensus_commits_master_after_required_votes() -> None:
    consensus = RankConsensus(vote_min=3, gap=0.9, commit_after_gone=0.35)
    info = _parse_rank_text("11056위 마스터 감독 3036점")
    consensus.observe(info, 1.00)
    consensus.observe(info, 1.01)
    consensus.observe(info, 1.02)

    assert consensus.commit(1.02) == (11056, "마스터 감독", 3036)


class _CommittedConsensus:
    def __init__(self, value):
        self.value = value
        self.was_reset = False

    def commit(self, _now):
        return self.value

    def reset(self):
        self.was_reset = True


def _bare_app(committed):
    app = AutomationApp.__new__(AutomationApp)
    app._rank_consensus = _CommittedConsensus(committed)
    app._last_rank = 9000
    app._last_tier = "챔피언스 감독"
    app._last_score = 2800
    app._rank_state = None
    app._maybe_stop_on_target = lambda *_args: None
    app.queue_log = lambda *_args: None
    app._report_status = lambda **_kwargs: None
    # 확정은 판수 카운터에 '이 패널에서 값이 나왔다'만 알린다(세는 건 패널이 사라질 때).
    app._match_counter = session.MatchCounter()
    app._session_tracker = session.SessionTracker()
    return app


def test_new_result_without_tier_does_not_reuse_previous_tier() -> None:
    app = _bare_app((11056, None, 3036))

    AutomationApp._commit_rank(app, 1.0)

    assert app._rank_consensus.was_reset is True
    assert app._last_tier is None
    assert app._rank_state == (11056, "3036점")


def test_new_master_result_replaces_previous_tier() -> None:
    app = _bare_app((11056, "마스터 감독", 3036))

    AutomationApp._commit_rank(app, 1.0)

    assert app._last_tier == "마스터 감독"
    assert app._rank_state == (11056, "마스터 감독 · 3036점")

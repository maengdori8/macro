import datetime as dt
from pathlib import Path
import tempfile

from macroapp.mining import (
    DIVISION_CHAMPIONS,
    DIVISION_SUPER_CHAMPIONS,
    KST,
    MiningSettings,
    MiningStore,
    MiningSynchronizer,
    calculate_fc,
)


def _record(match_id, when, *, division, result, ouid="user-1"):
    return {
        "match_id": match_id,
        "ouid": ouid,
        "nickname": "테스트",
        "match_date_utc": when,
        "division": division,
        "result": result,
        "end_type": 0,
        "fc": calculate_fc(division, result),
        "fetched_at_utc": "2026-08-02T00:00:00Z",
    }


def test_fc_reward_policy():
    assert calculate_fc(DIVISION_CHAMPIONS, "승") == 15
    assert calculate_fc(DIVISION_SUPER_CHAMPIONS, "승") == 20
    assert calculate_fc(DIVISION_CHAMPIONS, "패") == 0
    assert calculate_fc(700, "승") == 0


def test_settings_round_trip_does_not_store_raw_key():
    with tempfile.TemporaryDirectory() as directory:
        store = MiningStore(Path(directory))
        settings = MiningSettings(
            nickname="감독",
            ouid="abc",
            api_key="secret-api-key",
            last_sync="2026-08-02T10:00:00+09:00",
        )
        store.save_settings(settings)
        raw = store.settings_path.read_text(encoding="utf-8")
        assert "secret-api-key" not in raw
        assert store.load_settings() == settings


def test_summary_uses_kst_day_and_eligible_matches_only():
    with tempfile.TemporaryDirectory() as directory:
        store = MiningStore(Path(directory))
        store.upsert_matches(
            [
                # UTC 전날이지만 KST로는 8월 2일.
                _record(
                    "m1",
                    "2026-08-01T16:00:00Z",
                    division=DIVISION_CHAMPIONS,
                    result="승",
                ),
                _record(
                    "m2",
                    "2026-08-02T02:00:00Z",
                    division=DIVISION_SUPER_CHAMPIONS,
                    result="승",
                ),
                _record(
                    "m3",
                    "2026-08-02T03:00:00Z",
                    division=DIVISION_CHAMPIONS,
                    result="패",
                ),
                _record(
                    "draw",
                    "2026-08-02T04:00:00Z",
                    division=DIVISION_CHAMPIONS,
                    result="무",
                ),
                _record(
                    "lower",
                    "2026-08-02T05:00:00Z",
                    division=700,
                    result="승",
                ),
            ]
        )

        summary = store.summary("user-1", 1, today=dt.date(2026, 8, 2))
        assert summary.fc == 35
        assert summary.matches == 3
        assert summary.wins == 2
        assert summary.losses == 1
        assert summary.super_champion_wins == 1
        assert summary.champion_wins == 1
        assert summary.active_days == 1
        assert summary.daily_average == 35.0
        assert summary.daily[0].date == "2026-08-02"
        assert summary.daily[0].fc == 35


class _FakeClient:
    details = {}
    pages = {}
    page_calls = []
    detail_calls = []

    def __init__(self, api_key):
        assert api_key == "key"

    def resolve_ouid(self, nickname):
        assert nickname == "감독"
        return "user-1"

    def match_ids(self, ouid, offset, limit=100):
        self.page_calls.append(offset)
        return list(self.pages.get(offset, []))

    def match_detail(self, match_id):
        self.detail_calls.append(match_id)
        return self.details[match_id]


def _detail(match_id, hour):
    return {
        "matchDate": f"2026-08-02T{hour:02d}:00:00Z",
        "matchType": 52,
        "matchInfo": [
            {
                "ouid": "user-1",
                "nickname": "감독",
                "division": DIVISION_CHAMPIONS,
                "matchDetail": {
                    "seasonId": 202601,
                    "matchResult": "승",
                    "matchEndType": 0,
                },
            }
        ],
    }


def test_incremental_sync_stops_at_fully_existing_page():
    with tempfile.TemporaryDirectory() as directory:
        store = MiningStore(Path(directory))
        _FakeClient.page_calls = []
        _FakeClient.detail_calls = []
        _FakeClient.pages = {
            0: ["new", "old"],
        }
        _FakeClient.details = {
            "new": _detail("new", 4),
            "old": _detail("old", 3),
        }
        synchronizer = MiningSynchronizer(
            store,
            max_workers=2,
            max_pages=5,
            client_factory=_FakeClient,
        )

        first = synchronizer.sync("감독", "key")
        assert first.added == 2
        assert _FakeClient.page_calls == [0]

        _FakeClient.page_calls = []
        _FakeClient.detail_calls = []
        second = synchronizer.sync("감독", "key")
        assert second.added == 0
        assert _FakeClient.page_calls == [0]
        assert _FakeClient.detail_calls == []
        assert store.load_settings().ouid == "user-1"

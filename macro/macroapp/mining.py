"""FC Online 감독모드 FC 채굴 기록 동기화와 로컬 통계.

Nexon Open API 호출은 사용자가 동기화를 요청했을 때만 실행한다. 자동화
루프와 상태를 공유하지 않으며, 모든 영구 데이터는 앱의 ``data`` 폴더에
저장한다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import asdict, dataclass
import base64
import datetime as dt
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:  # Windows 판매본에서는 pywin32의 DPAPI를 사용한다.
    import win32crypt  # type: ignore
except Exception:  # pragma: no cover - Windows가 아닌 테스트 환경
    win32crypt = None


API_BASE_URL = "https://open.api.nexon.com"
MANAGER_MATCH_TYPE = 52
DIVISION_SUPER_CHAMPIONS = 800
DIVISION_CHAMPIONS = 900
PAGE_SIZE = 100
MAX_HISTORY_PAGES = 50
KST = dt.timezone(dt.timedelta(hours=9))


class MiningError(RuntimeError):
    """사용자에게 보여줄 수 있는 채굴 동기화 오류."""


@dataclass(frozen=True)
class MiningSettings:
    nickname: str = ""
    ouid: str = ""
    api_key: str = ""
    last_sync: str = ""


@dataclass(frozen=True)
class DailyMining:
    date: str
    fc: int
    matches: int
    wins: int
    losses: int


@dataclass(frozen=True)
class MiningSummary:
    period_days: int
    start_date: str
    end_date: str
    fc: int
    matches: int
    wins: int
    losses: int
    super_champion_wins: int
    champion_wins: int
    active_days: int
    daily_average: float
    latest_match: str
    daily: tuple[DailyMining, ...]


@dataclass(frozen=True)
class SyncResult:
    nickname: str
    ouid: str
    scanned: int
    added: int
    failed: int
    pages: int
    finished_at: str


ProgressCallback = Callable[[str, int, int, str], None]


def calculate_fc(division: int, result: str) -> int:
    """현재 감독모드 FC 적립 규칙으로 한 경기의 FC를 계산한다."""

    if result != "승":
        return 0
    if int(division) == DIVISION_SUPER_CHAMPIONS:
        return 20
    if int(division) == DIVISION_CHAMPIONS:
        return 15
    return 0


def _protect_secret(secret: str) -> str:
    raw = secret.encode("utf-8")
    if not raw:
        return ""
    if win32crypt is not None:
        try:
            encrypted = win32crypt.CryptProtectData(
                raw,
                "mAuto Nexon Open API key",
                None,
                None,
                None,
                0,
            )[1]
            return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
        except Exception:
            pass
    # 비 Windows 개발/테스트 환경의 호환 저장. 보안 저장으로 오인하지 않게
    # 접두사를 분명히 하며 Windows 배포본에서는 위 DPAPI 경로를 사용한다.
    return "plain:" + base64.b64encode(raw).decode("ascii")


def _unprotect_secret(token: str) -> str:
    if not token:
        return ""
    try:
        scheme, encoded = token.split(":", 1)
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        if scheme == "dpapi":
            if win32crypt is None:
                return ""
            raw = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
        elif scheme != "plain":
            return ""
        return raw.decode("utf-8")
    except Exception:
        return ""


def _parse_api_datetime(value: str) -> Optional[dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _utc_iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MiningStore:
    """설정과 매치 기록을 관리하는 작은 SQLite 저장소."""

    def __init__(self, base_dir: Path):
        self.data_dir = Path(base_dir) / "data"
        self.settings_path = self.data_dir / "mining_settings.json"
        self.db_path = self.data_dir / "mining_stats.db"
        self._write_lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mining_matches (
                    match_id       TEXT PRIMARY KEY,
                    ouid           TEXT NOT NULL,
                    nickname       TEXT NOT NULL,
                    match_date_utc TEXT NOT NULL,
                    division       INTEGER NOT NULL,
                    result         TEXT NOT NULL,
                    end_type       INTEGER NOT NULL DEFAULT 0,
                    fc             INTEGER NOT NULL DEFAULT 0,
                    fetched_at_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mining_ouid_date
                    ON mining_matches(ouid, match_date_utc);
                CREATE TABLE IF NOT EXISTS mining_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                -- 매크로가 이 PC에서 직접 센 판수 원장. 넥슨 기록(mining_matches)과
                -- 성격이 완전히 달라 테이블을 분리한다: 이쪽은 화면 인식 기반이라
                -- 무승부·하위 디비전·미판정을 전부 포함하고 동기화가 필요 없다.
                -- 판당 한 행을 남겨 두면 '한 판'의 정의가 나중에 바뀌어도 다시 셀 수 있다.
                CREATE TABLE IF NOT EXISTS macro_matches (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_macro_recorded
                    ON macro_matches(recorded_at_utc);
                """
            )

    def load_settings(self) -> MiningSettings:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return MiningSettings()
            return MiningSettings(
                nickname=str(payload.get("nickname") or ""),
                ouid=str(payload.get("ouid") or ""),
                api_key=_unprotect_secret(str(payload.get("api_key") or "")),
                last_sync=str(payload.get("last_sync") or ""),
            )
        except (OSError, ValueError, TypeError):
            return MiningSettings()

    def save_settings(self, settings: MiningSettings) -> None:
        payload = {
            "version": 1,
            "nickname": settings.nickname.strip(),
            "ouid": settings.ouid.strip(),
            "api_key": _protect_secret(settings.api_key.strip()),
            "last_sync": settings.last_sync,
        }
        temporary = self.settings_path.with_suffix(".json.tmp")
        with self._write_lock:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)

    def get_state(self, key: str, default: str = "") -> str:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT value FROM mining_state WHERE key = ?", (key,)
            ).fetchone()
        return default if row is None else str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        with self._write_lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO mining_state(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def existing_ids(self, match_ids: Iterable[str]) -> set[str]:
        ids = tuple(dict.fromkeys(str(value) for value in match_ids if value))
        if not ids:
            return set()
        found: set[str] = set()
        with closing(self._connect()) as connection, connection:
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT match_id FROM mining_matches WHERE match_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                found.update(str(row["match_id"]) for row in rows)
        return found

    def upsert_matches(self, records: Iterable[dict]) -> int:
        rows = [dict(record) for record in records]
        if not rows:
            return 0
        with self._write_lock, closing(self._connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT INTO mining_matches(
                    match_id, ouid, nickname, match_date_utc, division,
                    result, end_type, fc, fetched_at_utc
                ) VALUES (
                    :match_id, :ouid, :nickname, :match_date_utc, :division,
                    :result, :end_type, :fc, :fetched_at_utc
                )
                ON CONFLICT(match_id) DO UPDATE SET
                    ouid=excluded.ouid,
                    nickname=excluded.nickname,
                    match_date_utc=excluded.match_date_utc,
                    division=excluded.division,
                    result=excluded.result,
                    end_type=excluded.end_type,
                    fc=excluded.fc,
                    fetched_at_utc=excluded.fetched_at_utc
                """,
                rows,
            )
            return connection.total_changes - before

    def append_macro_match(self, session_id: str, recorded_at_utc: str) -> None:
        """매크로가 센 판 하나를 원장에 적습니다(UTC 그대로)."""

        with self._write_lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO macro_matches(session_id, recorded_at_utc) VALUES (?, ?)",
                (str(session_id), str(recorded_at_utc)),
            )

    def count_macro_matches(self, start_utc: str, end_utc: str) -> int:
        """[start_utc, end_utc) 구간의 판수. 날짜 경계는 호출부가 정합니다."""

        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM macro_matches
                WHERE recorded_at_utc >= ? AND recorded_at_utc < ?
                """,
                (str(start_utc), str(end_utc)),
            ).fetchone()
        return 0 if row is None else int(row[0] or 0)

    def summary(
        self,
        ouid: str,
        period_days: int,
        *,
        today: Optional[dt.date] = None,
    ) -> MiningSummary:
        days = max(1, int(period_days))
        today_kst = today or dt.datetime.now(KST).date()
        start_date = today_kst - dt.timedelta(days=days - 1)
        end_date = today_kst
        start_kst = dt.datetime.combine(start_date, dt.time.min, KST)
        end_exclusive_kst = dt.datetime.combine(
            end_date + dt.timedelta(days=1), dt.time.min, KST
        )
        start_utc = _utc_iso(start_kst)
        end_utc = _utc_iso(end_exclusive_kst)

        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT match_date_utc, division, result, fc
                FROM mining_matches
                WHERE ouid = ? AND match_date_utc >= ? AND match_date_utc < ?
                  AND division IN (?, ?) AND result IN ('승', '패')
                ORDER BY match_date_utc ASC
                """,
                (
                    ouid,
                    start_utc,
                    end_utc,
                    DIVISION_SUPER_CHAMPIONS,
                    DIVISION_CHAMPIONS,
                ),
            ).fetchall()

        by_date: dict[str, dict[str, int]] = {}
        wins = losses = fc = sc_wins = ch_wins = 0
        latest_match = ""
        for row in rows:
            when = _parse_api_datetime(str(row["match_date_utc"]))
            if when is None:
                continue
            date_key = when.astimezone(KST).date().isoformat()
            bucket = by_date.setdefault(
                date_key,
                {"fc": 0, "matches": 0, "wins": 0, "losses": 0},
            )
            result = str(row["result"])
            division = int(row["division"])
            earned = int(row["fc"])
            bucket["matches"] += 1
            bucket["fc"] += earned
            fc += earned
            if result == "승":
                wins += 1
                bucket["wins"] += 1
                if division == DIVISION_SUPER_CHAMPIONS:
                    sc_wins += 1
                elif division == DIVISION_CHAMPIONS:
                    ch_wins += 1
            else:
                losses += 1
                bucket["losses"] += 1
            latest_match = str(row["match_date_utc"])

        daily: list[DailyMining] = []
        for offset in range(days):
            date_key = (start_date + dt.timedelta(days=offset)).isoformat()
            bucket = by_date.get(
                date_key,
                {"fc": 0, "matches": 0, "wins": 0, "losses": 0},
            )
            daily.append(DailyMining(date=date_key, **bucket))

        active_days = sum(1 for item in daily if item.matches > 0)
        return MiningSummary(
            period_days=days,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            fc=fc,
            matches=wins + losses,
            wins=wins,
            losses=losses,
            super_champion_wins=sc_wins,
            champion_wins=ch_wins,
            active_days=active_days,
            daily_average=(fc / days),
            latest_match=latest_match,
            daily=tuple(daily),
        )


class NexonMiningClient:
    """표준 라이브러리만 사용하는 Nexon Open API 클라이언트."""

    def __init__(self, api_key: str, *, timeout: float = 15.0):
        self.api_key = api_key.strip()
        self.timeout = max(2.0, float(timeout))
        if not self.api_key:
            raise MiningError("Nexon Open API 키를 입력해 주세요.")

    def _get_json(self, path: str, params: dict) -> object:
        url = f"{API_BASE_URL}{path}?{urlencode(params)}"
        request = Request(
            url,
            method="GET",
            headers={
                "x-nxopen-api-key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "mAuto/FCMining",
            },
        )
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(0.7 * (2 ** attempt))
                    last_error = exc
                    continue
                if exc.code in {401, 403}:
                    raise MiningError("API 키가 거부되었습니다. 키를 다시 확인해 주세요.") from exc
                if exc.code == 429:
                    raise MiningError("API 호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.") from exc
                raise MiningError(f"Nexon API 오류(HTTP {exc.code})") from exc
            except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.7 * (2 ** attempt))
                    continue
        raise MiningError(f"Nexon API 통신에 실패했습니다: {last_error}")

    def resolve_ouid(self, nickname: str) -> str:
        payload = self._get_json("/fconline/v1/id", {"nickname": nickname})
        if not isinstance(payload, dict) or not payload.get("ouid"):
            raise MiningError("닉네임을 찾을 수 없습니다.")
        return str(payload["ouid"])

    def match_ids(self, ouid: str, offset: int, limit: int = PAGE_SIZE) -> list[str]:
        payload = self._get_json(
            "/fconline/v1/user/match",
            {
                "ouid": ouid,
                "matchtype": MANAGER_MATCH_TYPE,
                "offset": int(offset),
                "limit": int(limit),
                "orderby": "desc",
            },
        )
        if not isinstance(payload, list):
            raise MiningError("매치 목록 응답 형식이 올바르지 않습니다.")
        return [str(value) for value in payload if value]

    def match_detail(self, match_id: str) -> dict:
        payload = self._get_json(
            "/fconline/v1/match-detail",
            {"matchid": match_id},
        )
        if not isinstance(payload, dict):
            raise MiningError("매치 상세 응답 형식이 올바르지 않습니다.")
        return payload


def _record_from_detail(match_id: str, detail: dict, ouid: str) -> Optional[dict]:
    match_date = _parse_api_datetime(str(detail.get("matchDate") or ""))
    if match_date is None:
        return None
    for participant in detail.get("matchInfo") or ():
        if not isinstance(participant, dict) or str(participant.get("ouid")) != ouid:
            continue
        match_detail = participant.get("matchDetail") or {}
        division = participant.get("division")
        season_id = match_detail.get("seasonId")
        result = str(match_detail.get("matchResult") or "")
        if division is None or season_id is None or result not in {"승", "무", "패"}:
            return None
        division = int(division)
        now_utc = dt.datetime.now(dt.timezone.utc)
        return {
            "match_id": match_id,
            "ouid": ouid,
            "nickname": str(participant.get("nickname") or ""),
            "match_date_utc": _utc_iso(match_date),
            "division": division,
            "result": result,
            "end_type": int(match_detail.get("matchEndType") or 0),
            "fc": calculate_fc(division, result),
            "fetched_at_utc": _utc_iso(now_utc),
        }
    return None


class MiningSynchronizer:
    """점진적 매치 동기화. 자동화 실행 중에는 작은 워커 수로 사용한다."""

    def __init__(
        self,
        store: MiningStore,
        *,
        max_workers: int = 8,
        max_pages: int = MAX_HISTORY_PAGES,
        client_factory=NexonMiningClient,
    ):
        self.store = store
        self.max_workers = max(1, min(16, int(max_workers)))
        self.max_pages = max(1, int(max_pages))
        self.client_factory = client_factory

    @staticmethod
    def _notify(
        callback: Optional[ProgressCallback],
        phase: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if callback is not None:
            callback(phase, int(current), int(total), message)

    def sync(
        self,
        nickname: str,
        api_key: str,
        *,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> SyncResult:
        nickname = nickname.strip()
        api_key = api_key.strip()
        if not nickname:
            raise MiningError("FC Online 닉네임을 입력해 주세요.")
        if not api_key:
            raise MiningError("Nexon Open API 키를 입력해 주세요.")
        client = self.client_factory(api_key)
        self._notify(progress, "account", 0, 1, "계정 확인 중")
        ouid = client.resolve_ouid(nickname)
        if cancel_event is not None and cancel_event.is_set():
            raise MiningError("동기화를 취소했습니다.")

        history_key = f"history_complete:{ouid}"
        history_complete = self.store.get_state(history_key) == "1"
        all_ids: list[str] = []
        pages = 0
        reached_end = False
        for page in range(self.max_pages):
            if cancel_event is not None and cancel_event.is_set():
                raise MiningError("동기화를 취소했습니다.")
            ids = client.match_ids(ouid, page * PAGE_SIZE)
            pages += 1
            self._notify(
                progress,
                "list",
                pages,
                self.max_pages,
                f"매치 목록 확인 중 · {len(all_ids) + len(ids):,}개",
            )
            if not ids:
                reached_end = True
                break
            existing_page = self.store.existing_ids(ids)
            all_ids.extend(ids)
            # 과거 전체가 한 번 채워진 계정은 이미 보유한 최신 페이지와 만나는
            # 즉시 종료한다. 평소 동기화가 1~2 API 호출로 끝나 PC와 API 부하가 작다.
            if history_complete and len(existing_page) == len(ids):
                break
            if len(ids) < PAGE_SIZE:
                reached_end = True
                break

        unique_ids = tuple(dict.fromkeys(all_ids))
        existing = self.store.existing_ids(unique_ids)
        new_ids = [match_id for match_id in unique_ids if match_id not in existing]
        records: list[dict] = []
        failed = 0
        total = len(new_ids)
        self._notify(progress, "detail", 0, total, f"신규 매치 {total:,}개 분석 중")

        if new_ids:
            with ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="mining-sync",
            ) as executor:
                futures = {
                    executor.submit(client.match_detail, match_id): match_id
                    for match_id in new_ids
                }
                for completed, future in enumerate(as_completed(futures), 1):
                    if cancel_event is not None and cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        raise MiningError("동기화를 취소했습니다.")
                    match_id = futures[future]
                    try:
                        record = _record_from_detail(match_id, future.result(), ouid)
                    except Exception:
                        record = None
                    if record is None:
                        failed += 1
                    else:
                        records.append(record)
                    self._notify(
                        progress,
                        "detail",
                        completed,
                        total,
                        f"신규 매치 분석 · {completed:,}/{total:,}",
                    )

        self.store.upsert_matches(records)
        if reached_end and failed == 0:
            self.store.set_state(history_key, "1")

        finished_at = dt.datetime.now(KST).isoformat(timespec="seconds")
        newest_nickname = nickname
        if records:
            newest = max(records, key=lambda record: record["match_date_utc"])
            newest_nickname = newest.get("nickname") or nickname
        self.store.save_settings(
            MiningSettings(
                nickname=newest_nickname,
                ouid=ouid,
                api_key=api_key,
                last_sync=finished_at,
            )
        )
        self._notify(progress, "done", total, total, "동기화 완료")
        return SyncResult(
            nickname=newest_nickname,
            ouid=ouid,
            scanned=len(unique_ids),
            added=len(records),
            failed=failed,
            pages=pages,
            finished_at=finished_at,
        )


def summary_as_dict(summary: MiningSummary) -> dict:
    """진단/테스트용 JSON 직렬화 표현."""

    payload = asdict(summary)
    payload["daily"] = [asdict(item) for item in summary.daily]
    return payload

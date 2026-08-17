"""프로세스 밖까지 살아남는 네 번째 방어선.

runner.py 의 3중 방어(워커 finally / 창 닫기 / atexit)는 **전부 이 프로세스가
파이썬 코드를 더 실행해야** 동작한다. 작업 관리자의 '작업 끝내기', 네이티브
크래시, 설치 프로그램의 강제 종료(setup 은 잠긴 exe 를 덮어쓰려고 실행 중인
인스턴스를 닫는다), 전원 차단은 셋 다 건너뛴다. 그러면 정지시킨 대상은
되살릴 근거조차 메모리와 함께 사라진 채 무한정 멈춘 상태로 남는다.

그래서 정지 직후 (PID, 신원 토큰) 을 아주 작은 파일에 적고, 재개에 성공하면
지운다. 다음 실행이 시작될 때 남아 있는 항목을 먼저 되살린다.

파일에 대해 두 가지를 지킨다.
  * **살아 있는 동안만 존재한다.** 마지막 항목이 지워지면 파일 자체를 지운다.
    평소에는 디스크에 아무것도 없다.
  * **아무 설명도 적지 않는다.** 줄마다 숫자 두 개뿐이다.

신원 토큰(프로세스 생성 시각)이 필요한 이유: PID 는 재사용된다. 토큰이 다르면
그 PID 는 이미 다른 프로그램의 것이므로 **건드리지 않고** 항목만 버린다.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from macroapp import winproc

_lock = threading.Lock()
_FILE_NAME = "state"


@dataclass(frozen=True)
class Entry:
    pid: int
    token: int


# ─── 순수 로직 (파일·Windows 없이 검증 가능) ───────────────────────────────


def serialize(entries: Iterable[Entry]) -> str:
    """항목들을 파일 내용으로. 한 줄에 하나, 숫자 두 개."""
    return "".join(f"{int(e.pid)} {int(e.token)}\n" for e in entries)


def parse(text: str) -> list[Entry]:
    """파일 내용을 항목 목록으로. 깨진 줄은 조용히 버린다.

    파일이 반쯤 쓰이다 만 상태(강제 종료 도중)일 수 있으므로, 한 줄이 이상해도
    나머지는 살려야 한다 — 여기서 예외를 내면 복구가 통째로 멈춘다.
    """
    entries: list[Entry] = []
    seen: set[int] = set()
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            token = int(parts[1])
        except ValueError:
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        entries.append(Entry(pid, token))
    return entries


def without(entries: Iterable[Entry], pid: int) -> list[Entry]:
    return [e for e in entries if e.pid != int(pid)]


def merged(entries: Iterable[Entry], entry: Entry) -> list[Entry]:
    """같은 PID 는 최신 것으로 덮어쓴다(중복 줄 방지)."""
    return without(entries, entry.pid) + [entry]


def should_resume(entry: Entry, actual_token: Optional[int]) -> bool:
    """이 항목을 정말 되살려도 되는가.

    actual_token 이 None 이면 프로세스를 열지 못한 것(이미 종료됐거나 권한 없음)
    → 건드리지 않는다. 토큰이 다르면 PID 가 재사용된 것 → 절대 건드리면 안 된다.
    기록된 토큰이 0 이면 그때 신원을 못 읽었다는 뜻이라 확인할 방법이 없다
    → 안전한 쪽(건드리지 않음)을 고른다.
    """
    if actual_token is None:
        return False
    if not entry.token or not actual_token:
        return False
    return int(entry.token) == int(actual_token)


# ─── 파일 (실패해도 본 동작에 영향 없음) ───────────────────────────────────


def path() -> Path:
    """설치 폴더와 무관하게 유지되는 사용자 데이터 폴더 아래 한 파일."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) / "Macro" if base else Path.home() / ".macro"
    return root / _FILE_NAME


def _read() -> list[Entry]:
    try:
        return parse(path().read_text(encoding="utf-8"))
    except OSError:
        return []


def _write(entries: list[Entry]) -> None:
    target = path()
    try:
        if not entries:
            # 남은 게 없으면 파일 자체를 지운다 — 평소에 흔적을 남기지 않는다.
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓰고 바꿔 끼운다. 쓰는 도중에 죽어도 반쪽짜리 파일이
        # 남지 않는다(그 파일이 유일한 복구 근거다).
        temporary = target.with_suffix(".tmp")
        temporary.write_text(serialize(entries), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        pass


def record(pid: int, token: int) -> None:
    """정지시킨 대상을 기록한다."""
    with _lock:
        _write(merged(_read(), Entry(int(pid), int(token))))


def forget(pid: int) -> None:
    """되살린 대상을 기록에서 지운다."""
    with _lock:
        _write(without(_read(), int(pid)))


def pending() -> list[Entry]:
    with _lock:
        return _read()


def recover() -> int:
    """앱이 시작될 때 남아 있는 항목을 되살린다. 되살린 개수를 돌려준다.

    무슨 일이 있어도 예외를 밖으로 내지 않는다 — 이것 때문에 앱이 안 뜨면
    사용자는 복구할 방법이 아예 없어진다.
    """
    resumed = 0
    try:
        entries = pending()
    except Exception:
        return 0
    for entry in entries:
        try:
            handle = winproc.open_process(entry.pid)
        except Exception:
            # 이미 끝났거나 권한이 없다 — 기록만 버린다.
            forget(entry.pid)
            continue
        try:
            token = winproc.identity_token(handle)
            if should_resume(entry, token):
                winproc.resume(handle)
                resumed += 1
        except Exception:
            pass
        finally:
            try:
                handle.close()
            except Exception:
                pass
            forget(entry.pid)
    return resumed

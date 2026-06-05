"""로깅 타입과 콘솔 안전 출력. GUI 외 모듈이 타입이 있는 logger 인자를 받도록 합니다."""

from __future__ import annotations

from typing import Callable

LogCallback = Callable[[str], None]


def safe_console_logger(message: str) -> None:
    """--noconsole 빌드에서 stdout이 None이어도 죽지 않는 기본 logger."""
    try:
        print(message)
    except Exception:
        pass

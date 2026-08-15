"""자판기용 라이선스를 서버에서 일괄 발급합니다.

예시:
    python license_generator.py --days 30 --count 20 --batch "7월 재고"
    python license_generator.py --days 30 --count 100 --plain > stock.txt

관리자 키는 코드나 명령행에 적지 않습니다. ADMIN_KEY 환경변수가 없으면
표시되지 않는 입력창으로 직접 받습니다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://license-server-flame-eta.vercel.app"
VALID_UNLIMITED_DAYS = 99999
LICENSE_KEY_PATTERN = re.compile(
    r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}"
    r"(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}){4}$"
)


def _validate_args(days: int, count: int, batch: str, memo: str) -> None:
    if days != VALID_UNLIMITED_DAYS and not 1 <= days <= 36500:
        raise ValueError("기간은 1~36500일 또는 무제한(99999)이어야 합니다.")
    if not 1 <= count <= 100:
        raise ValueError("발급 수량은 1~100개여야 합니다.")
    if len(batch) > 80:
        raise ValueError("배치명은 80자 이하여야 합니다.")
    if len(memo) > 200:
        raise ValueError("메모는 200자 이하여야 합니다.")


def issue_batch(
    *,
    api_base: str,
    admin_key: str,
    days: int,
    count: int,
    batch: str,
    memo: str,
    timeout: float = 30.0,
) -> dict:
    """관리자 API에 한 번의 멱등 요청으로 키 묶음을 발급합니다."""
    _validate_args(days, count, batch, memo)
    if not admin_key:
        raise ValueError("관리자 키가 비어 있습니다.")

    payload = {
        "action": "batchIssue",
        "requestId": secrets.token_hex(16),
        "days": days,
        "count": count,
        "batchName": batch,
        "memo": memo,
    }
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/admin",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": admin_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", str(exc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = str(exc)
        raise RuntimeError(f"발급 실패: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"서버 연결 실패: {exc.reason}") from exc

    keys = result.get("keys")
    if not result.get("success") or not isinstance(keys, list):
        raise RuntimeError(result.get("message") or "서버 응답에 키 목록이 없습니다.")
    if (
        len(keys) != count
        or len(set(keys)) != count
        or any(not isinstance(key, str) or not LICENSE_KEY_PATTERN.fullmatch(key) for key in keys)
    ):
        raise RuntimeError("서버가 반환한 키 수량 또는 형식이 올바르지 않습니다.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="mAuto 자판기용 라이선스 일괄 발급")
    parser.add_argument("--days", type=int, default=30, help="이용기간(기본 30일, 무제한 99999)")
    parser.add_argument("--count", type=int, default=10, help="발급 수량 1~100(기본 10)")
    parser.add_argument("--batch", default="", help="관리 화면에 표시할 배치명")
    parser.add_argument("--memo", default="", help="모든 키에 저장할 공통 메모")
    parser.add_argument("--plain", action="store_true", help="자판기용으로 키만 한 줄씩 출력")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=argparse.SUPPRESS)
    args = parser.parse_args()

    admin_key = os.environ.get("ADMIN_KEY", "").strip()
    if not admin_key:
        admin_key = getpass.getpass("관리자 키: ").strip()

    try:
        result = issue_batch(
            api_base=args.api_base,
            admin_key=admin_key,
            days=args.days,
            count=args.count,
            batch=args.batch.strip(),
            memo=args.memo.strip(),
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"[오류] {exc}") from exc

    plain_text = "\n".join(result["keys"])
    if args.plain:
        sys.stdout.write(plain_text)
        return

    period = "무제한" if args.days == VALID_UNLIMITED_DAYS else f"{args.days}일권"
    print(f"\n{period} {args.count}개 발급 완료")
    if args.batch:
        print(f"배치: {args.batch}")
    print("-" * 40)
    print(plain_text)
    print("-" * 40)
    print("위 목록을 자판기 재고 입력란에 그대로 붙여넣으세요.")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    main()

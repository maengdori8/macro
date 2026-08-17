"""자판기용 라이선스를 서버에서 일괄 발급합니다.

예시:
    python license_generator.py --days 30 --count 20 --batch "7월 재고"
    python license_generator.py --days 30 --count 100 --plain > stock.txt
    python license_generator.py --product mpause --days 30 --count 20   # mPause 전용 키

--product 를 생략하면 기존과 같이 mAuto("macro") 키가 나옵니다. 제품이 다른 키는
해당 앱에서만 인증되며, 서로 교차 사용할 수 없습니다.

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


DEFAULT_PRODUCT = "macro"
# 서버 lib/product.js 의 PRODUCT_PATTERN 과 같은 제약(서명 메시지 구분자 주입 차단).
PRODUCT_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")


def _validate_args(days: int, count: int, batch: str, memo: str, product: str) -> None:
    if days != VALID_UNLIMITED_DAYS and not 1 <= days <= 36500:
        raise ValueError("기간은 1~36500일 또는 무제한(99999)이어야 합니다.")
    if not 1 <= count <= 100:
        raise ValueError("발급 수량은 1~100개여야 합니다.")
    if len(batch) > 80:
        raise ValueError("배치명은 80자 이하여야 합니다.")
    if len(memo) > 200:
        raise ValueError("메모는 200자 이하여야 합니다.")
    if not PRODUCT_PATTERN.fullmatch(product):
        raise ValueError("제품 식별자는 [a-z0-9_-] 1~32자여야 합니다.")


def issue_batch(
    *,
    api_base: str,
    admin_key: str,
    days: int,
    count: int,
    batch: str,
    memo: str,
    product: str = DEFAULT_PRODUCT,
    timeout: float = 30.0,
) -> dict:
    """관리자 API에 한 번의 멱등 요청으로 키 묶음을 발급합니다."""
    _validate_args(days, count, batch, memo, product)
    if not admin_key:
        raise ValueError("관리자 키가 비어 있습니다.")

    payload = {
        "action": "batchIssue",
        "requestId": secrets.token_hex(16),
        "days": days,
        "count": count,
        "batchName": batch,
        "memo": memo,
        # 어느 앱 전용 키인지. 서버가 이 값을 라이센스 문서에 저장하고,
        # 검증 시 요청 제품과 일치할 때만 유효 서명을 내준다.
        "product": product,
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

    # ⚠️ 서버가 **실제로 어떤 제품으로** 발급했는지 확인한다.
    # 제품 바인딩이 배포되지 않은 서버는 요청의 product 를 조용히 무시하고 macro 키를
    # 만든다. 그걸 모른 채 mPause 자판기에 넣으면 구매자 전원이 인증 실패만 겪고,
    # 관리 화면에서는 그 키들이 평범한 mAuto 재고로 보여 어느 배치가 오염됐는지
    # 추적할 수 없다. product 가 없는 응답은 구버전 서버이므로 macro 로 본다
    # (서버 lib/product.js 의 해석 규칙과 같다).
    issued = str(result.get("product") or DEFAULT_PRODUCT).strip().lower()
    if issued != product:
        raise RuntimeError(
            f"서버가 '{issued}' 제품으로 발급했습니다(요청: '{product}'). "
            "서버를 먼저 배포한 뒤 다시 실행하세요. "
            "방금 만들어진 키는 자판기에 넣지 마세요."
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="자판기용 라이선스 일괄 발급")
    parser.add_argument(
        "--product",
        default=DEFAULT_PRODUCT,
        help="제품 (기본 macro=mAuto, mpause=mPause). 다른 제품 키로는 인증되지 않습니다.",
    )
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
            product=args.product.strip().lower(),
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"[오류] {exc}") from exc

    plain_text = "\n".join(result["keys"])
    if args.plain:
        sys.stdout.write(plain_text)
        return

    period = "무제한" if args.days == VALID_UNLIMITED_DAYS else f"{args.days}일권"
    # 사용자가 친 값이 아니라 **서버가 실제로 발급한 제품**을 찍는다
    # (위에서 불일치를 걸러내므로 여기까지 오면 둘은 같지만, 출력이 늘 사실을 말하게 둔다).
    issued = str(result.get("product") or DEFAULT_PRODUCT).strip().lower()
    print(f"\n[{issued}] {period} {args.count}개 발급 완료")
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

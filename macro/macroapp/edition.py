"""이 실행 파일이 어느 제품인지 — 일반(mAuto)인가 프로(mAuto Pro)인가.

같은 소스에서 **별개의 exe 두 개**를 만든다. 나누는 건 진입점 하나뿐이다:

    macro_main.py      → set_product("macro")      (일반)
    macro_pro_main.py  → set_product("macro_pro")  (프로)

왜 상수가 아니라 모듈인가: 진입점이 정한 값을 나머지 전부가 같은 자리에서 읽어야
한다. 모듈마다 상수를 두면 한 곳만 고치고 다른 곳을 잊는 순간, 프로 빌드가 일반
키로 인증되거나 그 반대가 된다.

⚠️ **반드시 UI 를 만들기 전에, 라이센스를 확인하기 전에** set_product 를 부른다.
그 뒤에 바꾸면 이미 그 값으로 서명을 검증한 결과와 어긋난다.
"""

from __future__ import annotations

PRODUCT_STANDARD = "macro"
PRODUCT_PRO = "macro_pro"

#: 이 프로세스가 무슨 제품으로 도는지. 기본값은 일반이다 — 프로 진입점이 아니면
#: 프로가 될 수 없다(fail-closed).
_product = PRODUCT_STANDARD


def set_product(value: str) -> None:
    """진입점에서 한 번만 부른다. 아는 값이 아니면 일반으로 둔다."""
    global _product
    _product = PRODUCT_PRO if str(value).strip() == PRODUCT_PRO else PRODUCT_STANDARD


def product_id() -> str:
    """라이센스 요청·서명 검증에 쓰는 제품 식별자."""
    return _product


def is_pro() -> bool:
    return _product == PRODUCT_PRO


def display_name() -> str:
    return "mAuto Pro" if is_pro() else "mAuto"


def signature_candidates() -> tuple[str, ...]:
    """서명을 맞춰 볼 제품 후보 — **빌드마다 다르다.**

    일반 빌드: 프로 키도 받아 준다(상위 포함). 그래야 프로 고객이 일반 프로그램을
    켜도 막히지 않는다. 빈 문자열(v1)까지 보는 이유는 서버를 되돌렸을 때 정품
    사용자가 막히지 않게 하기 위해서다.

    프로 빌드: **프로 키만** 받는다. v1 도 받지 않는다 — 서버를 옛 버전으로
    되돌리면 v1 서명이 오는데, 그걸 받아 주면 아무 키로나 프로가 열린다.
    프로 사용자가 잠깐 막히는 쪽이 낫다(fail-closed).
    """
    if is_pro():
        return (PRODUCT_PRO,)
    return (PRODUCT_PRO, PRODUCT_STANDARD, "")

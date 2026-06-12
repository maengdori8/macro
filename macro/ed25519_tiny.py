"""Ed25519 (RFC 8032) 순수 파이썬 구현 — 업데이트 매니페스트 서명/검증 전용.

외부 의존성이 없어 Nuitka/PyInstaller 단일 exe에 그대로 컴파일됩니다.
순수 파이썬이라 느리지만(서명/검증당 ~0.1초) 업데이트 확인처럼 드문 작업엔 충분합니다.

서명 대상 메시지는 호출 측에서 도메인 구분 접두사를 붙여 구성하세요.
(예: "macro-update-v1|<version>|<url>|<sha256>")
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int:
    if y >= _P:
        raise ValueError("invalid point")
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        if sign:
            raise ValueError("invalid point")
        return 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        raise ValueError("invalid point")
    if (x & 1) != sign:
        x = _P - x
    return x


# 확장 좌표(extended homogeneous coordinates) — 점 연산마다 모듈러 역원을 피해서
# 순수 파이썬에서도 실용적인 속도를 냅니다. 항등원은 (0, 1, 1, 0).
def _add(p: tuple, q: tuple) -> tuple:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mult(p: tuple, s: int) -> tuple:
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


_BY = 4 * _inv(5) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)


def _compress(p: tuple) -> bytes:
    x, y, z, _ = p
    zi = _inv(z)
    x = x * zi % _P
    y = y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data: bytes) -> tuple:
    if len(data) != 32:
        raise ValueError("invalid point length")
    n = int.from_bytes(data, "little")
    y = n & ((1 << 255) - 1)
    sign = (n >> 255) & 1
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % _P)


def _equal(p: tuple, q: tuple) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _secret_scalar(seed: bytes) -> tuple[int, bytes]:
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """32바이트 시드에서 32바이트 공개키를 유도합니다."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    a, _ = _secret_scalar(seed)
    return _compress(_mult(_B, a))


def sign(message: bytes, seed: bytes) -> bytes:
    """메시지를 서명해 64바이트 서명을 반환합니다."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    a, prefix = _secret_scalar(seed)
    pub = _compress(_mult(_B, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % _L
    rp = _compress(_mult(_B, r))
    k = int.from_bytes(_sha512(rp + pub + message), "little") % _L
    s = (r + k * a) % _L
    return rp + s.to_bytes(32, "little")


def verify(message: bytes, signature: bytes, pub: bytes) -> bool:
    """서명이 유효하면 True. 예외를 던지지 않고 항상 bool을 반환합니다."""
    try:
        if len(signature) != 64 or len(pub) != 32:
            return False
        a = _decompress(pub)
        rp = _decompress(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(_sha512(signature[:32] + pub + message), "little") % _L
        left = _mult(_B, s)
        right = _add(rp, _mult(a, k))
        return _equal(left, right)
    except Exception:
        return False

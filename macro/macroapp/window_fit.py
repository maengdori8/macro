"""게임 창을 템플릿 기준 WGC 프레임 크기(1928x1048)에 정확히 맞춥니다.

왜 필요한가:
    타깃 템플릿은 1928x1048 프레임에서 잘라낸 픽셀 조각입니다. 다른 크기로 켜면
    window.py의 해상도 자동 보정이 프레임을 리샘플링해서 매칭을 살리지만,
    리샘플링은 정보 손실이라 점수가 떨어집니다. 창을 애초에 기준 크기로 맞추면
    보정 자체가 필요 없어져(배율 1.0) 원본 그대로 매칭합니다.

설계:
    ctypes 접근은 전부 이 모듈에 가두고, 수렴 로직은 '측정 함수'를 주입받는
    순수 로직으로 분리했습니다. 덕분에 Windows 없이도 수렴 동작을 단위 검증할 수 있습니다.
    (갱신매크로 turbo_session.py의 검증된 구현을 창 조정 부분만 옮겨왔습니다.)
"""

from __future__ import annotations

import ctypes
import platform
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

from macroapp.config import TEMPLATE_REFERENCE_SIZE
from macroapp.logging_util import LogCallback

_SW_SHOWMAXIMIZED = 3
_SW_SHOWNOACTIVATE = 4
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

# 키스톤 가드: Windows가 아니면 None이라 Mac에서도 import가 깨지지 않습니다.
try:
    _user32 = ctypes.windll.user32 if platform.system() == "Windows" else None
except Exception:  # noqa: BLE001 - ctypes 로드 실패도 '기능 없음'으로 처리합니다.
    _user32 = None


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowResizeSnapshot:
    """창을 되돌리기 위한 원래 위치·크기입니다."""

    hwnd: int
    original: WindowRect
    resized: WindowRect
    original_was_maximized: bool = False


@dataclass(frozen=True)
class FitResult:
    """창 맞춤 결과. changed=False면 창을 건드리지 않았습니다."""

    ok: bool
    changed: bool
    before: tuple[int, int]
    after: tuple[int, int]
    snapshot: Optional[WindowResizeSnapshot]
    message: str


def _require_user32() -> None:
    if _user32 is None:
        raise RuntimeError("창 크기 조정은 Windows에서만 지원합니다.")


def get_window_rect(hwnd: int) -> WindowRect:
    """유효한 창의 현재 외곽 위치와 크기를 읽습니다."""

    _require_user32()
    if not _user32.IsWindow(int(hwnd)):
        raise RuntimeError("대상 창이 더 이상 유효하지 않습니다.")
    rect = _RECT()
    if not _user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        raise RuntimeError("대상 창 크기를 읽지 못했습니다.")
    return WindowRect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def target_outer_size_for_wgc(
    current_outer: WindowRect,
    current_wgc_size: tuple[int, int],
    target_wgc_size: tuple[int, int] = TEMPLATE_REFERENCE_SIZE,
) -> tuple[int, int]:
    """측정된 '외곽창 - WGC 프레임' 차이만큼만 보정한 목표 외곽 크기를 계산합니다.

    테두리 두께를 추측하지 않고 실측 차이를 그대로 쓰기 때문에 DPI나 테마가 달라도
    맞습니다. 차이가 비정상적으로 크면(다른 창을 잡았거나 측정 오류) 예외를 냅니다.
    """

    current_width = int(current_wgc_size[0])
    current_height = int(current_wgc_size[1])
    target_width = int(target_wgc_size[0])
    target_height = int(target_wgc_size[1])
    if (
        current_width < 640
        or current_height < 480
        or target_width < 640
        or target_height < 480
    ):
        raise ValueError("WGC 프레임이 너무 작아 안전하게 맞출 수 없습니다.")

    width_difference = current_outer.width - current_width
    height_difference = current_outer.height - current_height
    if abs(width_difference) > 64 or abs(height_difference) > 64:
        raise ValueError("외곽창과 WGC 프레임 크기 차이가 64px 안전 한계를 넘었습니다.")
    return (target_width + width_difference, target_height + height_difference)


def resize_window_no_activate(hwnd: int, target_size: tuple[int, int]) -> WindowResizeSnapshot:
    """창을 활성화하거나 Z 순서를 바꾸지 않고 목표 외곽 크기로 조정합니다.

    전면화가 없어야 '비활성 100%' 불변식이 유지됩니다.
    """

    _require_user32()
    original = get_window_rect(hwnd)
    original_was_maximized = bool(_user32.IsZoomed(int(hwnd)))
    width, height = int(target_size[0]), int(target_size[1])
    if width < 640 or height < 480:
        raise ValueError("목표 창 크기가 너무 작습니다.")

    if original_was_maximized:
        # 최대화된 창은 SetWindowPos로 크기를 못 바꿉니다. 활성화·Z순서 변경 없이
        # 일반 배치로만 되돌립니다.
        _user32.ShowWindowAsync(int(hwnd), _SW_SHOWNOACTIVATE)
        deadline = time.monotonic() + 1.0
        while bool(_user32.IsZoomed(int(hwnd))) and time.monotonic() < deadline:
            time.sleep(0.005)
        if bool(_user32.IsZoomed(int(hwnd))):
            raise RuntimeError("최대화된 창을 일반 크기로 되돌리지 못했습니다.")

    if not _user32.SetWindowPos(
        int(hwnd),
        0,
        original.left,
        original.top,
        width,
        height,
        _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    ):
        raise RuntimeError("창 크기 변경이 거부되었습니다.")

    resized = get_window_rect(hwnd)
    if abs(resized.width - width) > 2 or abs(resized.height - height) > 2:
        # 게임이 일부 크기만 적용했으면 원래 상태로 되돌리고 실패로 처리합니다.
        _user32.SetWindowPos(
            int(hwnd),
            0,
            original.left,
            original.top,
            original.width,
            original.height,
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )
        if original_was_maximized:
            _user32.ShowWindowAsync(int(hwnd), _SW_SHOWMAXIMIZED)
        raise RuntimeError(
            f"게임이 목표 크기를 적용하지 않았습니다: {resized.width}x{resized.height}"
        )
    return WindowResizeSnapshot(int(hwnd), original, resized, original_was_maximized)


def restore_window_no_activate(snapshot: WindowResizeSnapshot) -> WindowRect:
    """맞춤 전 창 위치·크기(최대화 여부 포함)로 되돌립니다."""

    _require_user32()
    original = snapshot.original
    if not _user32.SetWindowPos(
        int(snapshot.hwnd),
        0,
        original.left,
        original.top,
        original.width,
        original.height,
        _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    ):
        raise RuntimeError("원래 창 크기 복원이 거부되었습니다.")

    if snapshot.original_was_maximized:
        _user32.ShowWindowAsync(int(snapshot.hwnd), _SW_SHOWMAXIMIZED)
        deadline = time.monotonic() + 1.0
        while (
            not bool(_user32.IsZoomed(int(snapshot.hwnd)))
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)

    restored = get_window_rect(snapshot.hwnd)
    if (
        abs(restored.width - original.width) > 2
        or abs(restored.height - original.height) > 2
    ) and not snapshot.original_was_maximized:
        raise RuntimeError(
            f"원래 창 크기 복원이 완료되지 않았습니다: {restored.width}x{restored.height}"
        )
    return restored


def fit_window_to_reference(
    hwnd: int,
    measure_wgc_size: Callable[[], Optional[tuple[int, int]]],
    *,
    target_wgc_size: tuple[int, int] = TEMPLATE_REFERENCE_SIZE,
    corrections: int = 3,
    logger: Optional[LogCallback] = None,
    resize: Optional[Callable[[int, tuple[int, int]], WindowResizeSnapshot]] = None,
    read_rect: Optional[Callable[[int], WindowRect]] = None,
) -> FitResult:
    """WGC 프레임이 기준 크기가 될 때까지 창을 맞추고, 실패하면 되돌립니다.

    measure_wgc_size는 '지금 WGC 프레임 크기'를 돌려주는 함수입니다. 실제 캡처를
    주입받는 구조라 Windows 없이도 수렴 로직을 검증할 수 있습니다.
    resize/read_rect도 주입 가능하며, 생략하면 실제 Win32 구현을 씁니다.
    """

    log = logger or (lambda _message: None)
    do_resize = resize or resize_window_no_activate
    do_read = read_rect or get_window_rect
    target = (int(target_wgc_size[0]), int(target_wgc_size[1]))

    before = measure_wgc_size()
    if before is None:
        return FitResult(False, False, (0, 0), (0, 0), None, "WGC 프레임을 받지 못했습니다.")
    before = (int(before[0]), int(before[1]))

    if before == target:
        return FitResult(
            True, False, before, before, None,
            f"이미 기준 크기입니다 ({before[0]}x{before[1]}). 창을 건드리지 않았습니다.",
        )

    snapshot: Optional[WindowResizeSnapshot] = None
    try:
        outer = do_read(hwnd)
        snapshot = do_resize(hwnd, target_outer_size_for_wgc(outer, before, target))
        for attempt in range(max(1, corrections)):
            current = measure_wgc_size()
            if current is None:
                raise RuntimeError("맞춘 뒤 WGC 프레임을 받지 못했습니다.")
            current = (int(current[0]), int(current[1]))
            if current == target:
                log(
                    f"[창 맞춤] {before[0]}x{before[1]} → {current[0]}x{current[1]} "
                    f"(보정 {attempt}회)"
                )
                return FitResult(
                    True, True, before, current, snapshot,
                    f"기준 크기로 맞췄습니다 ({current[0]}x{current[1]}).",
                )
            if attempt >= corrections - 1:
                break
            # 남은 차이만큼 한 번 더 보정합니다(게임이 크기를 살짝 다르게 적용하는 경우).
            snapshot = do_resize(
                hwnd,
                target_outer_size_for_wgc(do_read(hwnd), current, target),
            )
        raise RuntimeError(f"기준 크기로 수렴하지 못했습니다: {current[0]}x{current[1]}")
    except Exception as exc:  # noqa: BLE001 - 실패해도 창은 반드시 되돌립니다.
        if snapshot is not None:
            try:
                restore_window_no_activate(snapshot)
            except Exception as restore_error:  # noqa: BLE001
                log(f"[창 맞춤] 되돌리기도 실패했습니다: {restore_error}")
        return FitResult(False, False, before, before, None, f"창 맞춤 실패: {exc}")

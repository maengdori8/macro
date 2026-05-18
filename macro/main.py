"""
Windows inactive-window image matching and click GUI example.

목적:
- tkinter UI에서 대상 창 제목을 입력하고 자동화를 시작/중지합니다.
- 특정 Windows 창이 다른 창 뒤에 가려져 있어도 클라이언트 영역을 캡처합니다.
- OpenCV 템플릿 매칭으로 target_A/B/C.png를 찾습니다.
- 실제 마우스 커서를 움직이지 않고 PostMessage로 클릭 메시지를 보냅니다.
주의:
- 최소화된 창은 지원하지 않습니다.
- 일부 프로그램은 PrintWindow 캡처 또는 PostMessage 클릭을 지원하지 않을 수 있습니다.
- 허가된 사내 프로그램, 개인 학습, RPA 테스트 목적의 예시 코드입니다.
"""

from __future__ import annotations

import ctypes
import platform
import queue
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import scrolledtext
from typing import Callable, Optional

import cv2
import numpy as np

try:
    import pyautogui
except ImportError as exc:
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = exc
else:
    PYAUTOGUI_IMPORT_ERROR = None
    pyautogui.FAILSAFE = True

try:
    # pywin32는 Windows 전용 패키지입니다.
    # Mac 개발 환경에서는 설치되지 않는 것이 정상이라 Pylance 경고만 무시합니다.
    import win32api  # type: ignore[reportMissingModuleSource]
    import win32con  # type: ignore[reportMissingModuleSource]
    import win32gui  # type: ignore[reportMissingModuleSource]
    import win32ui  # type: ignore[reportMissingModuleSource]
except ImportError as exc:
    win32api = None
    win32con = None
    win32gui = None
    win32ui = None
    WIN32_IMPORT_ERROR = exc
else:
    WIN32_IMPORT_ERROR = None


# UI 입력칸의 기본값입니다. 사용자는 프로그램 실행 후 UI에서 수정할 수 있습니다.
# 예: "메모장", "Notepad", "계산기", "Chrome"
WINDOW_TITLE = "대상 창 제목 일부를 입력하세요"

# 아무것도 발견되지 않았을 때 CPU 과부하를 막기 위한 기본 대기 시간입니다.
LOOP_SLEEP_SECONDS = 0.5

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# 매칭 영역 중심 주변에서 클릭 좌표를 약간 조정합니다.
# 허가된 UI 테스트에서 고정 좌표 취약성을 줄이기 위한 안정화 값입니다.
CLICK_JITTER_PIXELS = 3

# WM_LBUTTONDOWN과 WM_LBUTTONUP 사이의 짧은 지연입니다.
CLICK_MESSAGE_DELAY_SECONDS = 0.05

# 화면 영역 캡처 모드의 기본 영역입니다.
DEFAULT_REGION_X = 0
DEFAULT_REGION_Y = 0
DEFAULT_REGION_WIDTH = 1280
DEFAULT_REGION_HEIGHT = 720

LogCallback = Callable[[str], None]


@dataclass
class TargetImage:
    """탐지할 이미지 정보입니다."""

    name: str
    filename: str
    wait_after_click: float
    threshold: float = 0.8

    # load_targets()에서 GrayScale 이미지가 채워집니다.
    # repr=False로 두면 로그에 큰 NumPy 배열 내용이 출력되지 않습니다.
    image_gray: Optional[np.ndarray] = field(default=None, repr=False)


class InactiveManager:
    """비활성 Windows 창 캡처와 메시지 클릭을 담당합니다."""

    def __init__(self, window_title: str, logger: Optional[LogCallback] = None):
        self.window_title = window_title
        self.hwnd: Optional[int] = None
        self.window_text: str = ""
        self.logger = logger or print

        if WIN32_IMPORT_ERROR is not None:
            self.log("[오류] pywin32 모듈을 불러올 수 없습니다.")
            self.log("       이 프로그램은 Windows + pywin32 환경에서 실행해야 합니다.")
            self.log(f"       원본 오류: {WIN32_IMPORT_ERROR}")

        if platform.system() != "Windows":
            self.log("[주의] 현재 OS는 Windows가 아닙니다.")
            self.log("       코드는 작성/검토할 수 있지만 실제 캡처와 클릭은 Windows에서 실행하세요.")

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def find_window(self) -> bool:
        """
        WINDOW_TITLE 문자열이 제목에 포함된 창을 찾습니다.

        정확히 일치하지 않아도 되도록 부분 문자열로 검색합니다.
        같은 제목의 창이 여러 개 있을 수 있으므로 모든 후보 HWND와 제목을 출력합니다.
        """

        if not self._win32_ready():
            return False

        keyword = self.window_title.strip().lower()
        if not keyword:
            self.log("[오류] 대상 창 제목이 비어 있습니다.")
            self.log("       UI의 대상 창 제목 입력칸에 찾을 창 제목 일부를 입력하세요.")
            return False

        matches: list[tuple[int, str]] = []
        minimized_matches: list[tuple[int, str]] = []

        def enum_handler(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindow(hwnd):
                return
            if not win32gui.IsWindowVisible(hwnd):
                return

            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            if keyword not in title.lower():
                return

            if win32gui.IsIconic(hwnd):
                minimized_matches.append((hwnd, title))
                return

            matches.append((hwnd, title))

        try:
            win32gui.EnumWindows(enum_handler, None)
        except Exception as exc:
            self.log(f"[오류] 창 검색 중 문제가 발생했습니다: {exc}")
            return False

        self.log(f"[창 검색] 제목에 '{self.window_title}' 포함된 창을 검색했습니다.")

        if minimized_matches:
            self.log("[제외] 최소화된 창은 지원하지 않아 제외했습니다.")
            for hwnd, title in minimized_matches:
                self.log(f"       HWND={hwnd}, 제목='{title}'")

        if not matches:
            self.log("[안내] 사용 가능한 대상 창을 찾지 못했습니다.")
            self.log("       창 제목을 확인하거나, 대상 창이 최소화되어 있지 않은지 확인하세요.")
            return False

        self.log("[발견] 사용 가능한 창 후보:")
        for hwnd, title in matches:
            self.log(f"       HWND={hwnd}, 제목='{title}'")

        self.hwnd, self.window_text = matches[0]
        self.log(f"[선택] HWND={self.hwnd}, 제목='{self.window_text}' 창을 사용합니다.")
        return True

    def is_valid_window(self) -> bool:
        """현재 저장된 HWND가 캡처 가능한 상태인지 확인합니다."""

        if not self._win32_ready():
            return False
        if self.hwnd is None:
            return False

        try:
            if not win32gui.IsWindow(self.hwnd):
                self.log("[주의] 대상 HWND가 더 이상 유효하지 않습니다.")
                return False
            if not win32gui.IsWindowVisible(self.hwnd):
                self.log("[주의] 대상 창이 보이지 않는 상태입니다.")
                return False
            if win32gui.IsIconic(self.hwnd):
                self.log("[주의] 대상 창이 최소화되어 있습니다. 최소화된 창은 지원하지 않습니다.")
                return False

            left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                self.log("[주의] 대상 창의 클라이언트 영역 크기가 0입니다.")
                return False

            return True
        except Exception as exc:
            self.log(f"[주의] 대상 창 상태 확인 중 오류가 발생했습니다: {exc}")
            return False

    def capture_client_area(self) -> Optional[np.ndarray]:
        """
        대상 창의 클라이언트 영역을 캡처하고 GrayScale NumPy 배열로 반환합니다.

        GDI 리소스 흐름:
        1. GetWindowDC로 원본 창 DC 핸들을 얻습니다.
        2. win32ui.CreateDCFromHandle로 pywin32 DC 객체를 만듭니다.
        3. CreateCompatibleDC로 메모리 DC를 만듭니다.
        4. CreateCompatibleBitmap으로 캡처 결과를 담을 HBITMAP을 만듭니다.
        5. PrintWindow(PW_CLIENTONLY)로 클라이언트 영역만 메모리 DC에 그립니다.
        6. GetBitmapBits로 BGRA/BGR 바이트를 NumPy 배열로 변환합니다.
        7. finally에서 HBITMAP, compatible DC, 원본 DC를 반드시 해제합니다.
        """

        if not self.is_valid_window():
            return None

        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        width = right - left
        height = bottom - top

        hwnd_dc = None
        source_dc = None
        memory_dc = None
        bitmap = None
        old_bitmap = None

        try:
            # 원본 창 DC를 얻습니다. PrintWindow는 이 DC와 호환되는 메모리 DC에 그립니다.
            hwnd_dc = win32gui.GetWindowDC(self.hwnd)
            if not hwnd_dc:
                self.log("[오류] GetWindowDC가 실패했습니다.")
                return None

            # HDC 핸들을 pywin32 DC 객체로 감쌉니다.
            source_dc = win32ui.CreateDCFromHandle(hwnd_dc)

            # 화면이 아니라 메모리에 그릴 compatible DC를 만듭니다.
            memory_dc = source_dc.CreateCompatibleDC()

            # 클라이언트 영역 크기만큼 compatible bitmap을 만듭니다.
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source_dc, width, height)

            # bitmap을 memory_dc에 선택해야 PrintWindow 결과가 bitmap에 기록됩니다.
            old_bitmap = memory_dc.SelectObject(bitmap)

            for flag_name, flag_value in self._print_window_flag_candidates():
                self.log(f"[캡처 시도] PrintWindow 플래그={flag_name}")
                printed = self._print_window(
                    self.hwnd,
                    memory_dc.GetSafeHdc(),
                    flag_value,
                )
                if printed != 1:
                    self.log(
                        f"[캡처 오류] PrintWindow 실패 "
                        f"(플래그: {flag_name}, 결과값: {printed})"
                    )
                    continue

                bitmap_info = bitmap.GetInfo()
                bitmap_bytes = bitmap.GetBitmapBits(True)
                image_bgr = self._bitmap_bytes_to_bgr(bitmap_bytes, bitmap_info)
                if image_bgr is None:
                    continue

                if image_bgr.size == 0:
                    self.log(f"[캡처 오류] 캡처 결과가 비어 있습니다. (플래그: {flag_name})")
                    continue

                if np.all(image_bgr == 0):
                    self.log(
                        f"[캡처 오류] 캡처 이미지가 완전히 검은색입니다. "
                        f"(플래그: {flag_name})"
                    )
                    continue

                mean_value = float(image_bgr.mean())
                std_value = float(image_bgr.std())
                if mean_value < 1.0 and std_value < 1.0:
                    self.log(
                        f"[캡처 오류] 캡처 결과가 거의 검은 화면입니다. "
                        f"(플래그: {flag_name})"
                    )
                    continue

                if std_value < 0.2:
                    self.log(
                        f"[주의] 캡처 결과가 거의 단색입니다. "
                        f"(플래그: {flag_name})"
                    )
                    self.log("       정상 화면인지, 빈 화면이 캡처된 것인지 확인이 필요합니다.")

                self.log(f"[캡처 성공] 클라이언트 영역 {width}x{height}, 플래그={flag_name}")

                # OpenCV 템플릿 매칭을 위해 GrayScale로 변환합니다.
                return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

            self.log("[캡처 오류] 모든 PrintWindow 플래그 재시도가 실패했습니다.")
            self.log("       대상 앱이 백그라운드 캡처를 지원하지 않거나 다른 캡처 방식이 필요할 수 있습니다.")
            self.save_printwindow_failure_debug_screenshot()
            return None

        except Exception as exc:
            self.log(f"[오류] 클라이언트 영역 캡처 중 예외가 발생했습니다: {exc}")
            return None

        finally:
            # SelectObject로 바꿔 끼운 bitmap을 원래 객체로 되돌립니다.
            # 이 과정을 거치면 bitmap 삭제 시 GDI 리소스가 더 안전하게 정리됩니다.
            try:
                if memory_dc is not None and old_bitmap is not None:
                    memory_dc.SelectObject(old_bitmap)
            except Exception:
                pass

            # HBITMAP 해제: 캡처 루프마다 새로 생성되므로 반드시 삭제해야 합니다.
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass

            # 메모리 compatible DC 해제.
            try:
                if memory_dc is not None:
                    memory_dc.DeleteDC()
            except Exception:
                pass

            # CreateDCFromHandle로 만든 DC 객체 해제.
            try:
                if source_dc is not None:
                    source_dc.DeleteDC()
            except Exception:
                pass

            # GetWindowDC로 얻은 원본 DC 핸들은 ReleaseDC로 반환해야 합니다.
            try:
                if hwnd_dc is not None:
                    win32gui.ReleaseDC(self.hwnd, hwnd_dc)
            except Exception:
                pass

    def post_click(self, x: int, y: int) -> bool:
        """
        실제 마우스 커서를 움직이지 않고 대상 창에 클릭 메시지를 보냅니다.

        좌표는 캡처된 클라이언트 영역 기준입니다.
        일부 프로그램은 보안 정책, 자체 입력 처리 방식, DirectX/게임 렌더링 구조 때문에
        PostMessage 기반 클릭을 무시할 수 있습니다.
        """

        if not self.is_valid_window():
            return False

        try:
            x = int(x)
            y = int(y)
            lparam = self._make_lparam(x, y)

            self.log(f"[클릭 전송] HWND={self.hwnd}, client=({x}, {y})")
            win32gui.PostMessage(
                self.hwnd,
                win32con.WM_LBUTTONDOWN,
                win32con.MK_LBUTTON,
                lparam,
            )
            time.sleep(CLICK_MESSAGE_DELAY_SECONDS)
            win32gui.PostMessage(
                self.hwnd,
                win32con.WM_LBUTTONUP,
                0,
                lparam,
            )
            self.log("[클릭 완료] WM_LBUTTONDOWN / WM_LBUTTONUP 메시지를 보냈습니다.")
            return True
        except Exception as exc:
            self.log(f"[오류] 클릭 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def client_to_screen(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """클라이언트 영역 기준 좌표를 화면 절대 좌표로 변환합니다."""

        if not self.is_valid_window():
            return None

        try:
            screen_x, screen_y = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
            return int(screen_x), int(screen_y)
        except Exception as exc:
            self.log(f"[오류] 클라이언트 좌표를 화면 좌표로 변환하지 못했습니다: {exc}")
            return None

    def _win32_ready(self) -> bool:
        """pywin32 모듈이 정상 로드되었는지 확인합니다."""

        return WIN32_IMPORT_ERROR is None

    def _print_window(self, hwnd: int, hdc: int, flags: int) -> int:
        """pywin32의 PrintWindow를 우선 사용하고, 없으면 ctypes로 호출합니다."""

        if hasattr(win32gui, "PrintWindow"):
            return int(win32gui.PrintWindow(hwnd, hdc, flags))

        # 오래된 pywin32에서 PrintWindow 래퍼가 없을 때의 보조 경로입니다.
        return int(ctypes.windll.user32.PrintWindow(hwnd, hdc, flags))

    def save_printwindow_failure_debug_screenshot(self) -> None:
        """
        PrintWindow가 실패했을 때 당시 전체 화면을 디버깅용으로 저장합니다.

        이 함수는 GDI 리소스를 직접 건드리지 않습니다. GDI 해제는 capture_client_area()
        finally 블록이 담당하므로 여기서는 pyautogui 전체 화면 스냅샷만 시도합니다.
        """

        self.log("[시스템 경고] PrintWindow API가 모든 플래그에서 실패했습니다.")
        self.log("               원인: 권한 부족(UIPI), 하드웨어 가속, 또는 대상 창의 그래픽 차단 가능성.")

        if pyautogui is None:
            self.log("[디버그] pyautogui를 불러올 수 없어 전체 화면 디버그 저장을 건너뜁니다.")
            self.log(f"         원본 오류: {PYAUTOGUI_IMPORT_ERROR}")
            return

        try:
            debug_path = Path(__file__).resolve().parent / "DEBUG_PRINTWINDOW_FAILED.png"
            fallback_screenshot = pyautogui.screenshot()
            fallback_screenshot.save(debug_path)
            self.log(
                "[디버그] PrintWindow 실패 당시 전체 화면을 "
                f"'{debug_path.name}'로 저장했습니다."
            )
        except Exception as exc:
            self.log(f"[디버그] 임시 스크린샷 저장마저 실패했습니다: {exc}")

    def _print_window_flag_candidates(self) -> list[tuple[str, int]]:
        """
        PrintWindow 플래그 후보를 반환합니다.

        앱마다 응답하는 PrintWindow 플래그가 달라서 여러 방식을 순차 시도합니다.
        PW_CLIENTONLY는 클라이언트 영역 중심이고, PW_RENDERFULLCONTENT는 일부
        Chromium/Electron/스크롤 가능 창에서 더 나은 결과를 주는 경우가 있습니다.
        """

        pw_client_only = getattr(win32con, "PW_CLIENTONLY", 0x00000001)
        pw_render_full_content = getattr(win32con, "PW_RENDERFULLCONTENT", 0x00000002)
        candidates = [
            ("PW_CLIENTONLY", pw_client_only),
            (
                "PW_CLIENTONLY|PW_RENDERFULLCONTENT",
                pw_client_only | pw_render_full_content,
            ),
            ("PW_RENDERFULLCONTENT", pw_render_full_content),
            ("0", 0),
        ]

        # 혹시 같은 값이 중복될 경우 로그가 헷갈리지 않도록 제거합니다.
        deduped: list[tuple[str, int]] = []
        seen_values: set[int] = set()
        for name, value in candidates:
            if value in seen_values:
                continue
            deduped.append((name, value))
            seen_values.add(value)

        return deduped

    def _bitmap_bytes_to_bgr(
        self,
        bitmap_bytes: bytes,
        bitmap_info: dict[str, int],
    ) -> Optional[np.ndarray]:
        """
        HBITMAP의 raw bytes를 OpenCV BGR 배열로 변환합니다.

        Windows bitmap은 환경에 따라 BGRA(4채널) 또는 BGR(3채널)에 가까운 형태로
        반환될 수 있습니다. bmBitsPixel, bmWidthBytes를 확인해서 명확히 처리합니다.
        """

        try:
            width = int(bitmap_info["bmWidth"])
            height = int(bitmap_info["bmHeight"])
            bits_per_pixel = int(bitmap_info["bmBitsPixel"])
            row_bytes = int(bitmap_info["bmWidthBytes"])
            bytes_per_pixel = bits_per_pixel // 8

            if width <= 0 or height <= 0:
                self.log("[오류] bitmap 크기가 올바르지 않습니다.")
                return None

            if bytes_per_pixel not in (3, 4):
                self.log(f"[오류] 지원하지 않는 bitmap 비트 깊이입니다: {bits_per_pixel} bpp")
                return None

            expected_without_padding = width * height * bytes_per_pixel
            raw_array = np.frombuffer(bitmap_bytes, dtype=np.uint8)

            if len(bitmap_bytes) == expected_without_padding:
                shaped = raw_array.reshape((height, width, bytes_per_pixel))
            else:
                # scanline padding이 포함된 경우 row_bytes 기준으로 행을 나눈 뒤,
                # 실제 픽셀 영역만 잘라냅니다.
                shaped_rows = raw_array.reshape((height, row_bytes))
                pixel_bytes = shaped_rows[:, : width * bytes_per_pixel]
                shaped = pixel_bytes.reshape((height, width, bytes_per_pixel))

            if bytes_per_pixel == 4:
                # compatible bitmap의 4채널 값은 보통 BGRA/BGRX 순서입니다.
                # OpenCV 매칭은 BGR/GrayScale이면 충분하므로 알파 채널을 제거합니다.
                return cv2.cvtColor(shaped, cv2.COLOR_BGRA2BGR)

            # 3채널 bitmap은 OpenCV가 사용하는 BGR 순서로 취급합니다.
            return shaped.copy()

        except Exception as exc:
            self.log(f"[오류] bitmap 데이터를 NumPy 배열로 변환하지 못했습니다: {exc}")
            return None

    def _make_lparam(self, x: int, y: int) -> int:
        """
        Windows 마우스 메시지 lParam을 만듭니다.

        하위 16비트는 x 좌표, 상위 16비트는 y 좌표입니다.
        win32api.MAKELONG이 있으면 사용하고, 없으면 동일한 방식으로 직접 구성합니다.
        """

        if hasattr(win32api, "MAKELONG"):
            return int(win32api.MAKELONG(x, y))

        return (y & 0xFFFF) << 16 | (x & 0xFFFF)


class AutomationApp:
    """tkinter UI와 자동화 스레드를 관리합니다."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("비활성 창 이미지 자동화 테스트")
        self.root.geometry("980x760+80+80")
        self.root.minsize(900, 640)
        self.ui_preview_only = platform.system() != "Windows"
        if self.ui_preview_only:
            self.root.title("비활성 창 이미지 자동화 테스트 - UI 미리보기")

        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        initial_status = "UI 미리보기" if self.ui_preview_only else "대기 중"
        self.status_var = tk.StringVar(value=initial_status)
        self.threshold_lock = threading.Lock()
        self.threshold_values = {
            "target_A": 0.8,
            "target_B": 0.8,
            "target_C": 0.8,
        }
        self.threshold_vars = {
            name: tk.DoubleVar(value=value)
            for name, value in self.threshold_values.items()
        }
        self.threshold_label_vars = {
            name: tk.StringVar(value=f"{value:.2f}")
            for name, value in self.threshold_values.items()
        }
        self.capture_mode_var = tk.StringVar(value="printwindow")
        self.click_mode_var = tk.StringVar(value="postmessage")
        self.region_vars = {
            "x": tk.IntVar(value=DEFAULT_REGION_X),
            "y": tk.IntVar(value=DEFAULT_REGION_Y),
            "width": tk.IntVar(value=DEFAULT_REGION_WIDTH),
            "height": tk.IntVar(value=DEFAULT_REGION_HEIGHT),
        }

        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.closing = False

        self._build_ui()
        self._bind_shortcuts()
        self._set_button_state(running=False)
        self._bring_window_to_front()
        self._poll_ui_queue()

        self.log("프로그램을 시작했습니다.")
        self.log("F8: 시작, F9 또는 ESC: 중지")
        self.log("대상 창은 백그라운드에 있어도 되지만 최소화되어 있으면 안 됩니다.")
        if self.ui_preview_only:
            self.log("[UI 미리보기] 현재 OS가 Windows가 아니므로 자동화 실행은 비활성화했습니다.")
            self.log("              UI 확인만 가능하며, 실제 캡처/클릭은 Windows에서 실행하세요.")

    def _bring_window_to_front(self) -> None:
        """창이 뒤에 숨어 보이지 않는 일을 줄이기 위해 잠깐 최상위로 올립니다."""

        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

        # macOS에서 처음 뜬 tkinter 창이 다른 앱 뒤로 가는 경우가 있어 잠깐만 topmost를 켭니다.
        self.root.attributes("-topmost", True)
        self.root.after(1200, lambda: self.root.attributes("-topmost", False))

    def _build_ui(self) -> None:
        """간단한 테스트 도구 형태의 UI를 만듭니다."""

        if self.ui_preview_only:
            self._build_preview_ui()
            return

        # macOS의 기본 Tcl/Tk는 다크 모드에서 배경색을 강하게 바꾸는 경우가 있어,
        # 미리보기에서도 확실히 읽히도록 어두운 배경 + 밝은 글자로 고정합니다.
        bg = "#1E1E1E"
        panel_bg = "#252526"
        input_bg = "#111827"
        text_color = "#F9FAFB"
        accent = "#7AB7FF"

        self.root.configure(bg=bg)

        main_frame = tk.Frame(self.root, bg=bg, padx=12, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        title_frame.pack(fill=tk.X)

        tk.Label(
            title_frame,
            text="대상 창",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            title_frame,
            text="창 제목 일부",
            bg=panel_bg,
            fg=text_color,
        ).pack(anchor=tk.W, pady=(8, 4))
        self.title_entry = tk.Entry(
            title_frame,
            textvariable=self.window_title_var,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
        )
        self.title_entry.pack(fill=tk.X)

        button_frame = tk.Frame(main_frame, bg=bg)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8)",
            command=self.start_automation,
            width=14,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=14,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        status_frame = tk.Frame(main_frame, bg=bg)
        status_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Label(status_frame, text="상태", bg=bg, fg=text_color).pack(side=tk.LEFT)
        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=bg,
            fg=accent,
        )
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        mode_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        mode_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            mode_frame,
            text="캡처/클릭 모드",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        capture_row = tk.Frame(mode_frame, bg=panel_bg)
        capture_row.pack(fill=tk.X, pady=2)
        tk.Label(
            capture_row,
            text="캡처",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="비활성 PrintWindow",
            variable=self.capture_mode_var,
            value="printwindow",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="화면 영역 캡처",
            variable=self.capture_mode_var,
            value="region",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        click_row = tk.Frame(mode_frame, bg=panel_bg)
        click_row.pack(fill=tk.X, pady=2)
        tk.Label(
            click_row,
            text="클릭",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="PostMessage",
            variable=self.click_mode_var,
            value="postmessage",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="마우스 이동 후 복귀",
            variable=self.click_mode_var,
            value="mouse",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        region_row = tk.Frame(mode_frame, bg=panel_bg)
        region_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            region_row,
            text="영역",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        for key, label in (
            ("x", "X"),
            ("y", "Y"),
            ("width", "W"),
            ("height", "H"),
        ):
            tk.Label(region_row, text=label, bg=panel_bg, fg=text_color).pack(side=tk.LEFT)
            tk.Entry(
                region_row,
                textvariable=self.region_vars[key],
                width=7,
                bg=input_bg,
                fg=text_color,
                insertbackground=text_color,
                relief=tk.SOLID,
                bd=1,
            ).pack(side=tk.LEFT, padx=(4, 10))

        threshold_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        threshold_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            threshold_frame,
            text="타겟별 임계값",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        for name in ("target_A", "target_B", "target_C"):
            row = tk.Frame(threshold_frame, bg=panel_bg)
            row.pack(fill=tk.X, pady=2)

            tk.Label(
                row,
                text=name,
                bg=panel_bg,
                fg=text_color,
                width=10,
                anchor=tk.W,
            ).pack(side=tk.LEFT)

            tk.Scale(
                row,
                from_=0.5,
                to=1.0,
                resolution=0.01,
                orient=tk.HORIZONTAL,
                variable=self.threshold_vars[name],
                command=lambda value, target_name=name: self.update_threshold(
                    target_name,
                    value,
                ),
                bg=panel_bg,
                fg=text_color,
                troughcolor=input_bg,
                highlightthickness=0,
                length=360,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            tk.Label(
                row,
                textvariable=self.threshold_label_vars[name],
                bg=panel_bg,
                fg=accent,
                width=5,
            ).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        tk.Label(
            log_frame,
            text="로그",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=22,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_preview_ui(self) -> None:
        """
        Mac 전용 UI 미리보기입니다.

        일부 Mac Tcl/Tk 조합에서는 Label, Entry, Text가 보이지 않는 경우가 있어
        미리보기 모드에서는 화면에 확실히 표시되는 Button 위젯만 사용합니다.
        Windows 실행 시에는 위의 일반 UI가 사용됩니다.
        """

        self.root.configure(bg="#1E1E1E")
        main_frame = tk.Frame(self.root, bg="#1E1E1E", padx=18, pady=18)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.title_entry = tk.Button(
            main_frame,
            text=f"대상 창 제목: {self.window_title_var.get()}",
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.title_entry.pack(fill=tk.X, pady=(0, 12))

        button_frame = tk.Frame(main_frame, bg="#1E1E1E")
        button_frame.pack(fill=tk.X, pady=(0, 12))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8) - 미리보기 로그만 표시",
            command=self.start_automation,
            width=28,
            height=2,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=18,
            height=2,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(10, 0))

        self.status_label = tk.Button(
            main_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.status_label.pack(fill=tk.X, pady=(0, 12))

        mode_text = (
            f"캡처 모드: {self.capture_mode_var.get()}\n"
            f"클릭 모드: {self.click_mode_var.get()}\n"
            f"영역: {DEFAULT_REGION_X}, {DEFAULT_REGION_Y}, "
            f"{DEFAULT_REGION_WIDTH}, {DEFAULT_REGION_HEIGHT}"
        )
        self.preview_mode_button = tk.Button(
            main_frame,
            text=mode_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=3,
        )
        self.preview_mode_button.pack(fill=tk.X, pady=(0, 12))

        threshold_text = "\n".join(
            f"{name} 임계값: {self.get_threshold(name):.2f}"
            for name in ("target_A", "target_B", "target_C")
        )
        self.preview_threshold_button = tk.Button(
            main_frame,
            text=threshold_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=3,
        )
        self.preview_threshold_button.pack(fill=tk.X, pady=(0, 12))

        self.preview_log_messages: list[str] = []
        self.preview_log_frame = tk.Frame(
            main_frame,
            relief=tk.GROOVE,
            bd=2,
        )
        self.preview_log_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_log_rows: list[tk.Button] = []
        for _index in range(12):
            row = tk.Button(
                self.preview_log_frame,
                text="",
                anchor=tk.W,
                justify=tk.LEFT,
                relief=tk.FLAT,
                height=1,
            )
            row.pack(fill=tk.X, padx=6, pady=1)
            self.preview_log_rows.append(row)

    def _bind_shortcuts(self) -> None:
        """UI가 활성화되어 있을 때 동작하는 단축키를 등록합니다."""

        self.root.bind("<F8>", lambda _event: self.start_automation())
        self.root.bind("<F9>", lambda _event: self.stop_automation())
        self.root.bind("<Escape>", lambda _event: self.stop_automation())

        # 기존 콘솔 예제의 q 종료 습관을 보조로 유지합니다.
        self.root.bind("<KeyPress-q>", lambda _event: self.stop_automation())
        self.root.bind("<KeyPress-Q>", lambda _event: self.stop_automation())

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_capture_mode_changed(self) -> None:
        """화면 영역 캡처 모드에서는 기본 클릭 방식을 마우스 이동/복귀로 맞춥니다."""

        if self.capture_mode_var.get() == "region":
            self.click_mode_var.set("mouse")
            self.log("[설정] 화면 영역 캡처 모드에서는 마우스 이동 후 복귀 클릭을 사용합니다.")

    def get_region_from_ui(self) -> Optional[tuple[int, int, int, int]]:
        """UI의 영역 입력값을 안전하게 읽습니다."""

        try:
            x = int(self.region_vars["x"].get())
            y = int(self.region_vars["y"].get())
            width = int(self.region_vars["width"].get())
            height = int(self.region_vars["height"].get())
        except Exception as exc:
            self.log(f"[오류] 화면 영역 값이 올바른 숫자가 아닙니다: {exc}")
            return None

        if width <= 0 or height <= 0:
            self.log("[오류] 화면 영역의 W/H는 1 이상이어야 합니다.")
            return None

        return x, y, width, height

    def start_automation(self) -> None:
        """시작 버튼 또는 F8 키로 자동화 스레드를 시작합니다."""

        if self.ui_preview_only:
            self.log("[UI 미리보기] Mac에서는 자동화 루프를 실행하지 않습니다.")
            self.log("              Windows에서 실행하면 시작 버튼과 F8이 자동화를 시작합니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[안내] 이미 실행 중입니다.")
            self.set_status("실행 중")
            return

        capture_mode = self.capture_mode_var.get()
        click_mode = self.click_mode_var.get()
        region = self.get_region_from_ui()
        if region is None:
            self.set_status("오류 발생")
            return

        if capture_mode == "region":
            click_mode = "mouse"
            self.click_mode_var.set("mouse")

        window_title = self.window_title_var.get().strip()
        if capture_mode == "printwindow" and not window_title:
            self.log("[오류] 대상 창 제목을 입력하세요.")
            self.set_status("오류 발생")
            return

        self.stop_event.clear()
        self._set_button_state(running=True)
        self.set_status("실행 중")
        self.log(
            f"[시작] 자동화를 시작합니다. "
            f"캡처={capture_mode}, 클릭={click_mode}, 영역={region}, 창='{window_title}'"
        )

        self.worker_thread = threading.Thread(
            target=self._automation_loop,
            args=(window_title, capture_mode, click_mode, region),
            daemon=True,
        )
        self.worker_thread.start()

    def update_threshold(self, target_name: str, value: str) -> None:
        """슬라이더 값 변경을 thread-safe하게 저장합니다."""

        threshold = max(0.0, min(1.0, float(value)))
        with self.threshold_lock:
            self.threshold_values[target_name] = threshold
        self.threshold_label_vars[target_name].set(f"{threshold:.2f}")

    def get_threshold(self, target_name: str) -> float:
        """작업 스레드에서 사용할 현재 임계값을 가져옵니다."""

        with self.threshold_lock:
            return float(self.threshold_values.get(target_name, 0.8))

    def apply_current_thresholds(self, targets: list[TargetImage]) -> None:
        """UI에서 조절한 임계값을 TargetImage 목록에 반영합니다."""

        for target in targets:
            target.threshold = self.get_threshold(target.name)

    def stop_automation(self) -> None:
        """종료 버튼, F9, ESC, q 키로 자동화를 안전하게 중단 요청합니다."""

        if self.ui_preview_only:
            self.log("[UI 미리보기] 실행 중인 자동화가 없습니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.log("[안내] 현재 실행 중인 자동화가 없습니다.")
            self.set_status("대기 중")
            self._set_button_state(running=False)
            return

        self.stop_event.set()
        self.set_status("종료 요청됨")
        self.log("[중지 요청] 자동화 루프를 안전하게 중단합니다.")
        self.stop_button.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        """창 닫기 버튼을 눌렀을 때도 자동화 스레드를 자연스럽게 멈춥니다."""

        self.closing = True
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
            self.log("[종료 요청] 창 닫기 전에 자동화 스레드를 중단합니다.")
            self.set_status("종료 요청됨")
            self.root.after(100, self._destroy_when_worker_stops)
            return

        self.root.destroy()

    def _destroy_when_worker_stops(self) -> None:
        """작업 스레드가 끝난 뒤 tkinter 창을 닫습니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.root.after(100, self._destroy_when_worker_stops)
            return
        self.root.destroy()

    def _automation_loop(
        self,
        window_title: str,
        capture_mode: str,
        click_mode: str,
        region: tuple[int, int, int, int],
    ) -> None:
        """
        실제 자동화 루프입니다.

        tkinter는 메인 스레드에서만 UI를 만지는 것이 안전하므로,
        이 함수는 직접 라벨/로그 위젯을 수정하지 않고 queue에 메시지만 넣습니다.
        """

        try:
            self.queue_status("대상 이미지 로드 중")
            base_dir = Path(__file__).resolve().parent
            targets = load_targets(base_dir, self.queue_log)
            if targets is None:
                self.queue_status("오류 발생")
                self.queue_log("[종료] 타겟 이미지 준비에 실패하여 실행을 중단합니다.")
                return

            manager: Optional[InactiveManager] = None
            if capture_mode == "printwindow" or click_mode == "postmessage":
                manager = InactiveManager(window_title, logger=self.queue_log)

            while not self.stop_event.is_set():
                self.apply_current_thresholds(targets)

                if capture_mode == "printwindow" and manager is not None and not manager.is_valid_window():
                    self.queue_status("대상 창 검색 중")
                    manager.find_window()

                    if self.stop_event.is_set():
                        break

                    if not manager.is_valid_window():
                        self.queue_log(f"[대기] {WINDOW_RETRY_SECONDS}초 후 창을 다시 찾습니다.")
                        self.interruptible_sleep(WINDOW_RETRY_SECONDS)
                        continue

                self.queue_status("실행 중")
                if capture_mode == "region":
                    screen_gray = self.capture_screen_region(region)
                elif manager is not None:
                    screen_gray = manager.capture_client_area()
                else:
                    self.queue_log("[오류] 캡처 관리자가 준비되지 않았습니다.")
                    screen_gray = None

                if screen_gray is None:
                    self.queue_status("오류 발생")
                    self.queue_log(f"[대기] 캡처 실패로 {LOOP_SLEEP_SECONDS}초 후 다시 시도합니다.")
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)
                    continue

                found_any = False

                # 요구사항대로 target_A -> target_B -> target_C 순서로 탐지합니다.
                for target in targets:
                    if self.stop_event.is_set():
                        break

                    center, score = find_template_center(screen_gray, target, self.queue_log)
                    if center is None:
                        continue

                    found_any = True
                    base_x, base_y = center
                    x, y = self.apply_click_jitter(target, base_x, base_y)
                    self.queue_status("이미지 감지 성공")
                    self.queue_log(
                        f"[감지] {target.name} "
                        f"(점수: {score:.3f}, 위치: {base_x}, {base_y})"
                    )
                    if (x, y) != (base_x, base_y):
                        self.queue_log(
                            f"[클릭 좌표] {target.name} "
                            f"(기준: {base_x}, {base_y}, 보정: {x}, {y})"
                        )

                    if self.dispatch_click(manager, click_mode, capture_mode, region, x, y):
                        self.queue_status("클릭 완료")
                        self.queue_log(f"[대기] {target.wait_after_click}초 동안 대기합니다.")
                        self.interruptible_sleep(target.wait_after_click)

                    # 한 루프에서 하나만 클릭합니다.
                    break

                if self.stop_event.is_set():
                    break

                if not found_any:
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)

        except Exception as exc:
            self.queue_status("오류 발생")
            self.queue_log(f"[치명적 오류] 예상하지 못한 문제가 발생했습니다: {exc}")

        finally:
            self.stop_event.set()
            self.queue_status("종료됨")
            self.queue_log("[종료] 자동화 루프가 종료되었습니다.")
            self.ui_queue.put(("finished", ""))

    def apply_click_jitter(
        self,
        target: TargetImage,
        center_x: int,
        center_y: int,
    ) -> tuple[int, int]:
        """매칭 영역 안에서 중심 좌표 주변의 작은 클릭 보정값을 적용합니다."""

        if target.image_gray is None:
            return center_x, center_y

        target_height, target_width = target.image_gray.shape[:2]

        # 중심에서 너무 멀어져 템플릿 영역 밖으로 나가지 않도록 작은 이미지에서는 범위를 줄입니다.
        max_x_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_width - 1) // 2))
        max_y_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_height - 1) // 2))

        jitter_x = random.randint(-max_x_jitter, max_x_jitter)
        jitter_y = random.randint(-max_y_jitter, max_y_jitter)

        return center_x + jitter_x, center_y + jitter_y

    def capture_screen_region(self, region: tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """
        화면의 지정 영역만 캡처해서 GrayScale NumPy 배열로 반환합니다.

        이 모드는 대상 창이 실제 화면에 보이는 상태일 때 사용하는 현실적 타협 모드입니다.
        PrintWindow와 달리 전체 화면 캡처에서 영역을 잘라오기 때문에 렌더링 호환성이 높지만,
        창이 다른 창에 가려지면 가려진 화면 그대로 캡처됩니다.
        """

        if pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 화면 영역 캡처를 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {PYAUTOGUI_IMPORT_ERROR}")
            return None

        x, y, width, height = region
        try:
            screenshot = pyautogui.screenshot(region=(x, y, width, height))
            image_rgb = np.array(screenshot)
            if image_rgb.size == 0:
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 비어 있습니다.")
                return None

            if np.all(image_rgb == 0):
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 완전히 검은색입니다.")
                return None

            self.queue_log(f"[캡처 성공] 화면 영역 x={x}, y={y}, w={width}, h={height}")
            screen_gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
            cv2.imwrite(str(Path(__file__).resolve().parent / "debug_capture.png"), screen_gray)
            return screen_gray
        except Exception as exc:
            self.queue_log(f"[캡처 오류] 화면 영역 캡처 중 문제가 발생했습니다: {exc}")
            return None

    def dispatch_click(
        self,
        manager: Optional[InactiveManager],
        click_mode: str,
        capture_mode: str,
        region: tuple[int, int, int, int],
        x: int,
        y: int,
    ) -> bool:
        """선택된 클릭 모드에 따라 클릭을 전송합니다."""

        if click_mode == "postmessage":
            if manager is None:
                self.queue_log("[오류] PostMessage 클릭에는 대상 창 HWND가 필요합니다.")
                return False
            return manager.post_click(x, y)

        if capture_mode == "region":
            screen_x = region[0] + x
            screen_y = region[1] + y
            return self.click_mouse_and_return(screen_x, screen_y)

        if manager is None:
            self.queue_log("[오류] 마우스 클릭 좌표 변환에 대상 창 정보가 없습니다.")
            return False

        screen_point = manager.client_to_screen(x, y)
        if screen_point is None:
            return False

        screen_x, screen_y = screen_point
        return self.click_mouse_and_return(screen_x, screen_y)

    def click_mouse_and_return(self, screen_x: int, screen_y: int) -> bool:
        """
        실제 마우스를 대상 위치로 부드럽게 이동해 클릭한 뒤 원래 위치로 되돌립니다.

        허가된 UI 테스트 및 업무 자동화 환경에서 입력 장치 충돌을 줄이기 위한
        안정 클릭 루틴입니다. 이 방식은 사용 중인 마우스를 잠깐 점유합니다.
        """

        if pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 마우스 클릭을 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {PYAUTOGUI_IMPORT_ERROR}")
            return False

        try:
            original_x, original_y = pyautogui.position()
            self.queue_log(
                f"[클릭 전송] 화면 좌표=({screen_x}, {screen_y}), "
                f"복귀 좌표=({original_x}, {original_y})"
            )

            # 입력 렉과 포커스 흔들림을 줄이기 위해 순간이동 대신 짧은 고정 시간으로 이동합니다.
            pyautogui.moveTo(screen_x, screen_y, duration=0.15)
            time.sleep(0.05)

            pyautogui.click()
            time.sleep(0.05)

            # 사용자의 원래 작업 위치를 최대한 보존합니다.
            pyautogui.moveTo(original_x, original_y, duration=0.15)
            self.queue_log("[클릭 완료] 마우스 클릭 후 원래 위치로 복귀했습니다.")
            return True
        except pyautogui.FailSafeException:
            self.queue_log("[긴급 중단] PyAutoGUI FAILSAFE가 감지되었습니다.")
            raise
        except Exception as exc:
            self.queue_log(f"[오류] 마우스 클릭 중 문제가 발생했습니다: {exc}")
            return False

    def interruptible_sleep(self, seconds: float) -> bool:
        """
        긴 대기 중에도 종료 버튼/단축키가 반응할 수 있도록 0.1초 단위로 나눠 쉽니다.

        반환값:
        - True: 대기 중 stop_event가 설정됨
        - False: 지정된 시간만큼 정상 대기
        """

        end_time = time.monotonic() + seconds
        while time.monotonic() < end_time:
            if self.stop_event.is_set():
                return True
            remaining = end_time - time.monotonic()
            time.sleep(min(0.1, max(0.0, remaining)))
        return self.stop_event.is_set()

    def log(self, message: str) -> None:
        """UI 스레드에서 바로 로그를 남깁니다."""

        timestamp = time.strftime("%H:%M:%S")
        line = f"{timestamp} {message}\n"

        if hasattr(self, "preview_log_rows"):
            self.preview_log_messages.append(line.rstrip())
            self.preview_log_messages = self.preview_log_messages[-12:]
            empty_count = 12 - len(self.preview_log_messages)
            visible_lines = [""] * empty_count + self.preview_log_messages
            for row, text in zip(self.preview_log_rows, visible_lines):
                row.configure(text=text)
            return

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)

        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.log_text.delete("1.0", "100.0")

        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def queue_log(self, message: str) -> None:
        """작업 스레드에서 로그 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("log", message))

    def set_status(self, status: str) -> None:
        """UI 스레드에서 상태 라벨을 갱신합니다."""

        self.status_var.set(status)

    def queue_status(self, status: str) -> None:
        """작업 스레드에서 상태 변경 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("status", status))

    def _poll_ui_queue(self) -> None:
        """root.after로 UI 큐를 주기적으로 확인하고 위젯을 갱신합니다."""

        while True:
            try:
                kind, message = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self.log(message)
            elif kind == "status":
                self.set_status(message)
            elif kind == "finished":
                self._set_button_state(running=False)

        if not self.closing:
            self.root.after(100, self._poll_ui_queue)

    def _set_button_state(self, running: bool) -> None:
        """실행 상태에 따라 시작/종료 버튼 활성화를 조절합니다."""

        if self.ui_preview_only:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)
            return

        if running:
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.title_entry.configure(state=tk.DISABLED)
        else:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)


def load_targets(
    base_dir: Path,
    logger: Optional[LogCallback] = None,
) -> Optional[list[TargetImage]]:
    """target_A/B/C.png를 GrayScale 이미지로 미리 로드합니다."""

    log = logger or print
    targets = [
        TargetImage(name="target_A", filename="target_A.png", wait_after_click=10.0),
        TargetImage(name="target_B", filename="target_B.png", wait_after_click=5.0),
        TargetImage(name="target_C", filename="target_C.png", wait_after_click=3.0),
    ]

    for target in targets:
        image_path = base_dir / target.filename

        if not image_path.exists():
            log(f"[오류] 이미지 파일을 찾을 수 없습니다: {image_path}")
            log(f"       main.py와 같은 폴더에 {target.filename} 파일을 넣어주세요.")
            return None

        try:
            # cv2.imread는 한글/특수문자 경로에서 실패하는 경우가 있어,
            # np.fromfile + cv2.imdecode 방식으로 읽습니다.
            file_bytes = np.fromfile(str(image_path), dtype=np.uint8)
            image_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception as exc:
            log(f"[오류] 이미지 파일을 읽는 중 문제가 발생했습니다: {image_path}")
            log(f"       원본 오류: {exc}")
            return None

        if image_bgr is None:
            log(f"[오류] 이미지 로드에 실패했습니다: {image_path}")
            log("       파일이 손상되었거나 OpenCV가 읽을 수 없는 형식일 수 있습니다.")
            return None

        target.image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        log(
            f"[이미지 로드] {target.filename}, "
            f"크기={target.image_gray.shape[1]}x{target.image_gray.shape[0]}, "
            f"임계값={target.threshold:.2f}"
        )

    return targets


def find_template_center(
    screen_gray: np.ndarray,
    target: TargetImage,
    logger: Optional[LogCallback] = None,
) -> tuple[Optional[tuple[int, int]], float]:
    """
    cv2.matchTemplate로 target 이미지를 찾고 중심 좌표와 매칭 점수를 반환합니다.

    반환:
    - center: threshold 이상이면 (x, y), 아니면 None
    - score: 가장 높은 매칭 점수
    """

    log = logger or print

    if target.image_gray is None:
        log(f"[오류] {target.name} 이미지가 로드되지 않았습니다.")
        return None, 0.0

    target_height, target_width = target.image_gray.shape[:2]
    screen_height, screen_width = screen_gray.shape[:2]

    if target_width > screen_width or target_height > screen_height:
        log(
            f"[주의] {target.filename}이 클라이언트 영역보다 큽니다. "
            f"target={target_width}x{target_height}, screen={screen_width}x{screen_height}"
        )
        return None, 0.0

    cv2.imwrite(str(Path(__file__).resolve().parent / "debug_screen.png"), screen_gray)

    result = cv2.matchTemplate(
        screen_gray,
        target.image_gray,
        cv2.TM_CCOEFF_NORMED,
    )
    _min_value, max_value, _min_location, max_location = cv2.minMaxLoc(result)
    score = float(max_value)
    debug_message = f"[디버그] {target.name} 검사 중... 산출된 최고 유사도 점수: {score:.3f}"
    print(debug_message)
    if logger is not None:
        log(debug_message)

    top_left_x, top_left_y = max_location
    center_x = top_left_x + target_width // 2
    center_y = top_left_y + target_height // 2

    log(
        f"[매칭] {target.name} "
        f"(점수: {score:.3f}, 위치: {center_x}, {center_y}, 기준: {target.threshold:.2f})"
    )

    if score < target.threshold:
        return None, score

    return (center_x, center_y), score


def main() -> None:
    """프로그램 진입점입니다."""

    root = tk.Tk()
    AutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

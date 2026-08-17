"""캡처 프레임 좌표 → 누를 좌표까지의 왕복을 검증한다.

이 경로가 틀리면 증상이 조용하다: 그림을 못 찾거나(아무 일도 안 일어남),
찾아도 엉뚱한 자리를 누른다. 둘 다 사용자에게는 '완료되었습니다'로 보인다.
실측으로 확인한 바로는, 정규화 방향을 반대로 뒤집어도 기존 테스트는 전부
통과했다 — 테스트가 앱이 쓰는 함수 대신 자기가 쓴 수식을 검증했기 때문이다.

여기서는 **진짜 Watcher** 를 만들고 win32 계층과 캡처 엔진만 가짜로 바꾼다.
"""

from __future__ import annotations

import pytest

from mpauseapp import deps, press, screen

pytestmark = pytest.mark.skipif(
    deps.cv2 is None or deps.np is None, reason="cv2/numpy 없으면 인식 경로가 없다"
)

HWND = 4242


class FakeWin32:
    """Watcher 가 쓰는 만큼만 흉내 낸다."""

    def __init__(self, client_size, client_origin=(100, 50), window_origin=(92, 19)):
        self.client_size = client_size
        self.client_origin = client_origin      # ClientToScreen(0,0)
        self.window_origin = window_origin      # GetWindowRect 좌상단

    def IsWindow(self, hwnd):
        return True

    def IsWindowVisible(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return False

    def GetClientRect(self, hwnd):
        return (0, 0, self.client_size[0], self.client_size[1])

    def ClientToScreen(self, hwnd, point):
        return (self.client_origin[0] + point[0], self.client_origin[1] + point[1])

    def GetWindowRect(self, hwnd):
        left, top = self.window_origin
        # 창 전체는 클라이언트보다 테두리만큼 크다.
        return (left, top, left + self.client_size[0] + 16, top + self.client_size[1] + 39)


class FakeEngine:
    def __init__(self, frame_size):
        self._size = frame_size
        self.closed_event = type("E", (), {"is_set": staticmethod(lambda: False)})()

    def frame_size(self):
        return self._size

    def latest(self, timeout=0.0):
        return None

    def stop(self):
        pass


@pytest.fixture
def template():
    loaded = screen.load_template(0.8)
    assert loaded is not None
    return loaded


def paste(frame_size, patch, position):
    np = deps.np
    width, height = frame_size
    frame = np.full((height, width), 40, dtype=np.uint8)
    frame[::7, :] = 55
    frame[:, ::11] = 50
    x, y = position
    frame[y : y + patch.shape[0], x : x + patch.shape[1]] = patch
    return frame


def make_watcher(monkeypatch, client_size, frame_size):
    monkeypatch.setattr(deps, "win32gui", FakeWin32(client_size))
    monkeypatch.setattr(screen, "wgc_engine", lambda hwnd, logger: FakeEngine(frame_size))
    return screen.Watcher(HWND)


def test_roundtrip_at_reference_resolution(monkeypatch, template):
    """캡처 = 클라이언트 영역인 흔한 경우."""
    watcher = make_watcher(monkeypatch, (1928, 1048), (1928, 1048))
    height, width = template.gray.shape[:2]
    frame = paste((1928, 1048), template.gray, (900, 500))

    normalized = watcher._normalize(frame)
    assert watcher.capture_scale == 1.0
    center, _score = screen.locate(normalized, template)
    assert center is not None

    client = watcher.to_client(*center)
    assert client is not None
    assert abs(client[0] - (900 + width // 2)) <= 1
    assert abs(client[1] - (500 + height // 2)) <= 1


def test_roundtrip_when_the_game_runs_at_1440p(monkeypatch, template):
    """UI 가 1.374배 커진 화면 — 정규화 방향이 뒤집히면 여기서 걸린다."""
    cv2 = deps.cv2
    scale = 1440 / 1048.0
    watcher = make_watcher(monkeypatch, (2560, 1440), (2560, 1440))
    big = cv2.resize(
        template.gray,
        (
            int(round(template.gray.shape[1] * scale)),
            int(round(template.gray.shape[0] * scale)),
        ),
        interpolation=cv2.INTER_LINEAR,
    )
    frame = paste((2560, 1440), big, (1500, 900))

    normalized = watcher._normalize(frame)
    assert normalized.shape[0] == pytest.approx(1048, abs=2)
    assert watcher.capture_scale == pytest.approx(scale, abs=1e-6)

    center, _score = screen.locate(normalized, template)
    assert center is not None, "정규화 후에 못 찾았다"

    client = watcher.to_client(*center)
    assert client is not None
    assert abs(client[0] - (1500 + big.shape[1] / 2)) <= 4
    assert abs(client[1] - (900 + big.shape[0] / 2)) <= 4


def test_capture_including_the_window_border_is_corrected(monkeypatch, template):
    """캡처가 테두리까지 포함하면 원점 차이만큼 빼 줘야 한다."""
    client_size = (1900, 1000)
    frame_size = (client_size[0] + 16, client_size[1] + 39)
    watcher = make_watcher(monkeypatch, client_size, frame_size)
    watcher.capture_scale = 1.0

    # 캡처 원점(창 좌상단 92,19)과 클라이언트 원점(100,50)의 차이 = (-8, -31)
    client = watcher.to_client(500, 400)
    assert client == (500 + 92 - 100, 400 + 19 - 50)


def test_point_outside_the_client_area_is_rejected(monkeypatch, template):
    """밖으로 나간 좌표는 누르지 않는다(엉뚱한 곳을 누르는 것보다 안전)."""
    watcher = make_watcher(monkeypatch, (800, 600), (800, 600))
    assert watcher.to_client(5000, 5000) is None
    assert watcher.to_client(-5, 10) is None


# ─── 누르기 쪽 순수 함수 ───────────────────────────────────────────────────


def test_jitter_stays_inside_the_template(template):
    height, width = template.gray.shape[:2]
    for _ in range(200):
        x, y = press.jitter(100, 200, (height, width))
        assert abs(x - 100) <= max(0, (width - 1) // 2)
        assert abs(y - 200) <= max(0, (height - 1) // 2)
        assert abs(x - 100) <= press.CLICK_JITTER_PIXELS
        assert abs(y - 200) <= press.CLICK_JITTER_PIXELS


def test_bezier_starts_and_ends_where_asked():
    start, control, end = (10, 10), (200, 300), (400, 120)
    assert press.bezier_point(start, control, end, 0.0) == start
    assert press.bezier_point(start, control, end, 1.0) == end
    # 범위를 벗어난 t 는 잘라낸다(경계 밖 좌표가 나오면 클램프가 필요해진다).
    assert press.bezier_point(start, control, end, -1.0) == start
    assert press.bezier_point(start, control, end, 5.0) == end


class RecordingWin32(FakeWin32):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []

    def PostMessage(self, hwnd, message, wparam, lparam):
        self.messages.append((message, wparam, lparam & 0xFFFF, lparam >> 16))
        return True


def test_click_sequence_moves_then_presses_at_the_target(monkeypatch):
    win32 = RecordingWin32((800, 600))
    monkeypatch.setattr(deps, "win32gui", win32)
    monkeypatch.setattr(press, "CURVED_CLICK_MOVE_DURATION_SECONDS", 0.0)
    monkeypatch.setattr(press, "CLICK_MESSAGE_DELAY_SECONDS", 0.0)

    assert press.curved_click(HWND, (10, 10), (400, 300)) is True

    kinds = [m[0] for m in win32.messages]
    assert kinds[-2:] == [press._WM_LBUTTONDOWN, press._WM_LBUTTONUP]
    assert set(kinds[:-2]) == {press._WM_MOUSEMOVE}, "이동 없이 바로 누르면 안 된다"
    # 누르는 좌표는 목적지여야 한다.
    for message, _wparam, x, y in win32.messages[-2:]:
        assert (x, y) == (400, 300)
    # 모든 이동 좌표가 클라이언트 영역 안이어야 한다.
    for _message, _wparam, x, y in win32.messages:
        assert 0 <= x < 800 and 0 <= y < 600

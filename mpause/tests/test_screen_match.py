"""이식해 온 화면 인식이 실제로 그림을 찾아내는지 검증한다.

캡처(WGC)는 게임이 떠 있어야 하므로 테스트하지 않지만, **찾는 부분은**
합성 프레임으로 전부 검증할 수 있다. 여기서 지키는 것:

  * 임베드한 그림이 제대로 풀린다(바이너리에 박는 과정에서 깨지지 않았다).
  * 기준 해상도에서 정확한 좌표를 준다.
  * 다른 해상도(2560x1440)로 켠 화면도 정규화 후 찾아낸다 — 이 경로가 깨지면
    특정 해상도 사용자에게서만 조용히 아무 일도 안 일어난다.
"""

from __future__ import annotations

import pytest

from mpauseapp import deps, screen

pytestmark = pytest.mark.skipif(
    deps.cv2 is None or deps.np is None,
    reason="cv2/numpy 가 없으면 인식 기능 자체가 꺼진다",
)

THRESHOLD = 0.8


@pytest.fixture
def template():
    loaded = screen.load_template(THRESHOLD)
    assert loaded is not None, "임베드한 그림을 풀지 못했습니다"
    return loaded


def make_frame(width: int, height: int, patch=None, position=(0, 0)):
    """단색 배경 + (선택) 그림 한 장을 붙인 합성 프레임."""
    np = deps.np
    frame = np.full((height, width), 40, dtype=np.uint8)
    # 완전 단색이면 상관계수가 정의되지 않는 구역이 생긴다 → 옅은 무늬를 깐다.
    frame[::7, :] = 55
    frame[:, ::11] = 50
    if patch is not None:
        x, y = position
        frame[y : y + patch.shape[0], x : x + patch.shape[1]] = patch
    return frame


def test_embedded_template_unpacks(template):
    assert template.gray is not None
    height, width = template.gray.shape[:2]
    assert height > 4 and width > 4


def test_finds_at_reference_resolution(template):
    height, width = template.gray.shape[:2]
    frame = make_frame(1928, 1048, template.gray, (1200, 700))
    center, score = screen.locate(frame, template)
    assert center is not None, f"못 찾았다 (점수 {score:.3f})"
    assert abs(center[0] - (1200 + width // 2)) <= 1
    assert abs(center[1] - (700 + height // 2)) <= 1
    assert score >= THRESHOLD


def test_absent_template_is_not_reported(template):
    frame = make_frame(1928, 1048)
    center, _score = screen.locate(frame, template)
    assert center is None


def test_second_call_uses_cache_and_agrees(template):
    """캐시 경로(직전 위치 주변만 다시 보기)가 같은 답을 줘야 한다."""
    frame = make_frame(1928, 1048, template.gray, (300, 200))
    first, _ = screen.locate(frame, template)
    assert template._last_match is not None
    second, _ = screen.locate(frame, template)
    assert first == second


def test_finds_after_resolution_normalization(template):
    """2560x1440 으로 켠 화면: UI 가 1.374배 커진 상태를 정규화해서 찾는다."""
    cv2 = deps.cv2
    scale = 1440 / 1048.0
    big_patch = cv2.resize(
        template.gray,
        (
            max(1, int(round(template.gray.shape[1] * scale))),
            max(1, int(round(template.gray.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_LINEAR,
    )
    frame = make_frame(2560, 1440, big_patch, (1500, 900))

    assert abs(screen.capture_normalization_scale(1440) - scale) < 1e-6
    normalized = cv2.resize(
        frame,
        (int(round(2560 / scale)), int(round(1440 / scale))),
        interpolation=cv2.INTER_AREA,
    )
    center, score = screen.locate(normalized, template)
    assert center is not None, f"정규화 후에도 못 찾았다 (점수 {score:.3f})"
    # 정규화 프레임 좌표 → 원본 좌표로 되돌리면 붙인 자리와 맞아야 한다.
    assert abs(center[0] * scale - (1500 + big_patch.shape[1] / 2)) <= 4
    assert abs(center[1] * scale - (900 + big_patch.shape[0] / 2)) <= 4


def test_normalization_is_skipped_for_same_and_wide_resolutions():
    """세로가 같으면 보정하지 않는다(21:9 처럼 가로만 넓은 해상도 보호)."""
    assert screen.capture_normalization_scale(1048) == 1.0
    assert screen.capture_normalization_scale(1040) == 1.0   # 데드존
    assert screen.capture_normalization_scale(0) == 1.0
    assert screen.capture_normalization_scale(99999) == 1.0  # 지원 범위 밖

"""감독모드 홈 화면(로비) OCR — 티어·랭킹 점수·순위 파서.

실측 근거: 1920x1080 홈 화면(세미프로 3부)을 winocr 로 읽은 원문이
"세미프로3부감독0승/0무/1패9경기남음세미프로2부감독승격까지17점남음점1727" 이었다.
챔피언스/슈퍼 챔피언스 홈은 '랭킹 점수 4,604'·'순위 1'·'승률 48%' 가 추가로 뜬다(사용자 캡처).
"""

from __future__ import annotations

from macroapp import ocr


REAL_SEMIPRO = "세미프로3부감독0승/0무/1패9경기남음세미프로2부감독승격까지17점남음점1727"
SUPER_CHAMP = "슈퍼 챔피언스 감독 랭킹 점수 4,604 승률 48% 2287승/150무/2295패 순위 1 강등보호 25분 후 갱신됩니다."


def test_lower_tier_home_reads_only_the_first_tier_title():
    info = ocr.parse_home_text(REAL_SEMIPRO)
    assert info["has_home"] is True
    assert info["tier"] == "세미프로 3부 감독", info
    # '세미프로 2부 감독 승격까지' 는 두 번째 — 현재 티어가 아니다.
    assert info["score"] is None, "승격 포인트(17점)가 랭킹 점수로 읽혔다"
    assert info["rank"] is None


def test_champion_home_reads_tier_score_and_rank():
    info = ocr.parse_home_text(SUPER_CHAMP)
    assert info["has_home"] is True
    assert info["tier"] == "슈퍼 챔피언스 감독"
    assert info["score"] == 4604
    assert info["rank"] == 1


def test_champion_home_without_super_prefix():
    info = ocr.parse_home_text("챔피언스 감독 랭킹 점수 2,229 순위 1681 강등보호")
    assert (info["tier"], info["score"], info["rank"]) == ("챔피언스 감독", 2229, 1681)


def test_tabs_named_rank_do_not_produce_a_rank():
    """'친구 순위'·'클럽원 순위' 탭은 숫자가 안 붙어 순위로 안 읽힌다."""
    info = ocr.parse_home_text("슈퍼 챔피언스 감독 랭킹 점수 4,604 등급 변동 친구 순위 클럽원 순위")
    assert info["rank"] is None
    assert info["score"] == 4604


def test_non_home_screens_are_rejected():
    # 경기 결과 패널(등수/점수)은 홈 게이트 토큰이 없다.
    assert ocr.parse_home_text("챔피언스 감독 1681위 2229점")["has_home"] is False
    # 게이트 토큰은 있는데 티어(앵커)가 없다.
    assert ocr.parse_home_text("랭킹 점수 4,604 순위 1")["has_home"] is False
    # 아무것도 없음.
    assert ocr.parse_home_text("")["has_home"] is False
    assert ocr.parse_home_text(None)["has_home"] is False


def test_noise_tier_is_dropped_by_canon_snap():
    """'공식경기 감독모드' 같은 제목 노이즈는 표준 티어로 못 좁혀 홈으로 안 본다."""
    info = ocr.parse_home_text("공식경기 감독모드 홈 챔피언스 랭킹 보상 상세 정보 경기남음")
    assert info["has_home"] is False


def test_config_defaults_are_sane():
    from macroapp import config

    l, t, r, b = config.HOME_OCR_REGION
    assert 0 <= l < r <= 1 and 0 <= t < b <= 1
    assert config.HOME_OCR_INTERVAL_SECONDS >= 1.0
    assert config.HOME_OCR_VOTE_MIN >= 2

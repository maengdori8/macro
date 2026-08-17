"""스코어 숫자 글리프 템플릿 (자동 생성 — 손으로 고치지 말 것).

실물 캡처에서 뽑았다: '0' 두 장은 사용자 녹화(0:0, tv 레인지), '1'·'2' 는
사용자 스크린샷(1:2, full 레인지). 20x28 이진 마스크 PNG 의 base64 다.

왜 OCR 이 아니라 템플릿인가(실측): winocr 는 맥락 없는 숫자 한두 개를 프레임에
따라 못 읽는다 — '0 0' 은 읽고 '1 2' 는 완벽한 크롭에서도 빈 문자열이었다.
게임 폰트는 고정이므로 실물 글리프 IoU 매칭이 결정적이고 훨씬 빠르다(~1ms).
같은 숫자 IoU >= 0.79, 다른 숫자 <= 0.51 (실측) — 임계값 0.65.

숫자 3~9 템플릿은 아직 없다 → 그 스코어는 '박스는 보이는데 숫자 미상'으로
처리된다(자동 종료 판정엔 0/1/2 면 충분 — 0:2 까지 가는 길은 0:0→0:1 뿐이다).
새 숫자를 추가하려면 스크린샷을 받아 이 파일을 재생성한다.
"""

GLYPH_PNGS_B64 = {
    "0": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAf0lEQVQoFVXBgU0DQBADMGf/ocNfEGqx49SfeOKpjyCobyHUfxHqBHUiaoI6kZo49URq4tQTqYlTk5o4NamJU5OaODWpiVOTmjg1qYlTk5o4NamJU5OaODWpiVOTmjg1qYlTT6QmTj0RNVG/ItSJ+hWh/ougvoV46iOIU3/i+QHiEzARuj74mAAAAABJRU5ErkJggg==",
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAfElEQVQoFVXBgW3EMADEMGn/odVLauMRUiZ+BIT4EiG+RIgvMQ6JwzgkDuMlj3gZL3nEy3jIv3gYI1eMMXLFGCNXjDFyxRgjV4wxcsUYI1eMMXLFGCNXjDFyxRgjRzyMkX/xMl7yiJdxSBzGIXEI8SUy8SMgEz8C8ohL5g/MRjAJrqGrBgAAAABJRU5ErkJggg==",
    ],
    "1": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAVklEQVQoFWXBwQkAQRDDMKf/or2PcEdgpPCTTygZoWRErsgVuSJXKBmhZISSEUpGKBmhZISSEUpGKBmhZISSEUpGKBmhZISSEUpGKBmhZISSEUpGKBkPi/McAcuhmvgAAAAASUVORK5CYII=",
    ],
    "2": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAb0lEQVQoFWXBARLDIADDMPv/j84gsNIekgzhEBDClwjhS4TwJYaSKZShZAplKJlCCQFZQslbKHkLJW+h5AglcoQSOUKJ/IUSkC2UDLKERQaZwp8MMoVFSqZQssgQSjYhlDwMmzwMmzwMN8PNcDPcfvOwIBI42yQEAAAAAElFTkSuQmCC",
    ],
}

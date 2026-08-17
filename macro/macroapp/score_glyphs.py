"""스코어 숫자 글리프 템플릿 (자동 생성 — 손으로 고치지 말 것).

실물 캡처에서 뽑았다: '0' 두 장은 사용자 녹화(0:0, tv 레인지), '1'·'2' 는
사용자 스크린샷(1:2, full 레인지), '2' 두 번째와 '3' 은 2:3 스크린샷
(2026-08-17, 1920x1080 전체화면). 20x28 이진 마스크 PNG 의 base64 다.

왜 OCR 이 아니라 템플릿인가(실측): winocr 는 맥락 없는 숫자 한두 개를 프레임에
따라 못 읽는다 — '0 0' 은 읽고 '1 2' 는 완벽한 크롭에서도 빈 문자열이었다.
게임 폰트는 고정이므로 실물 글리프 IoU 매칭이 결정적이고 훨씬 빠르다(~1ms).

측정된 분리도(임계값 0.65):
  * 같은 숫자   : 0.79~0.85 이상  (예: '2' 두 샘플끼리 0.854)
  * 다른 숫자   : 0.48~0.61       (최악이 '3' vs '0' = 0.606)
  ⚠️ '3' 이 들어오면서 교차 IoU 최악값이 0.51 → 0.606 으로 올라 여유가 얇아졌다.
  숫자를 더 추가할 때는 반드시 교차 IoU 를 다시 재고, 0.65 에 근접하면 임계값이
  아니라 **마스크 해상도/여백**을 손봐야 한다(임계값을 낮추면 오인식이 는다).

숫자 4~9 템플릿은 아직 없다 → 그 스코어는 '박스는 보이는데 숫자 미상'으로
처리된다(진행 중 증거로만 쓰이고 판정은 하지 않는다 — 안전 방향).
새 숫자를 추가하려면 그 숫자가 보이는 스크린샷을 받아 이 파일을 재생성한다.
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
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAcElEQVQoFV3BgRGDMBADMHn/od3wAZpDiku9gljqFLHUKUJ9hVriUiPqEpcaqRFbLaGW2GqJU4041YhTjTjViENt8Vcj4lEjiEeNIB41gthqiyVGjRhxqS1GLHWLEeoWt1AjXqFGvEJ9hfoK9RXq6wfOPyAP9kNAGAAAAABJRU5ErkJggg==",
    ],
    "3": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAcklEQVQoFV3BgYHDIBADMHn/of1w9BtSKf5VfMRSjyCoWwj1FqHeInUEdaRGbDVSI7YasTSOGnGrEbcacaslYqtLxFaPEFs9Qmx1iXjUiLjUEbcacasRShw1UkscNVJbjBqpEVuN1K8I9Rah3iKW+orlD3wjJBArl95bAAAAAElFTkSuQmCC",
    ],
}

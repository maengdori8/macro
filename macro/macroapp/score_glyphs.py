"""스코어 숫자 글리프 템플릿 (자동 생성 — 손으로 고치지 말 것).

실물 캡처에서 뽑았다: '0' 두 장은 사용자 녹화(0:0, tv 레인지), '1'·'2' 는
사용자 스크린샷(1:2, full 레인지), '2' 두 번째와 '3' 은 2:3 스크린샷
(2026-08-17, 1920x1080 전체화면), '4' 두 장은 2026-08-11 실전 프레임 저장본
(dist/fc_state_0157=4:0, fc_state_0211=4:1, 1936x1056 창 모드). 20x28 이진 마스크
PNG 의 base64 다.

왜 OCR 이 아니라 템플릿인가(실측): winocr 는 맥락 없는 숫자 한두 개를 프레임에
따라 못 읽는다 — '0 0' 은 읽고 '1 2' 는 완벽한 크롭에서도 빈 문자열이었다.
2026-08-21 재검증: 저장된 실전 프레임 8장의 스코어보드 띠(팀명·시계 포함)를 통째로
넣어도 팀명과 시계("84:10")는 읽지만 **박스 안 숫자는 한 자리도 안 나온다.**
게임 폰트는 고정이므로 실물 글리프 IoU 매칭이 결정적이고 훨씬 빠르다(~1ms).

측정된 분리도(임계값 0.65):
  * 같은 숫자   : 0.79~0.87 이상  (예: '4' 두 샘플끼리 1.000)
  * 다른 숫자   : 최악 '0' vs '3' = 0.606
  ⚠️ 숫자를 더 추가할 때는 반드시 교차 IoU 를 다시 재고, 0.65 에 근접하면 임계값이
  아니라 **마스크 해상도/여백**을 손봐야 한다(임계값을 낮추면 오인식이 는다).

숫자 5~9 템플릿은 아직 없다 → 그 스코어는 '박스는 보이는데 숫자 미상'으로
처리된다(진행 중 증거로만 쓰이고 판정은 하지 않는다 — 안전 방향).
실전 표본은 logs/score_unknown/ 에 자동으로 쌓인다(gui._save_score_unknown_crop) —
그 크롭으로 이 파일을 재생성한다.
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
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAZ0lEQVQoFW3BQW7EMAwAMc7/H61NGvlQwGQe45UjjD85wvhkxRBDVgwxZGUNWVlDVtaQldd45MhjvHLkNV5Z+QyycoysHCMrhjCyYoghK8bKCuOTlcd45chFLnKRi1zkIhe5aGL89wOQMRkcYu5XrwAAAABJRU5ErkJggg==",
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAcklEQVQoFV3BgYHDIBADMHn/of1w9BtSKf5VfMRSjyCoWwj1FqHeInUEdaRGbDVSI7YasTSOGnGrEbcacaslYqtLxFaPEFs9Qmx1iXjUiLjUEbcacasRShw1UkscNVJbjBqpEVuN1K8I9Rah3iKW+orlD3wjJBArl95bAAAAAElFTkSuQmCC",
    ],
    "4": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAd0lEQVQoFWXBgXHCQAAEMW3/RV8e44BnkPIxl8jHXCIfc4nc5ha5zS1ym1vkbf5F3uZf5GW+Ii/zFWEumSPCHGGOCHOEOSLmJcwRMUeOOaI5cpkjmiOXOaL51fxqfjW/8jGXyNe8RB7miDzMEXmYI/IwR+RhjvgD6g0jGX4BrccAAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAd0lEQVQoFWXBgXHCQAAEMW3/RV8e44BnkPIxl8jHXCIfc4nc5ha5zS1ym1vkbf5F3uZf5GW+Ii/zFWEumSPCHGGOCHOEOSLmJcwRMUeOOaI5cpkjmiOXOaL51fxqfjW/8jGXyNe8RB7miDzMEXmYI/IwR+RhjvgD6g0jGX4BrccAAAAASUVORK5CYII=",
    ],
    "5": [
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAa0lEQVQoFVXBgW0EMQwDMGr/oVWfHyhiMtQVoa4IdUWoK0KNeIQa8Qg14hFqxCPUiEeoEY9QryDUFaGuCHVFqBGfWvGqFa9acdWIq0Yc9QklfmqlVnxqpVZ8aqVWfGqFuiLUFaGuiFH/YvwB4XkhE0S8Ee0AAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAABQAAAAcCAAAAABEscC8AAAAaElEQVQoFV3BgY0CMQAEMU//Re9DQK9wdsyvNE9pntI8pTlyaY5cmiOX5silecutecut+RWaX6F5iOYpzRHmK5f5yG2O3ObIbY7c5oiRjzmal3zM0RxhvpojzFfzlJhbiLmFvMy/vPwBC2khE/EJ4rIAAAAASUVORK5CYII=",
    ],
}

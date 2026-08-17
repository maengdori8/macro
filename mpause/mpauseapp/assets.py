"""바이너리에 박아 넣는 자산.

설치 폴더에 느슨한 이미지 파일을 두지 않기 위해 base64 문자열로 임베드한다.
파일이 폴더에 보이면 그것만으로 앱이 무엇을 찾는지 알려 주게 된다.

출처: mAuto 의 target_H.png (감독모드 자동화가 쓰는 것과 **같은 픽셀**).
원본이 바뀌면 아래 한 줄로 다시 만든다:

    python -c "import base64,pathlib;print(base64.b64encode(pathlib.Path('../macro/target_H.png').read_bytes()).decode())"
"""

from __future__ import annotations

import base64

_TARGET_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAB8AAAAWCAYAAAA4oUfxAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZ"
    "cwAADsMAAA7DAcdvqGQAAAPrSURBVEhL7ZRNbFRVFMd/9703896M05Z22ukHtoLF+BVsLcVWCSFNBCKJidS4IK5kUQLR"
    "uHNh1AhppDERYWGjkBjZuGcjSQVFolhqDC2lLaWCC21Lp6XToXXmzfu6Ll479I1tUhKiG3/J25x7/vf/7j3nHqGnOiT/"
    "EUph4N/kf/MAYjaHSJrgeMEFT0LOBasgvhYkkHMRWRdcv81WNDc+HiJ66DLKRDYQV4fTRA/0EvmgPxBfCyJtYXRdI3Kw"
    "F+2nJAAagJi3MY5czSdqF6b85M5BZEkIAGd7Almmo12axtsQy+eCfyPKlEno69/RrswiNYGzowp7by2yJAwCsDzUwTnU"
    "oTnsl2shf3JXotycz3/ew1Hcp9chZsx8TNzJBQ2XcCXa91NE911EP3kDZfQu6tUURtcg0Y5e1OvpQkUeBUAWhch+uhXr"
    "jU14NVEQIGwPuS6Ms6uGbFcT9t66Qi0A4q6NfmoMZSKL9fqjZD5vIdvdivN8BerALOEvfyuU5PFPrgqU21mMo4OEzk+i"
    "/JlBpC20vhn0EyOEeiaQRf71A4iMg3ZuEnUghUhZqAOzyLiO+f4zuFviOK3lmB81gSLQvru9zC5IvuGU4TnETA5nRyUL"
    "P+xm/sJusseawZWofTOIZR0ukibRQ5cJnxpDZBwApK6Au+wVSInUVb+zV5mheXNvUzGyXEftTxF5+xeib/UR/mwUVIHb"
    "WIYM33sYssIge3wr1v56vISBV1+EcttEP3EdZTyDcnMe/eg1RMbBbSn3G24F8js62xNkultwnouj/TyNdm4SFIF5uAHr"
    "4OMBkXxIw96zHrcpjizXsfZtRBoq+hc3iLX1EHvpvF+qhEHuwGMB7XIUFt9v6JtxlPEMzq4avIQBgLOtApE00Y8NE3n3"
    "CmLBv+IAisB67RHMzkacndW4TWW4W+LYr9SS/aQZt6GsUJFH6KkOqZ8cQz8+AnKxOIsTCAEyruMlDGQigr1nPZF3fsXb"
    "EGOh58XARgAi6yLmLKQikKVhWFaqpT5Rh+bIdj6L/Wqdby6SJuqtBWRI+IKwijRUvEoDQvc2UEfS6N2jeAkD88OGfBz8"
    "Hy5+6gwyFiLz1Qu4m0sDy2LaJPpmn29+pBG7fdEc/CkVPn0LtX82IFoJWWFgvrc5GHQlxU+eQcY0Mqe3/cOcnIs6OIeY"
    "t/GeKMGrjgRnu7hjovzx16qfOpImdHYc7eLUctna0FXc5jhOWxVedQSWal6Ytxraj0mi+y+tXPPFkxNWsNrrkFW+wUp4"
    "G2PYO6sfrHlR69lgbBWctkrMw433Z66MZ9C+nUQWh7DbC2a9BK1vJhhbBa80jFdfdH/mD5q/AbTfsPaTlERtAAAAAElF"
    "TkSuQmCC"
)


def target_png() -> bytes:
    """찾을 대상의 PNG 바이트."""
    return base64.b64decode(_TARGET_B64)

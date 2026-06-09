"""빌드 전 실행: targets.json + target_*.png를 macroapp/_assets.py에 박습니다.

이렇게 하면 Nuitka가 이 모듈을 컴파일하여 자산이 exe 안에 들어가고,
설치 폴더엔 느슨한 로직/이미지 파일이 남지 않아 구매자가 내부를 열람·복사할 수 없습니다.
"""

import base64
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "macroapp", "_assets.py")


def main():
    assets = {}
    for path in sorted(glob.glob(os.path.join(HERE, "target_*.png"))):
        name = os.path.basename(path)
        with open(path, "rb") as f:
            assets[name] = base64.b64encode(f.read()).decode("ascii")

    targets_json = ""
    tj = os.path.join(HERE, "targets.json")
    if os.path.exists(tj):
        with open(tj, "r", encoding="utf-8") as f:
            targets_json = f.read()

    lines = ['"""자동 생성 파일 — gen_assets.py가 만듭니다. 직접 수정 금지."""', ""]
    lines.append("ASSETS = {")
    for name, b64 in assets.items():
        lines.append(f"    {name!r}: {b64!r},")
    lines.append("}")
    lines.append("")
    lines.append(f"TARGETS_JSON = {targets_json!r}")
    lines.append("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[gen_assets] {len(assets)}개 이미지 + targets.json({len(targets_json)}자) -> {OUT}")


if __name__ == "__main__":
    main()

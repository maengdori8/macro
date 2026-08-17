"""빌드 게이트: 설치된 cv2.pyd가 Media Foundation을 임포트하면 exit 1.

opencv-python(일반판)의 cv2.pyd는 MF.dll/MFPlat.dll/MFReadWrite.dll을 정적 임포트해서
Windows N/KN 에디션 등 미디어 기능 없는 PC에서 'import cv2'가 무성으로 죽는다. headless
변종은 이 의존성이 없다. 두 변종이 같은 cv2\\cv2.pyd 경로를 공유해 pip 상태가 꼬이면 MF
버전이 남을 수 있으므로, 빌드 직전 디스크의 cv2.pyd를 직접 PE 파싱해 확인한다.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import re
import struct
import sys


def imported_dlls(path: str) -> list[str]:
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        return []
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    numsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    ddoff = opt + (112 if magic == 0x20B else 96)
    imp_rva = struct.unpack_from("<I", data, ddoff + 8)[0]
    if imp_rva == 0:
        return []
    sects = []
    so = opt + optsz
    for i in range(numsec):
        b = so + i * 40
        va = struct.unpack_from("<I", data, b + 12)[0]
        vs = struct.unpack_from("<I", data, b + 8)[0]
        raw = struct.unpack_from("<I", data, b + 20)[0]
        rs = struct.unpack_from("<I", data, b + 16)[0]
        sects.append((va, vs, raw, rs))

    def r2o(rva: int):
        for va, vs, raw, rs in sects:
            if va <= rva < va + max(vs, rs):
                return raw + (rva - va)
        return None

    names = []
    off = r2o(imp_rva)
    if off is None:
        return []
    while True:
        nr = struct.unpack_from("<I", data, off + 12)[0]
        if nr == 0:
            break
        no = r2o(nr)
        if no is None:
            break
        end = data.index(b"\x00", no)
        names.append(data[no:end].decode("ascii", "ignore"))
        off += 20
    return names


def main() -> int:
    spec = importlib.util.find_spec("cv2")
    if not spec or not spec.origin:
        print("[check] cv2를 찾을 수 없습니다.")
        return 1
    base = os.path.dirname(spec.origin)
    pyds = [p for p in glob.glob(os.path.join(base, "**", "*.pyd"), recursive=True)
            if os.path.basename(p) == "cv2.pyd"]
    if not pyds:
        print("[check] cv2.pyd를 찾을 수 없습니다.")
        return 1
    bad = False
    for p in pyds:
        deps = imported_dlls(p)
        mf = [d for d in deps if re.match(r"(?i)^mf", d)]
        if mf:
            print(f"[check] FAIL: {p} 가 Media Foundation을 임포트합니다: {sorted(mf)}")
            bad = True
        else:
            print(f"[check] OK: {os.path.basename(p)} (Media Foundation 의존 없음)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

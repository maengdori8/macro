"""진입점. stdout/stderr 가드 후 Tk 루트를 만들고 라이센스 다이얼로그를 띄웁니다."""

from __future__ import annotations

import os
import sys
import tkinter as tk

from macroapp.paths import app_dir
from macroapp.config import migrate_custom_targets
from macroapp.gui import LicenseDialog


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    root = tk.Tk()
    base_dir = app_dir()
    # 예전 설치 폴더에 있던 커스텀 캡처를 업데이트에도 안 지워지는 AppData로 1회 이전.
    migrate_custom_targets(base_dir)
    LicenseDialog(root, base_dir)
    root.mainloop()


if __name__ == "__main__":
    main()

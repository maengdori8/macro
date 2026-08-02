"""개발 중 AutomationApp 레이아웃을 인증 없이 확인하는 수동 프리뷰."""

import tkinter as tk
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.gui import AutomationApp


def main() -> None:
    root = tk.Tk()
    app = AutomationApp(root, license_key=None, preview=True)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    page = os.environ.get("MAUTO_UI_PAGE", "automation").strip().lower()
    if page in app._pages:
        app._show_page(page)

    snapshot = os.environ.get("MAUTO_UI_SNAPSHOT", "").strip()
    if snapshot:
        def save_snapshot() -> None:
            from PIL import ImageGrab

            root.attributes("-topmost", True)
            root.lift()
            root.update_idletasks()
            root.update()
            x = root.winfo_rootx()
            y = root.winfo_rooty()
            width = root.winfo_width()
            height = root.winfo_height()
            ImageGrab.grab((x, y, x + width, y + height)).save(snapshot)
            app.on_close()

        root.after(1500, save_snapshot)
    root.mainloop()


if __name__ == "__main__":
    main()

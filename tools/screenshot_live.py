"""开发辅助：用真实磁盘数据渲染主窗口（不参与打包）。

用法：
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshot_live.py out.png [设备名] [--dark]

--dark 会用一套模拟的 KDE Breeze Dark 调色板覆盖应用，用来检查深色系统主题下
控件是否仍然可读（提示：样式表里没有写死的控件会落到系统调色板上）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402
from knowdiskdrive.history import HistoryStore  # noqa: E402


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def apply_dark_palette(app) -> None:
    """模拟 KDE Breeze Dark，用于验证深色主题。"""
    from PySide6.QtGui import QColor, QPalette

    dark = QPalette()
    dark.setColor(QPalette.Window, QColor("#1b1e23"))
    dark.setColor(QPalette.WindowText, QColor("#fcfcfc"))
    dark.setColor(QPalette.Base, QColor("#232629"))
    dark.setColor(QPalette.AlternateBase, QColor("#1b1e23"))
    dark.setColor(QPalette.Text, QColor("#fcfcfc"))
    dark.setColor(QPalette.Button, QColor("#31363b"))
    dark.setColor(QPalette.ButtonText, QColor("#fcfcfc"))
    dark.setColor(QPalette.Highlight, QColor("#e93f6b"))
    dark.setColor(QPalette.HighlightedText, QColor("#fcfcfc"))
    app.setPalette(dark)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dark"]
    want_dark = "--dark" in sys.argv[1:]
    out = Path(args[0] if args else "/tmp/kdd_live.png")
    want = args[1] if len(args) > 1 else "nvme0n1"

    app = create_application(["screenshot-live"])
    if want_dark:
        apply_dark_palette(app)
    store = HistoryStore(Path(tempfile.mkdtemp()) / "history.db")
    window = MainWindow(cfg=DEFAULT_CONFIG, store=store, tray=None)
    window.resize(1180, 760)
    window.show()
    pump(app, 0.5)

    window.refresh_devices()
    for _ in range(200):
        pump(app, 0.1)
        if window.device_list.count() and window._info is not None:
            break

    for row in range(window.device_list.count()):
        item = window.device_list.item(row)
        if item is not None and want in item.text():
            window.device_list.setCurrentRow(row)
            break

    for _ in range(200):
        pump(app, 0.1)
        if window._info is not None and window._attr_model.rowCount() > 0:
            break
    pump(app, 0.6)

    window.grab().save(str(out))
    info = window._info
    if info is not None:
        print(
            f"{info.device}  status={info.status}  temp={info.temperature_c}  "
            f"hours={info.power_on_hours}  属性行={window._attr_model.rowCount()}"
        )
    window.close()
    window.worker.stop()
    pump(app, 0.2)
    store.close()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

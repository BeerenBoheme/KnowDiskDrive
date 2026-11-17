"""复现「左侧发黑」的两个必要条件，并验证修复是否对症。

用法：
  python tools/repro_dark.py before  # 模拟修复前：深色应用调色板 + 样式表无 QListWidget 段
  python tools/repro_dark.py after   # 当前代码
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.gui import theme  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "after"  # before | palette | after
out = Path(sys.argv[2] if len(sys.argv) > 2 else f"/tmp/repro_{mode}.png")

app = create_application(["repro"])

if mode == "before":
    # 1) 样式表回到没有 QListWidget 选择器的年代
    stripped = re.sub(
        r"(QListWidget|QAbstractItemView)[^{]*\{[^}]*\}", "", theme.STYLESHEET
    )
    theme.STYLESHEET = stripped  # type: ignore[misc]
    # 2) 应用调色板是 Breeze 深色（真实桌面实测值）
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#26292e"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#fcfcfc"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#26292e"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#fcfcfc"))
    dark.setColor(QPalette.ColorRole.Button, QColor("#26292e"))
    dark.setColor(QPalette.ColorRole.ButtonText, QColor("#fcfcfc"))
    dark.setColor(QPalette.ColorRole.Highlight, QColor("#ff6376"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#1b1e20"))
    app.setPalette(dark)

elif mode == "palette":
    # 最坏情况：样式表对列表完全失效（用户那台机器上的实际表现），只靠调色板兜底
    stripped = re.sub(
        r"(QListWidget|QAbstractItemView)[^{]*\{[^}]*\}", "", theme.STYLESHEET
    )
    theme.STYLESHEET = stripped  # type: ignore[misc]
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#26292e"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#fcfcfc"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#26292e"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#fcfcfc"))
    dark.setColor(QPalette.ColorRole.Highlight, QColor("#ff6376"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#1b1e20"))
    app.setPalette(dark)
    theme.apply_light_palette(app)

window = MainWindow(cfg=DEFAULT_CONFIG)
window.resize(1180, 760)
window.show()


def shoot() -> None:
    app.processEvents()
    window.grab().save(str(out))
    lw = window.device_list
    print("mode:", mode, "->", out)
    print("  device_list base:", lw.palette().color(QPalette.ColorRole.Base).name())
    print("  viewport base:", lw.viewport().palette().color(QPalette.ColorRole.Base).name())
    app.quit()


QTimer.singleShot(1200, shoot)
raise SystemExit(app.exec())

"""在真实桌面（DISPLAY）上渲染主窗口并截图，检查本机主题下的实际外观。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.gui import workers  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kdd_live_real.png")

app = create_application(["live"])
print("platform:", app.platformName(), "style:", app.style().objectName())
pal = app.palette()
print("palette Window:", pal.color(pal.ColorRole.Window).name())
print("palette Base:", pal.color(pal.ColorRole.Base).name())
print("palette Text:", pal.color(pal.ColorRole.Text).name())

window = MainWindow(cfg=DEFAULT_CONFIG)
window.resize(1180, 760)
window.show()


def shoot():
    window.update()
    app.processEvents()
    img = window.grab()
    img.save(str(out))
    print("saved", out, img.width(), img.height())
    lw = window.device_list
    print("list count:", lw.count())
    print("list palette base:", lw.palette().color(lw.palette().ColorRole.Base).name())
    print("list palette window:", lw.palette().color(lw.palette().ColorRole.Window).name())
    print("list autoFillBackground:", lw.autoFillBackground())
    app.quit()


QTimer.singleShot(2500, shoot)
raise SystemExit(app.exec())

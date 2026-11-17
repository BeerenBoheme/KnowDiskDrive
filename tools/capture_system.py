"""在真实桌面上打开主窗口，用「系统截图」抓取窗口，复现用户看到的左侧发黑。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kdd_cap_system.png")
mode = sys.argv[2] if len(sys.argv) > 2 else "active"

app = create_application(["cap"])
print("platform:", app.platformName(), "style:", app.style().objectName(), flush=True)
window = MainWindow(cfg=DEFAULT_CONFIG)
window.resize(1180, 760)
window.move(80, 60)
window.show()


def capture():
    window.raise_()
    window.activateWindow()
    app.processEvents()
    geom = window.frameGeometry()
    print("frameGeometry:", geom.x(), geom.y(), geom.width(), geom.height(), flush=True)
    args = ["spectacle", "-b", "-n", "-o", str(out), "-" + mode]
    subprocess.run(args, check=False)
    print("exists:", out.exists(), out.stat().st_size if out.exists() else 0, flush=True)
    app.quit()


QTimer.singleShot(2000, capture)
raise SystemExit(app.exec())

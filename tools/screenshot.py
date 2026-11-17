"""开发辅助：把主窗口渲染成 PNG，便于在无显示环境下检查界面（不参与打包）。

用法：
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshot.py out.png [fixture]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.devices import Device  # noqa: E402
from knowdiskdrive.gui import workers  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402
from knowdiskdrive.history import HistoryStore  # noqa: E402
from knowdiskdrive.smart import SmartctlRun, parse_json_payload  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kdd_main.png")
    fixture = sys.argv[2] if len(sys.argv) > 2 else "ata_zhitai_healthy.json"

    payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    device = Device(
        name="sda",
        model="ZHITAI SC001 Active 512GB SSD",
        serial="ZTB1EXAMPLE0000001",
        tran="sata",
        size="476.9G",
        size_bytes=512110190592,
        rota=False,
    )
    info = parse_json_payload(payload, device.path, SmartctlRun(rc=0))

    workers.enumerate_devices = lambda: [device]  # type: ignore[assignment]
    workers.read_smart_auto = lambda *a, **k: info  # type: ignore[assignment]

    app = create_application(["screenshot"])
    tmp = Path(tempfile.mkdtemp()) / "history.db"
    store = HistoryStore(tmp)
    base = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
    for i, temp in enumerate([31, 32, 33, 34, 36, 38, 37, 35, 34, 33]):
        store.record_sample(
            device.serial, temp, "good", "active", ts=base + timedelta(minutes=30 * i)
        )

    window = MainWindow(cfg=DEFAULT_CONFIG, store=store, tray=None)
    window.resize(1180, 760)
    window.show()
    for _ in range(6):
        app.processEvents()
    QTimer.singleShot(0, app.quit)
    app.exec()

    if window._info is None and window._current is not None:
        window._apply_read(
            workers.WorkResult(
                kind=workers.KIND_READ, device=device, info=info, report=None
            )
        )
    for _ in range(6):
        app.processEvents()

    window.grab().save(str(out))
    window.worker.stop()
    store.close()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

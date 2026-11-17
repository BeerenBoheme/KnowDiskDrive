"""在「深色系统调色板」条件下渲染完整界面，验证浅色调色板加固。

与 repro_dark.py 的区别：这个脚本只注入深色系统调色板（模拟 KDE Breeze 深色），
不改样式表，用来确认「正常代码 + 深色主题」下的最终观感。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402

from knowdiskdrive.config import DEFAULT_CONFIG  # noqa: E402
from knowdiskdrive.gui import theme, workers  # noqa: E402
from knowdiskdrive.gui.app import create_application  # noqa: E402
from knowdiskdrive.gui.main_window import MainWindow  # noqa: E402
from knowdiskdrive.smart import SmartctlRun, parse_json_payload  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kdd_after_dark.png")
app = create_application(["dark"])

# 模拟系统的深色调色板（Breeze：Base #26292e / Text #fcfcfc / Highlight #ff6376）
dark = QPalette()
for role, value in (
    (QPalette.ColorRole.Window, "#26292e"),
    (QPalette.ColorRole.WindowText, "#fcfcfc"),
    (QPalette.ColorRole.Base, "#26292e"),
    (QPalette.ColorRole.Text, "#fcfcfc"),
    (QPalette.ColorRole.Button, "#26292e"),
    (QPalette.ColorRole.ButtonText, "#fcfcfc"),
    (QPalette.ColorRole.Highlight, "#ff6376"),
    (QPalette.ColorRole.HighlightedText, "#1b1e20"),
):
    dark.setColor(role, QColor(value))
app.setPalette(dark)
print("注入后 app Base:", app.palette().color(QPalette.ColorRole.Base).name())

# 修复点：把调色板拉回浅色
theme.apply_light_palette(app)
print("apply_light_palette 后 Base:", app.palette().color(QPalette.ColorRole.Base).name())

# 用假数据填界面，不读真实磁盘
payload = parse_json_payload(
    {
        "model_name": "ZHITAI SC001 Active 512GB SSD",
        "serial_number": "ZTB1EXAMPLE0000001",
        "firmware_version": "ZT015200",
        "smart_status": {"passed": True},
        "power_on_time": {"hours": 7735},
        "power_cycle_count": 6055,
        "temperature": {"current": 34},
        "ata_smart_attributes": {
            "table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "thresh": 10, "raw": {"value": 0, "string": "0"}},
                {"id": 9, "name": "Power_On_Hours", "value": 91, "worst": 91, "thresh": 0, "raw": {"value": 7735, "string": "7735"}},
                {"id": 12, "name": "Power_Cycle_Count", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 6055, "string": "6055"}},
                {"id": 194, "name": "Temperature_Celsius", "value": 66, "worst": 40, "thresh": 0, "raw": {"value": 34, "string": "34 (Min/Max 20/45)"}},
                {"id": 199, "name": "UDMA_CRC_Error_Count", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 0, "string": "0"}},
            ]
        },
    },
    "/dev/sda",
    SmartctlRun(rc=0),
)

from knowdiskdrive import devices as devices_mod  # noqa: E402

device = devices_mod.Device(
    name="sda",
    model="ZHITAI SC001 Active 512GB SSD",
    serial="ZTB1EXAMPLE0000001",
    tran="sata",
    size="476.9G",
    size_bytes=512110190592,
    rota=False,
)
workers.enumerate_devices = lambda: [device]

window = MainWindow(cfg=DEFAULT_CONFIG)
window.resize(1180, 760)
window.show()


def shoot() -> None:
    window._apply_devices([device])
    from knowdiskdrive.health import evaluate
    from knowdiskdrive.gui.workers import WorkResult, KIND_READ

    window._apply_read(
        WorkResult(kind=KIND_READ, device=device, info=payload, report=evaluate(payload, DEFAULT_CONFIG))
    )
    app.processEvents()
    window.grab().save(str(out))
    print("saved", out)
    print("device_list viewport Base:", window.device_list.viewport().palette().color(QPalette.ColorRole.Base).name())
    app.quit()


QTimer.singleShot(1500, shoot)
raise SystemExit(app.exec())

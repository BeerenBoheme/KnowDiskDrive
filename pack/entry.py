"""PyInstaller 打包入口（与 console_scripts 指向的同一个 main）。

三种启动方式（同一个二进制）：

* 默认：打开图形界面；
* `--cli <子命令> ...`：走命令行前端，供 AppImage / 单文件分发时使用
  （冻结环境里没有独立的 `knowdiskdrive` 脚本）；
* `KDD_SELFTEST=1`：不启动界面，只打印打包自检信息——资源能不能找到、
  smartctl 能不能定位、能不能枚举磁盘。打包后先跑这个再交付。
"""

import os
import sys


def _selftest() -> int:
    from knowdiskdrive import data as data_mod
    from knowdiskdrive import devices as devices_mod
    from knowdiskdrive import smart as smart_mod

    print(f"frozen={getattr(sys, 'frozen', False)} meipass={getattr(sys, '_MEIPASS', None)}")
    print(f"desktop={data_mod.desktop_path()}")
    print(f"icon={data_mod.icon_path()}")
    print(f"smartctl={smart_mod.find_smartctl()}")
    try:
        found = devices_mod.enumerate_devices()
    except devices_mod.DeviceEnumerationError as exc:
        print(f"devices=ERROR {exc}")
        return 1
    print(f"devices={len(found)}")
    for device in found:
        print(f"  {device.path} {device.model} {device.serial}")
    return 0


if os.environ.get("KDD_SELFTEST"):
    raise SystemExit(_selftest())

_args = sys.argv[1:]
if _args[:1] == ["--cli"]:
    # 冻结包里点一次界面库不值得，CLI 分支先于 gui 导入。
    from knowdiskdrive.cli import main as cli_main  # noqa: PLC0415

    raise SystemExit(cli_main(_args[1:]))

from knowdiskdrive.gui.app import main  # noqa: E402

raise SystemExit(main())

"""随包发布的桌面资源：.desktop 菜单项与矢量图标。

放在包内（而不是源码树的顶层 data/）是为了让 pip 安装后的机器上也拿得到：
wheel 只会打包 knowdiskdrive* 下的文件，源码树根目录的 data/ 会被漏掉。
代码统一通过 importlib.resources.files("knowdiskdrive.data") 访问，
PyInstaller 打包后同样有效（资源被解到 _MEIPASS 下的同名路径）。
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

DESKTOP_NAME = "knowdiskdrive.desktop"
ICON_NAME = "knowdiskdrive.svg"


def _frozen_path(name: str) -> Path | None:
    """PyInstaller 打包后的资源位置：`sys._MEIPASS/knowdiskdrive/data/<name>`。

    单文件模式必须走这条路——此时包模块在 PYZ 归档里，importlib.resources
    未必能解出真实路径，而 datas 里的文件是实打实解压到 _MEIPASS 的。
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    candidate = Path(base) / "knowdiskdrive" / "data" / name
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def resource_path(name: str) -> Path | None:
    """返回包内资源的真实路径；取不到（例如被打进 zip）时返回 None。"""
    frozen = _frozen_path(name)
    if frozen is not None:
        return frozen
    try:
        target = resources.files(__name__).joinpath(name)
        path = Path(str(target))
    except (AttributeError, ImportError, OSError, TypeError):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def desktop_path() -> Path | None:
    return resource_path(DESKTOP_NAME)


def icon_path() -> Path | None:
    return resource_path(ICON_NAME)

"""安装应用菜单项与图标（不依赖源码树）。

```sh
knowdiskdrive install-desktop      # 普通用户身份运行，装到 ~/.local/share
```

为什么单独有这个模块：install.sh 需要源码树（它还要做 setcap，必须 root），
而 pip 安装的机器上没有源码树。这个子命令把「装菜单项」这件事用 Python 重做一遍，
用户 pip 装完就能自己补上菜单入口，不必再去翻仓库。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import data as data_mod

APP_ID = "knowdiskdrive"


class DesktopInstallError(RuntimeError):
    """菜单项安装失败（资源缺失、权限不足等）。"""


@dataclass
class InstallResult:
    """一次菜单安装的结果，供 CLI 打印。"""

    desktop_file: Path
    icon_file: Path
    exec_command: str
    notes: list[str]


def _xdg_dir(env_name: str, fallback: str) -> Path:
    raw = os.environ.get(env_name) or fallback
    return Path(os.path.expanduser(raw))


def data_home() -> Path:
    """$XDG_DATA_HOME（默认 ~/.local/share）。"""
    return _xdg_dir("XDG_DATA_HOME", "~/.local/share")


def applications_dir() -> Path:
    return data_home() / "applications"


def icon_dir() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps"


def detect_exec_command() -> str:
    """菜单项里的启动命令：优先 PATH 上的 knowdiskdrive-gui，否则用当前解释器 -m。"""
    found = shutil.which("knowdiskdrive-gui")
    if found:
        return found
    return f"{sys.executable} -m knowdiskdrive.gui"


def render_desktop(exec_command: str) -> str:
    """把模板里的 Exec / TryExec 换成本机可用的启动命令。"""
    source = data_mod.desktop_path()
    if source is None:
        raise DesktopInstallError(
            "安装包内缺少 .desktop 模板（knowdiskdrive/data 未随包发布）。"
        )
    text = source.read_text(encoding="utf-8")
    # TryExec 只认可执行文件名/绝对路径，不接受带空格的命令行。
    try_exec = exec_command.split()[0] if exec_command.split() else exec_command
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("Exec="):
            lines.append(f"Exec={exec_command} %F")
        elif line.startswith("TryExec="):
            lines.append(f"TryExec={try_exec}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _refresh_caches(notes: list[str]) -> None:
    """尽量刷新桌面数据库与图标缓存；缺工具不算失败。"""
    apps = str(applications_dir())
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(
                ["update-desktop-database", apps],
                check=False,
                capture_output=True,
                timeout=20,
            )
            notes.append("已刷新 desktop 数据库。")
        except (OSError, subprocess.SubprocessError):
            notes.append("update-desktop-database 执行失败，菜单可能要重新登录才出现。")
    else:
        notes.append("未安装 desktop-file-utils，已跳过菜单缓存刷新（重新登录即可生效）。")

    hicolor = str(data_home() / "icons" / "hicolor")
    if shutil.which("gtk-update-icon-cache"):
        try:
            subprocess.run(
                ["gtk-update-icon-cache", "-q", "-t", "-f", hicolor],
                check=False,
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def install(exec_command: str | None = None) -> InstallResult:
    """安装菜单项与图标，返回实际写入的路径。

    以 root 身份运行会装到 root 的家目录（普通用户的菜单里看不到），直接报错。
    """
    if os.geteuid() == 0:
        raise DesktopInstallError(
            "请不要用 sudo 运行：菜单项要装进当前普通用户的 ~/.local/share。"
            "如果刚用 sudo 跑过 install.sh，以普通用户身份再执行一次本命令即可。"
        )
    icon_source = data_mod.icon_path()
    if icon_source is None:
        raise DesktopInstallError(
            "安装包内缺少图标文件（knowdiskdrive/data 未随包发布）。"
        )
    exec_command = exec_command or detect_exec_command()
    text = render_desktop(exec_command)

    apps = applications_dir()
    icons = icon_dir()
    notes: list[str] = []
    try:
        apps.mkdir(parents=True, exist_ok=True)
        icons.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DesktopInstallError(f"无法创建目录：{exc}") from exc

    desktop_file = apps / f"{APP_ID}.desktop"
    icon_file = icons / f"{APP_ID}.svg"
    try:
        desktop_file.write_text(text, encoding="utf-8")
        desktop_file.chmod(0o644)
        shutil.copyfile(icon_source, icon_file)
        icon_file.chmod(0o644)
    except OSError as exc:
        raise DesktopInstallError(f"写入失败：{exc}") from exc

    _refresh_caches(notes)
    if shutil.which("desktop-file-validate"):
        try:
            proc = subprocess.run(
                ["desktop-file-validate", str(desktop_file)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if proc.returncode == 0:
                notes.append("desktop 文件校验通过。")
            elif proc.stdout.strip() or proc.stderr.strip():
                notes.append(
                    "desktop 文件校验有告警："
                    + (proc.stdout.strip() or proc.stderr.strip()).splitlines()[0]
                )
        except (OSError, subprocess.SubprocessError):
            pass
    return InstallResult(
        desktop_file=desktop_file, icon_file=icon_file, exec_command=exec_command, notes=notes
    )

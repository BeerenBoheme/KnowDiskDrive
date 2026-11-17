"""一次性授权：给 smartctl 加文件能力（setcap），不需要源码树。

为什么需要这个模块：install.sh 是给源码树准备的。用户拿着 AppImage 或 pip
安装的版本，机器上根本没有那个文件，却会在界面上被要求「运行 sudo ./install.sh」
——这句话对他们是死路：AppImage 里翻不出 install.sh，pip 装的目录里也没有。
这个模块把 install.sh 第 2 节那件事用 Python 重做一遍，并负责把提权对话框弹出来。

提权后端按可用性依次尝试：

1. pkexec（PolicyKit）——桌面会话里会弹系统自己的认证窗口，与发行版视觉一致；
2. sudo -A + 一个能弹密码框的 askpass 程序（zenity / kdialog / yad / qarma）；
3. 终端里直接 sudo（仅当标准输入是 tty 时才有意义）。

不做的事：不写 /etc、不建 systemd 服务、不改用户组、不碰 udev 规则。只给
smartctl 这一个二进制加 security.capability 扩展属性；撤销就是
sudo setcap -r /usr/sbin/smartctl，或者重装 smartmontools 覆盖掉。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: 能力编号（linux/capability.h），用来解读 security.capability 的位图
CAP_DAC_READ_SEARCH = 2
CAP_SYS_RAWIO = 17
CAP_SYS_ADMIN = 21

#: 完整授权：SATA 走 cap_sys_rawio，NVMe 走 cap_sys_admin（NVMe 需要 ioctl 直通）
CAPSET_FULL = "cap_sys_rawio,cap_dac_read_search,cap_sys_admin+ep"
#: 只授 SATA 需要的能力；NVMe 盘会继续显示「未授予读取权限」
CAPSET_SATA_ONLY = "cap_sys_rawio,cap_dac_read_search+ep"

#: security.capability 的版本位（capabilities(7)）
_VFS_CAP_REVISION_MASK = 0xFF000000
#: 用户主动取消提权时的退出码（pkexec 用 126，sudo 用 130）
_CANCELLED_CODES = frozenset({126, 130})

_ELEVATION_TITLE = "KnowDiskDrive 需要管理员授权"


class PrivilegeError(RuntimeError):
    """提权前置条件不满足（找不到 setcap、smartctl 路径异常等）。"""


@dataclass
class ElevationResult:
    """一次提权执行的结果。"""

    ok: bool
    backend: str
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    #: 换一个后端重试是否有意义（后端本身不可用时为 True，用户取消时为 False）
    retryable: bool = False


# ---------------------------------------------------------------------------
# 能力位图
# ---------------------------------------------------------------------------
def required_caps(no_nvme: bool = False) -> frozenset[int]:
    """读取 SMART 需要的能力集合。"""
    caps = {CAP_SYS_RAWIO, CAP_DAC_READ_SEARCH}
    if not no_nvme:
        caps.add(CAP_SYS_ADMIN)
    return frozenset(caps)


def setcap_command(smartctl: str | Path, no_nvme: bool = False) -> list[str]:
    """要执行的 setcap 命令（不含提权前缀）。"""
    caps = CAPSET_SATA_ONLY if no_nvme else CAPSET_FULL
    return ["setcap", caps, str(smartctl)]


def manual_command(smartctl: str | Path, no_nvme: bool = False) -> str:
    """给用户手工复制的那一行（提权对话框不可用时的兜底）。"""
    return "sudo " + " ".join(setcap_command(smartctl, no_nvme=no_nvme))


def read_permitted_caps(path: str | Path) -> int | None:
    """读文件的 permitted 能力位图。

    返回 None 表示没有 security.capability 扩展属性（未配置过），
    而不是「读不到」——普通用户本来就能读这个属性。
    """
    try:
        blob = os.getxattr(str(path), "security.capability")
    except (OSError, AttributeError):
        return None
    if len(blob) < 12:
        return None
    magic = int.from_bytes(blob[0:4], "little")
    if magic & _VFS_CAP_REVISION_MASK not in {
        0x01000000,
        0x02000000,
        0x03000000,
    }:
        return None
    low = int.from_bytes(blob[4:8], "little")
    # rev2/rev3 在 data[1] 里放高 32 位；rev1 只有 12 字节，没有高位
    high = int.from_bytes(blob[12:16], "little") if len(blob) >= 20 else 0
    return low | (high << 32)


def is_configured(smartctl: str | Path, no_nvme: bool = False) -> bool:
    """smartctl 是否已经拿到了读取裸盘所需的能力。"""
    bits = read_permitted_caps(smartctl)
    if bits is None:
        return False
    return all(bits & (1 << cap) for cap in required_caps(no_nvme=no_nvme))


# ---------------------------------------------------------------------------
# 提权后端
# ---------------------------------------------------------------------------
def available_backends() -> list[str]:
    """这台机器上可能用得上的提权后端（按优先级）。"""
    found: list[str] = []
    if shutil.which("pkexec"):
        found.append("pkexec")
    if shutil.which("sudo"):
        found.append("sudo")
    return found


def _askpass_tool() -> tuple[str, str] | None:
    """找一个能弹密码框的程序，返回 (可执行文件名, 参数)。"""
    if shutil.which("zenity"):
        return "zenity", "--password --title"
    if shutil.which("kdialog"):
        return "kdialog", "--password"
    if shutil.which("yad"):
        return "yad", "--password --title"
    if shutil.which("qarma"):
        return "qarma", "--password --title"
    return None


def _build_askpass() -> tuple[Path, Path] | None:
    """写一个只做「弹框取密码并打印到 stdout」的脚本。

    返回 (脚本路径, 临时目录)；调用方用完要删临时目录。找不到图形化工具时返回 None。
    """
    found = _askpass_tool()
    if found is None:
        return None
    tool, args = found
    directory = Path(tempfile.mkdtemp(prefix="kdd-askpass-"))
    script = directory / "askpass.sh"
    script.write_text(
        f'#!/bin/sh\nexec {tool} {args} "{_ELEVATION_TITLE}"\n', encoding="utf-8"
    )
    script.chmod(0o700)
    return script, directory


def _describe(proc: subprocess.CompletedProcess, backend: str) -> str:
    detail = (proc.stderr or proc.stdout or "").strip()
    tail = detail.splitlines()[-3:] if detail else []
    joined = "；".join(line.strip() for line in tail if line.strip())
    if proc.returncode in _CANCELLED_CODES:
        return "已取消授权。"
    if proc.returncode == 0:
        return f"{backend} 授权成功。"
    if not joined:
        return f"{backend} 退出码 {proc.returncode}。"
    return joined


def _run_pkexec(command: list[str], timeout: int) -> ElevationResult:
    try:
        proc = subprocess.run(
            ["pkexec", *command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return ElevationResult(
            ok=False, backend="pkexec", command=command, message="找不到 pkexec。", retryable=True
        )
    except subprocess.TimeoutExpired:
        return ElevationResult(
            ok=False, backend="pkexec", command=command, message="等待授权超时。"
        )
    # 127 = 没有可用的认证代理；换下一个后端试试
    retryable = proc.returncode == 127
    message = "" if proc.returncode == 0 else _describe(proc, "pkexec")
    if proc.returncode == 127:
        message = "当前会话没有可用的 PolicyKit 认证代理。"
    return ElevationResult(
        ok=proc.returncode == 0,
        backend="pkexec",
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        message=message,
        retryable=retryable,
    )


def _run_sudo(command: list[str], timeout: int, *, askpass: bool) -> ElevationResult:
    built = ["sudo", *command]
    cleanup: Path | None = None
    env = dict(os.environ)
    if askpass:
        built = ["sudo", "-A", *command]
        made = _build_askpass()
        if made is None:
            return ElevationResult(
                ok=False,
                backend="sudo",
                command=built,
                message="找不到能弹密码框的程序（zenity / kdialog / yad / qarma）。",
                retryable=True,
            )
        script, cleanup = made
        env["SUDO_ASKPASS"] = str(script)
    try:
        proc = subprocess.run(
            built,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return ElevationResult(
            ok=False, backend="sudo", command=built, message="找不到 sudo。", retryable=True
        )
    except subprocess.TimeoutExpired:
        return ElevationResult(
            ok=False, backend="sudo", command=built, message="等待授权超时。"
        )
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
    message = "" if proc.returncode == 0 else _describe(proc, "sudo")
    return ElevationResult(
        ok=proc.returncode == 0,
        backend="sudo",
        command=built,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        message=message,
        retryable=False,
    )


def _has_gui_session() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _backend_order(prefer: str) -> list[str]:
    """决定尝试顺序。GUI 场景先 pkexec（弹系统窗口），终端场景先 sudo（直接用 tty）。"""
    gui = _has_gui_session() if prefer == "auto" else prefer == "gui"
    order = ["pkexec", "sudo-askpass", "sudo-tty"] if gui else ["sudo-tty", "sudo-askpass", "pkexec"]
    available: list[str] = []
    for name in order:
        if name == "pkexec" and not shutil.which("pkexec"):
            continue
        if name != "pkexec" and not shutil.which("sudo"):
            continue
        available.append(name)
    return available


def run_elevated(
    command: list[str],
    *,
    prefer: str = "auto",
    timeout: int = 180,
) -> ElevationResult:
    """带提权执行一条命令。

    prefer 取 auto / gui / terminal：auto 看有没有图形会话。
    用户主动取消授权时立即返回，不再换后端（否则会接连弹好几个密码框）。
    """
    if shutil.which("setcap") is None:
        # setcap 在 libcap2-bin 里；有些精简发行版默认不装
        return ElevationResult(
            ok=False,
            backend="none",
            command=command,
            message="系统里没有 setcap（libcap2-bin 未安装）。",
        )

    order = _backend_order(prefer)
    if not order:
        return ElevationResult(
            ok=False,
            backend="none",
            command=command,
            message="既找不到 pkexec 也找不到 sudo，无法弹提权对话框。",
        )

    last = ElevationResult(
        ok=False, backend="none", command=command, message="没有可用的提权后端。"
    )
    for name in order:
        if name == "pkexec":
            result = _run_pkexec(command, timeout)
        else:
            result = _run_sudo(command, timeout, askpass=(name == "sudo-askpass"))
        if result.ok:
            return result
        last = result
        if result.returncode in _CANCELLED_CODES:
            return result
        if not result.retryable:
            break
    return last


def apply_capabilities(
    smartctl: str | Path, *, no_nvme: bool = False, prefer: str = "auto"
) -> ElevationResult:
    """一站式：检查是否已配置，没配置就提权执行 setcap。"""
    smartctl = Path(smartctl)
    if not smartctl.exists():
        return ElevationResult(ok=False, backend="none", message=f"找不到 {smartctl}。")
    command = setcap_command(smartctl, no_nvme=no_nvme)
    if is_configured(smartctl, no_nvme=no_nvme):
        return ElevationResult(
            ok=True,
            backend="cached",
            command=command,
            message="smartctl 已经拥有读取权限，无需重复授权。",
        )
    result = run_elevated(command, prefer=prefer)
    if result.ok:
        result.message = f"已给 {smartctl} 授予读取权限。"
    elif not result.message:
        result.message = "授权未完成。"
    return result


def summary_lines() -> list[str]:
    """给 CLI 打印的一小段环境说明。"""
    lines = [f"Python：{sys.executable}"]
    backends = available_backends()
    lines.append("提权后端：" + ("、".join(backends) if backends else "无"))
    tool = _askpass_tool()
    lines.append("密码框工具：" + (tool[0] if tool else "无（GUI 下只能靠 pkexec）"))
    return lines

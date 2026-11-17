"""块设备枚举、过滤与白名单校验（提示词 3.1 / 6）。

数据来源：`lsblk -J -d -o NAME,MODEL,SERIAL,TRAN,SIZE,ROTA,TYPE`，再按规则过滤：

* 只要 `TYPE == "disk"`，排除 loop / zram / dm / md / sr；
* 只保留 `TRAN` 为 sata / nvme / usb 的设备；
* `TRAN` 为空时回退到 `/sys/block/<dev>/removable` 与 `/sys/block/<dev>/device/model`；
* 禁止硬编码 `/dev/sda`、`/dev/nvme0n1`。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

#: 枚举命令（list 形式，禁止 shell=True）
LSBLK_CMD: Sequence[str] = (
    "lsblk",
    "-J",
    "-d",
    "-o",
    "NAME,MODEL,SERIAL,TRAN,SIZE,ROTA,TYPE",
)

#: 一律排除的虚拟/光盘设备名前缀
EXCLUDED_NAME_PREFIXES: tuple[str, ...] = ("loop", "zram", "dm-", "md", "sr", "ram", "fd")

#: 允许的传输类型
ALLOWED_TRANS: frozenset[str] = frozenset({"sata", "nvme", "usb"})

#: lsblk 调用超时（秒）
LSBLK_TIMEOUT_SECONDS = 10.0

SYSFS_BLOCK = Path("/sys/block")


class DeviceEnumerationError(RuntimeError):
    """枚举块设备失败（lsblk 不存在、输出无法解析等）。"""


@dataclass(frozen=True)
class Device:
    """一块物理磁盘。"""

    name: str  # 例如 sda / nvme0n1
    model: str | None
    serial: str | None
    tran: str | None  # sata / nvme / usb
    size: str | None  # lsblk 给的人类可读容量，例如 "476.9G"
    size_bytes: int | None  # 由 /sys/block/<dev>/size 换算的精确字节数
    rota: bool  # True 表示旋转介质（HDD）
    removable: bool = False

    @property
    def path(self) -> str:
        """设备绝对路径，例如 `/dev/sda`。"""
        return f"/dev/{self.name}"

    @property
    def display_model(self) -> str:
        """型号展示字符串，缺失时用占位符（提示词 8：缺失显示 `—`）。"""
        return (self.model or "").strip() or "—"

    @property
    def display_serial(self) -> str:
        return (self.serial or "").strip() or "—"


def _read_sysfs(dev_name: str, relative: str) -> str | None:
    """安全读取 /sys/block/<dev>/<relative>，失败返回 None。"""
    try:
        raw = (SYSFS_BLOCK / dev_name / relative).read_text(errors="replace")
    except (OSError, ValueError):
        return None
    return raw.strip()


def _sysfs_size_bytes(dev_name: str) -> int | None:
    """从 /sys/block/<dev>/size（512 字节扇区数）换算精确字节数。"""
    raw = _read_sysfs(dev_name, "size")
    if not raw:
        return None
    try:
        return int(raw) * 512
    except ValueError:
        return None


def _device_exists(dev_name: str) -> bool:
    """`/dev/<name>` 是否存在（抽成函数便于单测替换）。"""
    return Path("/dev", dev_name).exists()


def _sysfs_removable(dev_name: str) -> bool:
    return _read_sysfs(dev_name, "removable") == "1"


def _sysfs_has_model(dev_name: str) -> bool:
    model = _read_sysfs(dev_name, "device/model")
    return bool(model)


def is_excluded_name(name: str) -> bool:
    """按名字前缀排除 loop / zram / dm / md / sr 等。"""
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in EXCLUDED_NAME_PREFIXES)


def resolve_tran(name: str, tran: str | None) -> str | None:
    """确定设备的传输类型；无法确定或不在白名单时返回 None。

    `TRAN` 为空时回退到 sysfs 判断（提示词 3.1）。
    """
    if tran:
        cleaned = tran.strip().lower()
        return cleaned if cleaned in ALLOWED_TRANS else None
    # TRAN 为空：先看是否可移动媒体（USB 桥接盘常见），再看是否有 device/model
    if _sysfs_removable(name):
        return "usb"
    if name.startswith("nvme"):
        return "nvme"
    if _sysfs_has_model(name):
        return "sata"
    return None


#: 接口显示名（提示词 5.1 的示例写作 `NVMe`，全大写 NVME 不好看）
TRAN_DISPLAY: dict[str, str] = {
    "nvme": "NVMe",
    "sata": "SATA",
    "usb": "USB",
    "ata": "ATA",
    "scsi": "SCSI",
    "sas": "SAS",
}


def tran_display(tran: str | None) -> str:
    """把 lsblk 的传输类型转成界面上好看的写法。"""
    if not tran:
        return ""
    cleaned = tran.strip().lower()
    return TRAN_DISPLAY.get(cleaned, cleaned.upper())


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_lsblk_json(payload: dict) -> list[Device]:
    """把 `lsblk -J -d` 的 JSON 转成过滤后的设备列表。"""
    rows = payload.get("blockdevices")
    if not isinstance(rows, list):
        raise DeviceEnumerationError("lsblk 输出缺少 blockdevices 字段")

    devices: list[Device] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _clean_optional(row.get("name"))
        if not name:
            continue
        dev_type = (_clean_optional(row.get("type")) or "").lower()
        # 只要 TYPE == "disk"；loop / zram / rom 等一律丢弃
        if dev_type != "disk":
            continue
        if is_excluded_name(name):
            continue
        tran = resolve_tran(name, _clean_optional(row.get("tran")))
        if tran is None:
            continue
        if not _device_exists(name):
            continue
        devices.append(
            Device(
                name=name,
                model=_clean_optional(row.get("model")),
                serial=_clean_optional(row.get("serial")),
                tran=tran,
                size=_clean_optional(row.get("size")),
                size_bytes=_sysfs_size_bytes(name),
                rota=_to_bool(row.get("rota"), default=False),
                removable=_sysfs_removable(name) or tran == "usb",
            )
        )
    devices.sort(key=lambda d: d.name)
    return devices


def enumerate_devices(timeout: float = LSBLK_TIMEOUT_SECONDS) -> list[Device]:
    """枚举本机物理磁盘（已过滤 loop / zram / dm / md / sr）。"""
    exe = shutil.which("lsblk")
    if exe is None:
        raise DeviceEnumerationError("未找到 lsblk 命令，无法枚举块设备（请安装 util-linux）")
    try:
        proc = subprocess.run(
            [exe, *LSBLK_CMD[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeviceEnumerationError(f"lsblk 执行超时（{timeout:g} 秒）") from exc
    except OSError as exc:
        raise DeviceEnumerationError(f"lsblk 执行失败：{exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DeviceEnumerationError(f"lsblk 返回错误码 {proc.returncode}：{detail or '无错误输出'}")

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise DeviceEnumerationError(f"lsblk 输出不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise DeviceEnumerationError("lsblk 输出 JSON 顶层不是对象")
    return parse_lsblk_json(payload)


def find_device(name_or_path: str, devices: Iterable[Device]) -> Device | None:
    """在已枚举结果中查找设备；接受 `sda` 或 `/dev/sda` 两种写法。"""
    wanted = name_or_path.strip()
    if wanted.startswith("/dev/"):
        wanted = wanted[len("/dev/"):]
    for device in devices:
        if device.name == wanted:
            return device
    return None


def validate_device_path(path: str, devices: Iterable[Device]) -> str:
    """白名单校验（提示词第 6 节）。

    只接受形如 `/dev/<name>` 且命中当前 `lsblk` 枚举结果的路径，
    任何外部输入都无法借此拼进命令行。
    """
    if not isinstance(path, str) or not path.startswith("/dev/"):
        raise ValueError(f"设备路径必须以 /dev/ 开头：{path!r}")
    name = path[len("/dev/"):]
    if not name or "/" in name or " " in name or name.startswith("-"):
        raise ValueError(f"非法设备名：{path!r}")
    for device in devices:
        if device.name == name:
            return device.path
    raise ValueError(f"设备不在当前枚举结果中，拒绝访问：{path!r}")


def resolve_device_arg(arg: str, devices: Sequence[Device]) -> Device:
    """把命令行参数解析成 Device，找不到时给出中文错误。"""
    device = find_device(arg, devices)
    if device is None:
        names = "、".join(d.path for d in devices) or "（无）"
        raise KeyError(f"未找到设备 {arg}；当前可用设备：{names}")
    return device


#: 容量换算基数：与 lsblk 一致使用 1024 进制，展示单位沿用 KB/MB/GB/TB
_SIZE_STEP = 1024.0
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(value: int | None) -> str:
    """字节数转人类可读容量，未知时返回 `—`。

    采用 1024 进制并沿用 KB/MB/GB 单位名（与 lsblk 及提示词示例
    “476.9 GB” 的显示口径一致）。
    """
    if value is None or value <= 0:
        return "—"
    size = float(value)
    for unit in _SIZE_UNITS:
        if size < _SIZE_STEP or unit == _SIZE_UNITS[-1]:
            text = f"{size:.1f} {unit}"
            return text.replace(".0 ", " ")
        size /= _SIZE_STEP
    return f"{value} B"


def sysfs_readable_by_current_user(dev_name: str) -> bool | None:
    """粗略判断当前用户是否可读 /dev/<dev>，用于提前提示 setcap 未配置。

    返回 None 表示无法判断。
    """
    try:
        return os.access(f"/dev/{dev_name}", os.R_OK)
    except OSError:
        return None

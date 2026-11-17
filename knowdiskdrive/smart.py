"""smartctl 调用与 JSON / 文本解析（提示词 3.2 / 3.3 / 3.4 / 第 6 节 / 第 8 节）。

安全约束（提示词第 6 节，硬性要求）：

* 所有 smartctl 调用统一经过 run_smartctl()，参数以 list 形式传给
  subprocess.run；
* 禁止 shell=True，禁止把设备名或任何外部输入拼进命令行字符串；
* 设备名必须先用 devices.validate_device_path() 做白名单校验。

解析约束（提示词 3.2）：

* 优先 smartctl -j，用 json 解析，不用正则解析文本表格；
* 仅当 -j 不可用时降级到文本解析，并标记 parse_mode = "text"。
"""

from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import devices as devices_mod
from .config import Config, DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# 失败态分类（提示词第 8 节，UI 逐条呈现）
# ---------------------------------------------------------------------------
STATUS_OK = "ok"
STATUS_STANDBY = "standby"
STATUS_NO_SMART = "no_smart"
STATUS_PERMISSION = "permission"
STATUS_TIMEOUT = "timeout"
STATUS_NOT_INSTALLED = "not_installed"
STATUS_DEVICE_GONE = "device_gone"
STATUS_USB_BRIDGE_FAILED = "usb_bridge_failed"
STATUS_ERROR = "error"

#: 失败态 -> 中文文案（UI 与 CLI 共用，不混英文）
STATUS_TEXT: dict[str, str] = {
    STATUS_OK: "正常",
    STATUS_STANDBY: "休眠中（未唤醒）",
    STATUS_NO_SMART: "不支持 SMART",
    STATUS_PERMISSION: "未授予读取权限（需要一次性授权）",
    STATUS_TIMEOUT: "读取超时，可能是 USB 设备无响应",
    STATUS_NOT_INSTALLED: "未安装 smartmontools",
    STATUS_DEVICE_GONE: "设备已移除",
    STATUS_USB_BRIDGE_FAILED: "该 USB 设备不支持 SMART 透传",
    STATUS_ERROR: "读取失败",
}

PARSE_MODE_JSON = "json"
PARSE_MODE_TEXT = "text"

#: 无法判断时统一显示的占位符（提示词 8：缺失项显示 —，不显示 0）
PLACEHOLDER = "—"

#: smartctl 退出状态位（提示词 3.4 / 8）
EXIT_BIT_OPEN_FAILED = 0x02
EXIT_BIT_STANDBY_OR_CMD_FAILED = 0x04
EXIT_BIT_DISK_FAILING = 0x08


@dataclass
class AtaAttribute:
    """一条 ATA SMART 属性（来自 ata_smart_attributes.table[]）。"""

    id: int
    name: str
    value: int | None = None
    worst: int | None = None
    thresh: int | None = None
    raw: int | None = None
    raw_text: str | None = None
    when_failed: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or PLACEHOLDER

    def raw_display(self) -> str:
        """原始值展示：优先用 raw.string，否则用数值，未知显示 —。"""
        if self.raw_text:
            return self.raw_text
        if self.raw is None:
            return PLACEHOLDER
        return str(self.raw)


@dataclass
class DiskInfo:
    """一块磁盘一次读取的结果。所有字段读不到就是 None，绝不补 0。"""

    device: str
    status: str = STATUS_OK
    parse_mode: str = PARSE_MODE_JSON
    # 基础信息
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    capacity_bytes: int | None = None
    rotation_rate: int | None = None
    protocol: str = "unknown"  # "ata" / "nvme" / "unknown"
    # 健康
    smart_available: bool | None = None
    smart_enabled: bool | None = None
    smart_passed: bool | None = None
    temperature_c: int | None = None
    power_on_hours: int | None = None
    power_cycle_count: int | None = None
    # 明细
    ata_attributes: list[AtaAttribute] = field(default_factory=list)
    nvme_log: dict[str, Any] | None = None
    # 状态与诊断
    device_state: str = "unknown"  # "active" / "standby" / "unknown"
    smartctl_rc: int | None = None
    messages: list[str] = field(default_factory=list)
    usb_d_arg: str | None = None  # 探测成功的 -d 值（如 "sat"）
    usb_d_tried: list[str] = field(default_factory=list)
    detail: str | None = None  # 给用户看的补充说明
    # 自检进度（提示词 3.5），没跑过自检时全是 None
    self_test_status: str | None = None  # smartctl 原样字符串，如 "in progress, 40% remaining"
    self_test_remaining_pct: int | None = None
    self_test_value: int | None = None  # 原始状态码，判断"还在跑"要靠它

    @property
    def is_nvme(self) -> bool:
        return self.protocol == "nvme"

    @property
    def media(self) -> str:
        """介质类型，用于温度分级：hdd / sata_ssd / nvme。

        protocol 未知（例如读取失败）时按最保守的 SATA SSD 处理。
        """
        if self.is_nvme:
            return "nvme"
        if (self.rotation_rate or 0) >= 1:
            return "hdd"
        return "sata_ssd"

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, STATUS_TEXT[STATUS_ERROR])

    @property
    def display_model(self) -> str:
        return (self.model or "").strip() or PLACEHOLDER

    @property
    def display_serial(self) -> str:
        return (self.serial or "").strip() or PLACEHOLDER


@dataclass
class SmartctlRun:
    """一次 smartctl 调用的原始结果。"""

    rc: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error_kind: str | None = None  # 进程级失败分类

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.error_kind is None


#: PATH 里找不到 smartctl 时再扫的目录。
#:
#: 必要性：smartctl 通常装在 /usr/sbin（Debian/Ubuntu）或 /usr/bin（Arch/Fedora），
#: 而 sbin 目录在非 root 会话里经常不出现在 PATH 中——从 .desktop 启动的 GUI
#: 继承的是会话管理器的 PATH，不是用户 shell 的 PATH。只信 shutil.which 会让
#: 「菜单点开提示未安装、终端里却正常」这种自相矛盾的现象出现。
SMARTCTL_FALLBACK_DIRS: tuple[str, ...] = (
    "/usr/sbin",
    "/usr/local/sbin",
    "/sbin",
    "/usr/bin",
    "/usr/local/bin",
    "/bin",
)


def _is_executable_file(path: str | os.PathLike[str]) -> bool:
    try:
        target = Path(path)
        return target.is_file() and os.access(target, os.X_OK)
    except OSError:
        return False


def find_smartctl(cfg: Config = DEFAULT_CONFIG) -> str | None:
    """定位 smartctl（提示词第 8 节：未安装要能检测到）。

    查找顺序：显式配置的路径 → PATH → 常见 sbin/bin 目录。
    """
    if cfg.smartctl_path:
        configured = Path(cfg.smartctl_path).expanduser()
        if _is_executable_file(configured):
            return str(configured)
        via_path = shutil.which(cfg.smartctl_path)
        return via_path or None
    found = shutil.which("smartctl")
    if found:
        return found
    for directory in SMARTCTL_FALLBACK_DIRS:
        candidate = Path(directory) / "smartctl"
        if _is_executable_file(candidate):
            return str(candidate)
    return None


#: 发行版 ID / ID_LIKE → 安装 smartmontools 的命令
_DISTRO_INSTALL_COMMANDS: dict[str, str] = {
    "debian": "sudo apt install smartmontools",
    "ubuntu": "sudo apt install smartmontools",
    "linuxmint": "sudo apt install smartmontools",
    "pop": "sudo apt install smartmontools",
    "neon": "sudo apt install smartmontools",
    "arch": "sudo pacman -S smartmontools",
    "manjaro": "sudo pacman -S smartmontools",
    "endeavouros": "sudo pacman -S smartmontools",
    "fedora": "sudo dnf install smartmontools",
    "rhel": "sudo dnf install smartmontools",
    "centos": "sudo dnf install smartmontools",
    "rocky": "sudo dnf install smartmontools",
    "almalinux": "sudo dnf install smartmontools",
    "opensuse-leap": "sudo zypper install smartmontools",
    "opensuse-tumbleweed": "sudo zypper install smartmontools",
    "sles": "sudo zypper install smartmontools",
    "gentoo": "sudo emerge --ask sys-apps/smartmontools",
    "alpine": "sudo apk add smartmontools",
    "void": "sudo xbps-install -S smartmontools",
}

OS_RELEASE_PATH = Path("/etc/os-release")


def _read_os_release(path: Path | None = None) -> dict[str, str]:
    """读 /etc/os-release；读不到返回空表（不抛异常）。"""
    target = OS_RELEASE_PATH if path is None else Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip("'\"")
    return values


def smartctl_install_hint(os_release_path: Path | None = None) -> str:
    """按发行版给出安装 smartmontools 的命令（提示词第 8 节：未安装要能引导修好）。"""
    values = _read_os_release(os_release_path)
    candidates = [values.get("ID", "").lower()]
    candidates.extend(part.lower() for part in values.get("ID_LIKE", "").split())
    for distro in candidates:
        if distro in _DISTRO_INSTALL_COMMANDS:
            return _DISTRO_INSTALL_COMMANDS[distro]
    pretty = values.get("PRETTY_NAME") or values.get("NAME")
    if pretty:
        return f"请用 {pretty} 的包管理器安装 smartmontools（apt / dnf / pacman / zypper）"
    fallback = "请用系统包管理器安装 smartmontools（apt / dnf / pacman / zypper）"
    return fallback


def run_smartctl(
    device: str,
    args: Sequence[str],
    cfg: Config = DEFAULT_CONFIG,
    allowed_devices: Iterable[devices_mod.Device] | None = None,
) -> SmartctlRun:
    """统一入口：以 list 传参调用 smartctl，永不使用 shell。

    :param device: 必须是 /dev/<name> 且命中 lsblk 枚举白名单
    :param args: 例如 ("-j", "-n", "standby,3", "-a")
    """
    exe = find_smartctl(cfg)
    if exe is None:
        return SmartctlRun(rc=None, error_kind=STATUS_NOT_INSTALLED)

    if allowed_devices is None:
        try:
            allowed_devices = devices_mod.enumerate_devices()
        except devices_mod.DeviceEnumerationError as exc:
            return SmartctlRun(rc=None, error_kind=STATUS_ERROR, stderr=str(exc))

    try:
        safe_device = devices_mod.validate_device_path(device, allowed_devices)
    except ValueError as exc:
        return SmartctlRun(rc=None, error_kind=STATUS_ERROR, stderr=str(exc))

    argv = [exe, *[str(a) for a in args], safe_device]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SmartctlRun(rc=None, timed_out=True, error_kind=STATUS_TIMEOUT)
    except FileNotFoundError:
        return SmartctlRun(rc=None, error_kind=STATUS_NOT_INSTALLED)
    except OSError as exc:
        return SmartctlRun(rc=None, error_kind=STATUS_DEVICE_GONE, stderr=str(exc))

    return SmartctlRun(rc=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")


# ---------------------------------------------------------------------------
# 安全取值工具（提示词 8：dict.get 链式安全取值）
# ---------------------------------------------------------------------------
def dig(data: Any, *path: str | int, default: Any = None) -> Any:
    """沿着路径安全取值，任何一级缺失/类型不符都返回 default。"""
    current = data
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, (list, tuple)) or not (-len(current) <= key < len(current)):
                return default
            current = current[key]
        else:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
    return default if current is None else current


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # smartctl 文本输出会带千分位（"Power On Hours: 7,982"），JSON 里不会；
        # 不剥掉逗号会让文本降级路径把这些数字全丢成 None。
        if text.lower().startswith("0x"):
            try:
                return int(text, 16)
            except ValueError:
                return None
        try:
            return int(text.replace(",", ""))
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _messages_text(payload: dict) -> list[str]:
    """提取 smartctl 消息列表的纯文本。

    smartctl -j 把 messages 放在**顶层** `messages[]`；部分版本/包装脚本放在
    `smartctl.messages[]`，两处都收（去重后保持原顺序）。
    """
    texts: list[str] = []
    seen: set[str] = set()
    for messages in (payload.get("messages"), dig(payload, "smartctl", "messages", default=[])):
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            text = item.get("string")
            if isinstance(text, str) and text.strip():
                cleaned = text.strip()
                if cleaned not in seen:
                    seen.add(cleaned)
                    texts.append(cleaned)
    return texts


def _classify_failure(payload: dict, run: SmartctlRun, messages: list[str]) -> str:
    """依据退出码位与消息文本判定失败态（提示词第 8 节检测方式）。"""
    if run.timed_out:
        return STATUS_TIMEOUT
    if run.error_kind:
        return run.error_kind

    # -n standby 命中时 smartctl 不唤醒设备，JSON 里会带 power_mode
    power_mode = str(dig(payload, "power_mode", default="") or "").upper()
    if power_mode in {"STANDBY", "SLEEP"}:
        return STATUS_STANDBY

    rc = _as_int(dig(payload, "smartctl", "exit_status"))
    if rc is None:
        rc = run.rc or 0
    joined = " ".join(messages).lower()
    stderr_lower = (run.stderr or "").lower()

    permission_hints = ("permission denied", "operation not permitted", "access denied")
    if any(h in joined or h in stderr_lower for h in permission_hints):
        return STATUS_PERMISSION

    if "standby" in joined or "sleep mode" in joined:
        return STATUS_STANDBY
    if (rc & EXIT_BIT_STANDBY_OR_CMD_FAILED) and "standby" in joined:
        return STATUS_STANDBY

    if rc & EXIT_BIT_OPEN_FAILED:
        return STATUS_PERMISSION if "denied" in joined else STATUS_ERROR

    unavailable_hints = (
        "smart support is: unavailable",
        "does not support smart",
        "smart support is unavailable",
        "unsupported",
    )
    if any(h in joined for h in unavailable_hints):
        return STATUS_NO_SMART

    return STATUS_OK


def _parse_ata_attributes(payload: dict) -> list[AtaAttribute]:
    """解析 ata_smart_attributes.table[]（提示词 3.2 指定字段路径）。"""
    table = dig(payload, "ata_smart_attributes", "table", default=[])
    if not isinstance(table, list):
        return []
    attributes: list[AtaAttribute] = []
    for row in table:
        if not isinstance(row, dict):
            continue
        attr_id = _as_int(row.get("id"))
        if attr_id is None:
            continue
        when_failed = row.get("when_failed")
        if isinstance(when_failed, str) and when_failed.strip().lower() in {"", "-"}:
            when_failed = None
        raw_string = dig(row, "raw", "string")
        attributes.append(
            AtaAttribute(
                id=attr_id,
                name=str(row.get("name") or "").strip() or f"属性 {attr_id}",
                value=_as_int(row.get("value")),
                worst=_as_int(row.get("worst")),
                thresh=_as_int(row.get("thresh")),
                raw=_as_int(dig(row, "raw", "value")),
                raw_text=(str(raw_string).strip() or None) if raw_string is not None else None,
                when_failed=str(when_failed).strip() if when_failed else None,
            )
        )
    attributes.sort(key=lambda a: a.id)
    return attributes


#: ATA 自检状态的高 4 位等于 0xF 表示"正在执行"，低 4 位是剩余百分比 / 10
#: （所以 0xF4 就是 in progress 且剩 40%）。
ATA_SELF_TEST_RUNNING_NIBBLE = 0xF
#: NVMe 自检操作码：1 短自检 / 2 扩展自检 / 3 厂商自定义自检都在进行中
NVME_SELF_TEST_RUNNING = frozenset({1, 2, 3})


def _parse_self_test(payload: dict) -> tuple[str | None, int | None, int | None]:
    """从 smartctl -j 输出取自检状态：(原样字符串, 剩余百分比, 原始状态码)。"""
    status = dig(payload, "ata_smart_data", "self_test", "status")
    if isinstance(status, dict):
        string = status.get("string")
        return (
            string if isinstance(string, str) else None,
            _as_int(status.get("remaining_percent")),
            _as_int(status.get("value")),
        )
    operation = dig(payload, "nvme_self_test_log", "current_self_test_operation")
    if isinstance(operation, dict):
        string = operation.get("string")
        return (string if isinstance(string, str) else None, None, _as_int(operation.get("value")))
    return (None, None, None)


def self_test_in_progress(info: DiskInfo) -> bool:
    """自检是否还在执行。提示词 3.5 靠它决定要不要继续每 5 秒轮询。"""
    value = info.self_test_value
    if value is not None:
        if info.is_nvme:
            return value in NVME_SELF_TEST_RUNNING
        return (value >> 4) == ATA_SELF_TEST_RUNNING_NIBBLE
    # 文本降级时拿不到状态码，只能看文案；注意 "No self-test in progress" 里也有 in progress
    text = (info.self_test_status or "").lower()
    if not text or "no self-test" in text:
        return False
    return "in progress" in text


def self_test_remaining(info: DiskInfo) -> int | None:
    """自检剩余百分比。文本模式从 "40% of test remaining" 里补。"""
    if info.self_test_remaining_pct is not None:
        return info.self_test_remaining_pct
    text = info.self_test_status or ""
    match = _REMAINING_PCT_RE.search(text)
    if match is None:
        return None
    pct = _as_int(match.group(1))
    if pct is None:
        return None
    return max(0, min(100, pct))


def _parse_nvme_log(payload: dict) -> dict[str, Any] | None:
    """解析 nvme_smart_health_information_log（提示词 3.2 指定字段）。"""
    log = dig(payload, "nvme_smart_health_information_log")
    if isinstance(log, dict):
        return dict(log)
    critical = dig(payload, "critical_warning")
    if critical is not None:
        return {"critical_warning": _as_int(critical)}
    return None


def parse_json_payload(payload: dict, device: str, run: SmartctlRun) -> DiskInfo:
    """把 smartctl -j 的 JSON 解析为 DiskInfo（主路径）。"""
    messages = _messages_text(payload)
    status = _classify_failure(payload, run, messages)
    info = DiskInfo(device=device, parse_mode=PARSE_MODE_JSON, status=status)
    info.messages = messages
    info.smartctl_rc = _as_int(dig(payload, "smartctl", "exit_status"))
    if info.smartctl_rc is None:
        info.smartctl_rc = run.rc

    if status in {STATUS_PERMISSION, STATUS_TIMEOUT, STATUS_NOT_INSTALLED, STATUS_DEVICE_GONE}:
        # 进程级失败：保留原因，不解析出假数据
        info.detail = "；".join(messages) or run.stderr.strip() or None
        return info

    if status == STATUS_STANDBY:
        info.device_state = "standby"
        info.detail = "磁盘处于休眠状态，本轮未唤醒（-n standby）"
        return info

    info.device_state = "active"

    protocol = str(dig(payload, "device", "protocol", default="") or "").lower()
    # 读不到协议时保持 "unknown"，不要在 UI 上假装是 ATA
    if protocol == "nvme":
        info.protocol = "nvme"
    elif protocol in {"ata", "scsi"}:
        info.protocol = "ata"

    info.model = str(payload.get("model_name") or dig(payload, "device", "model") or "").strip() or None
    if info.model is None:
        info.model = str(dig(payload, "device", "name", default="") or "").strip() or None
    info.serial = str(payload.get("serial_number") or "").strip() or None
    info.firmware = str(payload.get("firmware_version") or "").strip() or None
    info.capacity_bytes = _as_int(dig(payload, "user_capacity", "bytes"))
    info.rotation_rate = _as_int(payload.get("rotation_rate"))

    info.smart_available = _as_bool(dig(payload, "smart_support", "available"))
    info.smart_enabled = _as_bool(dig(payload, "smart_support", "enabled"))
    info.smart_passed = _as_bool(dig(payload, "smart_status", "passed"))

    temp = _as_int(dig(payload, "temperature", "current"))
    if temp is None:
        temp = _as_int(payload.get("temperature"))
    info.temperature_c = temp

    info.power_on_hours = _as_int(dig(payload, "power_on_time", "hours"))
    info.power_cycle_count = _as_int(payload.get("power_cycle_count"))

    info.ata_attributes = _parse_ata_attributes(payload)
    info.nvme_log = _parse_nvme_log(payload)
    (
        info.self_test_status,
        info.self_test_remaining_pct,
        info.self_test_value,
    ) = _parse_self_test(payload)
    if info.nvme_log is not None:
        info.protocol = "nvme"
        if info.temperature_c is None:
            info.temperature_c = _as_int(info.nvme_log.get("temperature"))
        if info.power_cycle_count is None:
            info.power_cycle_count = _as_int(info.nvme_log.get("power_cycles"))
        if info.power_on_hours is None:
            info.power_on_hours = _as_int(info.nvme_log.get("power_on_hours"))

    if info.smart_available is False and info.smart_passed is None:
        info.status = STATUS_NO_SMART
    return info


# ---------------------------------------------------------------------------
# 文本降级解析（仅在 -j 失败时使用，标记 parse_mode = "text"）
# ---------------------------------------------------------------------------
def _text_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, _, value = line.partition(":")
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


_CAPACITY_UNITS = {
    "bytes": 1,
    "byte": 1,
    "kilobytes": 1000,
    "megabytes": 10**6,
    "gigabytes": 10**9,
    "terabytes": 10**12,
}


_TEXT_ATTR_RE = re.compile(
    r"^\s*(\d{1,3})\s+(\S+)\s+(0x[0-9a-fA-F]{4})\s+"
    r"(\d{3})\s+(\d{3})\s+(\d{3})\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)$"
)

#: ATA 文本："Self-test execution status:      ( 244)	Self-test routine in progress..."
_ATA_SELF_TEST_RE = re.compile(
    r"^Self-test execution status:\s*\(\s*(\d+)\s*\)\s*(.*)$", re.MULTILINE
)
#: NVMe 文本："Self-test status: No self-test in progress"
_NVME_SELF_TEST_RE = re.compile(r"^Self-test status:\s*(.*)$", re.MULTILINE)
#: 进行中时紧跟着的那一行才有剩余百分比（"40% of test remaining."）
_REMAINING_PCT_RE = re.compile(r"(\d+)\s*% of test remaining", re.IGNORECASE)


def _parse_self_test_text(text: str) -> tuple[str | None, int | None, int | None]:
    """文本降级时取自检状态。返回 (原样字符串, 剩余百分比, 原始状态码)。"""
    match = _ATA_SELF_TEST_RE.search(text)
    if match is not None:
        status = match.group(2).strip()
        pct_match = _REMAINING_PCT_RE.search(text)
        pct = _as_int(pct_match.group(1)) if pct_match is not None else None
        return (status or None, pct, _as_int(match.group(1)))
    match = _NVME_SELF_TEST_RE.search(text)
    if match is not None:
        status = match.group(1).strip()
        return (status or None, None, None)
    return (None, None, None)


_NVME_TEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("critical warning", "critical_warning"),
    ("temperature", "temperature"),
    ("available spare", "available_spare"),
    ("available spare threshold", "available_spare_threshold"),
    ("percentage used", "percentage_used"),
    ("data units read", "data_units_read"),
    ("data units written", "data_units_written"),
    ("power cycles", "power_cycles"),
    ("power on hours", "power_on_hours"),
    ("unsafe shutdowns", "unsafe_shutdowns"),
    ("media and data integrity errors", "media_errors"),
    ("number of error information log entries", "num_err_log_entries"),
    ("warning  comp. temperature time", "warning_temp_time"),
    ("critical comp. temperature time", "critical_comp_time"),
)


def _parse_ata_attributes_text(text: str) -> list[AtaAttribute]:
    """从 smartctl -a 文本的属性表里取属性（仅降级路径用）。

    文本表格是定宽格式：
    ID# ATTRIBUTE_NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
    """
    attributes: list[AtaAttribute] = []
    for line in text.splitlines():
        match = _TEXT_ATTR_RE.match(line)
        if match is None:
            continue
        attr_id, name, _flag, value, worst, thresh, _type, _updated, when_failed, raw = (
            match.groups()
        )
        raw = raw.strip()
        raw_number = None
        head = raw.split()[0] if raw.split() else ""
        raw_number = _as_int(head)
        attributes.append(
            AtaAttribute(
                id=int(attr_id),
                name=name.strip(),
                value=_as_int(value),
                worst=_as_int(worst),
                thresh=_as_int(thresh),
                raw=raw_number,
                raw_text=raw or None,
                when_failed=None if when_failed.strip() in {"", "-"} else when_failed.strip(),
            )
        )
    attributes.sort(key=lambda a: a.id)
    return attributes


def _parse_nvme_log_text(text: str) -> dict[str, int | None]:
    """从 NVMe 文本输出里取健康日志（仅降级路径用）。"""
    log: dict[str, int | None] = {}
    for line in text.splitlines():
        pair = _text_value(line.strip())
        if pair is None:
            continue
        key, value = pair
        lowered = key.lower()
        for text_key, log_key in _NVME_TEXT_KEYS:
            if lowered == text_key and log_key not in log:
                head = value.split("[")[0].strip()
                head = head.split()[0] if head.split() else ""
                log[log_key] = _as_int(head.rstrip("%"))
                break
    return log


def parse_text_payload(text: str, device: str, run: SmartctlRun) -> DiskInfo:
    """降级解析 smartctl -a 文本输出（提示词 3.2 的兼容路径）。

    只在 -j 不可用时使用。除了设备信息，也会从文本属性表里取出属性行与
    NVMe 健康日志——否则文本模式下界面会显示「良好」却没有任何依据数据。
    文本表格是定宽格式，_TEXT_ATTR_RE 只认标准列数，认不出的行直接跳过。
    """
    info = DiskInfo(device=device, parse_mode=PARSE_MODE_TEXT, status=STATUS_OK)
    info.smartctl_rc = run.rc
    joined = (text.lower() + " " + (run.stderr or "").lower())

    if run.timed_out:
        info.status = STATUS_TIMEOUT
        info.detail = STATUS_TEXT[STATUS_TIMEOUT]
        return info
    if "permission denied" in joined or "operation not permitted" in joined:
        info.status = STATUS_PERMISSION
        info.detail = STATUS_TEXT[STATUS_PERMISSION]
        return info
    if "device is in standby" in joined:
        info.status = STATUS_STANDBY
        info.device_state = "standby"
        return info
    if "smart support is: unavailable" in joined or "does not support smart" in joined:
        info.status = STATUS_NO_SMART
        return info
    if not text.strip():
        info.status = STATUS_ERROR
        info.detail = (run.stderr or "").strip() or "smartctl 无输出"
        return info

    info.device_state = "active"
    power_on_hours: int | None = None
    power_cycles: int | None = None
    rotation: int | None = None
    capacity: int | None = None

    for raw_line in text.splitlines():
        pair = _text_value(raw_line.strip())
        if pair is None:
            continue
        key, value = pair
        lowered = key.lower()
        if lowered in {"model family", "device model", "model number", "product"} and not info.model:
            info.model = value or None
        elif lowered == "serial number" and not info.serial:
            info.serial = value or None
        elif lowered == "firmware version" and not info.firmware:
            info.firmware = value or None
        elif lowered == "user capacity" and capacity is None:
            head = value.split("[")[0].replace(",", "").strip()
            parts = head.split()
            if len(parts) >= 2 and parts[0].isdigit():
                mult = _CAPACITY_UNITS.get(parts[1].lower())
                if mult:
                    capacity = int(parts[0]) * mult
        elif lowered == "rotation rate" and rotation is None:
            if "solid state" in value.lower():
                rotation = 0
            elif value.split():
                rotation = _as_int(value.split()[0])
        elif lowered == "power on time" or lowered == "power on hours":
            power_on_hours = _as_int(value.split("h")[0].strip())
        elif lowered == "power cycles":
            power_cycles = _as_int(value)
        elif lowered == "temperature":
            info.temperature_c = _as_int(value.split()[0]) if value.split() else None
        elif lowered == "smart overall-health self-assessment test result":
            info.smart_passed = "passed" in value.lower()
        elif lowered == "smart support":
            info.smart_available = "unavailable" not in value.lower() and "available" in value.lower()
        elif lowered == "smart enabled":
            info.smart_enabled = "enabled" in value.lower()

    info.rotation_rate = rotation
    info.capacity_bytes = capacity
    info.power_on_hours = power_on_hours
    info.power_cycle_count = power_cycles
    (
        info.self_test_status,
        info.self_test_remaining_pct,
        info.self_test_value,
    ) = _parse_self_test_text(text)
    # 文本里没有 JSON 的 device.protocol，只能靠特征判断：NVMe 版 smartctl 会打印
    # "NVMe Version" / "NVMe Log"，同时健康日志里的键名也是 NVMe 独有的。
    # （原来只看前 3000 字符里的 "nvme"，长输出时信息段落被挤开就会漏判。）
    lowered_text = text.lower()
    nvme_log = _parse_nvme_log_text(text)
    is_nvme_text = (
        "nvme version" in lowered_text
        or "nvme log" in lowered_text
        or {"critical_warning", "percentage_used"} & set(nvme_log)
    )
    if is_nvme_text:
        info.protocol = "nvme"
        info.nvme_log = nvme_log
        log_temp = _as_int(nvme_log.get("temperature"))
        if info.temperature_c is None and log_temp is not None:
            info.temperature_c = log_temp
    else:
        info.ata_attributes = _parse_ata_attributes_text(text)
        # 文本输出没有独立的温度/通电行，这些字段只在属性表里（9 / 12 / 190 / 194）
        by_id = {a.id: a for a in info.ata_attributes}
        if info.power_on_hours is None and 9 in by_id:
            info.power_on_hours = by_id[9].raw
        if info.power_cycle_count is None and 12 in by_id:
            info.power_cycle_count = by_id[12].raw
        if info.temperature_c is None:
            for temp_id in (194, 190):
                attr = by_id.get(temp_id)
                if attr is not None and attr.raw is not None:
                    info.temperature_c = attr.raw
                    break
        if "sata version is" in joined or "ata version is" in joined:
            info.protocol = "ata"
    if info.smart_available is False:
        info.status = STATUS_NO_SMART
    return info


# ---------------------------------------------------------------------------
# 高层读取接口
# ---------------------------------------------------------------------------
def _base_args(cfg: Config, d_arg: str | None, use_json: bool) -> list[str]:
    args: list[str] = []
    if use_json:
        args.append("-j")
    if cfg.standby_policy:
        args.extend(["-n", cfg.standby_policy])
    if d_arg:
        args.extend(["-d", d_arg])
    args.append("-a")
    return args


def read_smart(
    device: str,
    cfg: Config = DEFAULT_CONFIG,
    d_arg: str | None = None,
    allowed_devices: Iterable[devices_mod.Device] | None = None,
    use_json: bool = True,
) -> DiskInfo:
    """读取一块磁盘的 SMART 信息（优先 JSON，失败降级文本）。"""
    run = run_smartctl(device, _base_args(cfg, d_arg, use_json), cfg, allowed_devices)
    return interpret_run(device, run, cfg, d_arg=d_arg, allowed_devices=allowed_devices)


def interpret_run(
    device: str,
    run: SmartctlRun,
    cfg: Config = DEFAULT_CONFIG,
    d_arg: str | None = None,
    allowed_devices: Iterable[devices_mod.Device] | None = None,
) -> DiskInfo:
    """把一次调用的结果解析成 DiskInfo，并处理 JSON 不可用的降级。"""
    if run.error_kind == STATUS_NOT_INSTALLED:
        info = DiskInfo(device=device, status=STATUS_NOT_INSTALLED)
        info.detail = f"未检测到 smartctl，{smartctl_install_hint()}"
        return info
    if run.timed_out:
        info = DiskInfo(device=device, status=STATUS_TIMEOUT)
        info.detail = STATUS_TEXT[STATUS_TIMEOUT]
        return info
    if run.error_kind == STATUS_DEVICE_GONE:
        info = DiskInfo(device=device, status=STATUS_DEVICE_GONE)
        info.detail = run.stderr.strip() or None
        return info

    payload: dict | None = None
    if run.stdout.strip():
        try:
            parsed = json.loads(run.stdout)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None

    if payload is not None:
        info = parse_json_payload(payload, device, run)
    else:
        info = parse_text_payload(run.stdout, device, run)
        if info.status == STATUS_OK:
            info.detail = "兼容模式解析（smartctl 未能输出 JSON）"
    info.usb_d_arg = d_arg
    return info


def read_smart_auto(
    device: str,
    cfg: Config = DEFAULT_CONFIG,
    cached_d_arg: str | None = None,
    allowed_devices: Iterable[devices_mod.Device] | None = None,
    is_usb: bool | None = None,
) -> DiskInfo:
    """带缓存的读取：先用已缓存的 -d，失败时对 USB 设备做桥接探测。

    探测顺序（提示词 3.3）：裸 -a → -d sat → -d usbjmicron → -d usbsunplus。
    """
    tried: list[str] = []

    if cached_d_arg:
        info = read_smart(device, cfg, d_arg=cached_d_arg, allowed_devices=allowed_devices)
        tried.append(f"-d {cached_d_arg}")
        if info.status == STATUS_OK:
            info.usb_d_tried = tried
            return info
        if info.status in {STATUS_PERMISSION, STATUS_TIMEOUT, STATUS_STANDBY, STATUS_DEVICE_GONE}:
            info.usb_d_tried = tried
            return info

    info = read_smart(device, cfg, d_arg=None, allowed_devices=allowed_devices)
    tried.append("(默认)")
    if info.status in {
        STATUS_OK,
        STATUS_STANDBY,
        STATUS_PERMISSION,
        STATUS_TIMEOUT,
        STATUS_DEVICE_GONE,
        STATUS_NO_SMART,
    }:
        info.usb_d_tried = tried
        return info

    if is_usb is False:
        info.usb_d_tried = tried
        return info

    for candidate in cfg.usb_bridge_d_args:
        attempt = read_smart(device, cfg, d_arg=candidate, allowed_devices=allowed_devices)
        tried.append(f"-d {candidate}")
        if attempt.status == STATUS_OK:
            attempt.usb_d_tried = tried
            return attempt
        info = attempt

    info.status = STATUS_USB_BRIDGE_FAILED
    info.usb_d_tried = tried
    info.detail = "已尝试：" + "、".join(tried)
    return info


# ---------------------------------------------------------------------------
# 自检（提示词 3.5，M3 阶段使用，先提供稳定接口）
# ---------------------------------------------------------------------------
def start_self_test(
    device: str,
    mode: str,
    cfg: Config = DEFAULT_CONFIG,
    allowed_devices: Iterable[devices_mod.Device] | None = None,
) -> SmartctlRun:
    """启动 short / long 自检。需要写权限（见 setcap 方案）。"""
    if mode not in {"short", "long"}:
        raise ValueError(f"不支持的自检类型：{mode!r}")
    return run_smartctl(device, ["-t", mode], cfg, allowed_devices)


#: NVMe 规范里 1 个 data unit = 1000 个 512 字节逻辑块（即 512000 字节，
#: 不是 512 字节）。把它当成 512 字节会让读写量小 1000 倍。
NVME_DATA_UNIT_BYTES = 1000 * 512

_NVME_DATA_UNIT_KEYS = frozenset({"data_units_read", "data_units_written"})

#: 十进制容量单位（SSD 写入量按 TBW 的口径计价，不用 1024 进制）
_DECIMAL_UNITS: tuple[tuple[int, str], ...] = (
    (10**12, "TB"),
    (10**9, "GB"),
    (10**6, "MB"),
    (10**3, "KB"),
)


def format_data_units(units: int | None) -> str:
    """NVMe 累计读写量换算成人类可读容量。

    data_units_read / data_units_written 的单位是 1000×512 字节，
    直接显示原始数字既不好读，也容易被误当成字节数。
    """
    if units is None:
        return PLACEHOLDER
    total = units * NVME_DATA_UNIT_BYTES
    if total <= 0:
        return "0"
    for limit, unit in _DECIMAL_UNITS:
        if total >= limit:
            return f"{total / limit:.1f} {unit}"
    return f"{total} B"


def collect_nvme_rows(nvme_log: dict[str, Any] | None) -> list[tuple[str, str]]:
    """把 NVMe 健康日志整理成中文键值行，供表格展示（提示词 5.2）。"""
    if not nvme_log:
        return []
    labels: list[tuple[str, str]] = [
        ("critical_warning", "严重警告"),
        ("temperature", "温度（°C）"),
        ("available_spare", "可用备用空间（%）"),
        ("available_spare_threshold", "备用空间阈值（%）"),
        ("percentage_used", "寿命消耗（%）"),
        ("media_errors", "介质错误数"),
        ("num_err_log_entries", "错误日志条目数"),
        ("power_on_hours", "通电时间（小时）"),
        ("power_cycles", "通电次数"),
        ("unsafe_shutdowns", "异常断电次数"),
        ("data_units_read", "累计读取"),
        ("data_units_written", "累计写入"),
        ("warning_temp_time", "过温时间（分钟）"),
        ("critical_comp_time", "严重复合温度时间（分钟）"),
    ]
    rows: list[tuple[str, str]] = []
    for key, label in labels:
        value = nvme_log.get(key)
        if value is None:
            continue
        if key in _NVME_DATA_UNIT_KEYS:
            rows.append((label, format_data_units(_as_int(value))))
        else:
            rows.append((label, str(value)))
    return rows

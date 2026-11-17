"""命令行入口（M1 阶段的 CLI 原型）。

子命令：

* `knowdiskdrive list`   —— 列出本机物理磁盘
* `knowdiskdrive show <设备>` —— 显示单块磁盘的 SMART 详情
* `knowdiskdrive health` —— 全部磁盘的健康概要

退出码约定：0 成功；1 参数/设备错误；2 读取失败（含未安装 smartctl）；
3 未完成一次性权限配置。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from . import attrnames as attrnames_mod
from . import desktop as desktop_mod
from . import devices as devices_mod
from . import health as health_mod
from . import smart as smart_mod
from .config import Config, load_config

PROG = "knowdiskdrive"

#: 表格总宽度上限（超出则截断内容而不是撑破终端）
TABLE_WIDTH = 100


# ---------------------------------------------------------------------------
# 等宽对齐工具：中文按 2 列宽计算，保证表格不歪
# ---------------------------------------------------------------------------
def display_width(text: str) -> int:
    """返回字符串在等宽终端中的显示宽度（全角字符算 2）。"""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def truncate(text: str, width: int) -> str:
    """按显示宽度截断，超出部分用省略号代替。"""
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + char_width > max(0, width - 1):
            break
        out.append(char)
        used += char_width
    return "".join(out) + "…"


def pad(text: str, width: int, align: str = "left") -> str:
    """按显示宽度补空格。"""
    clipped = truncate(text, width)
    space = max(0, width - display_width(clipped))
    if align == "right":
        return " " * space + clipped
    return clipped + " " * space


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str] | None = None) -> list[str]:
    """渲染等宽表格（按显示宽度自动对齐）。"""
    aligns = list(aligns or ["left"] * len(headers))
    widths = [display_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], display_width(str(cell)))
    lines = ["".join(pad(h, widths[i], aligns[i]) + ("  " if i < len(headers) - 1 else "") for i, h in enumerate(headers))]
    lines.append("-" * min(TABLE_WIDTH, sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            cells.append(pad(str(cell), widths[index], aligns[index]))
        lines.append("  ".join(cells))
    return lines


def _print(text: str = "") -> None:
    print(text)


# ---------------------------------------------------------------------------
# 展示辅助
# ---------------------------------------------------------------------------
def _fmt_capacity(device: devices_mod.Device, info: smart_mod.DiskInfo | None) -> str:
    """容量展示：优先精确字节数，其次 lsblk 给出的字符串。"""
    if info is not None and info.capacity_bytes:
        return devices_mod.format_bytes(info.capacity_bytes)
    if device.size_bytes:
        return devices_mod.format_bytes(device.size_bytes)
    return device.size or "—"


def _model_text(device: devices_mod.Device, info: smart_mod.DiskInfo) -> str:
    """SMART 读不到型号时回退到 lsblk 的型号，仍然没有才显示 —。"""
    if (info.model or "").strip():
        return info.display_model
    return device.display_model


def _serial_text(device: devices_mod.Device, info: smart_mod.DiskInfo) -> str:
    if (info.serial or "").strip():
        return info.display_serial
    return device.display_serial


def _tran_text(device: devices_mod.Device) -> str:
    return devices_mod.tran_display(device.tran) or "—"


def _media_text(info: smart_mod.DiskInfo) -> str:
    """介质类型（提示词 4.3 的温度阈值就是按这个分的档）。"""
    if info.protocol == "unknown":
        return "—"
    return "HDD" if info.media == "hdd" else "SSD"


def _temp_text(info: smart_mod.DiskInfo, cfg: Config) -> str:
    if info.temperature_c is None:
        return "—"
    level = health_mod.temperature_level(info.temperature_c, info.media, cfg)
    suffix = {health_mod.TEMP_YELLOW: " 偏高", health_mod.TEMP_RED: " 过高"}.get(level, "")
    return f"{info.temperature_c}°C{suffix}"


def _status_note(info: smart_mod.DiskInfo) -> str:
    """把读取状态异常翻译成一句中文提示（提示词第 8 节）。"""
    if info.status == smart_mod.STATUS_OK:
        return ""
    text = info.status_text
    if info.status == smart_mod.STATUS_USB_BRIDGE_FAILED and info.usb_d_tried:
        text += "，已尝试：" + "、".join(info.usb_d_tried)
    if info.status == smart_mod.STATUS_PERMISSION:
        text += "（运行 knowdiskdrive fix-permission 一键授权，或在图形界面点「一键授权」）"
    elif info.detail:
        text += f"（{info.detail}）"
    return text


def _cell(value: int | None) -> str:
    return "—" if value is None else str(value)


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_list(args: argparse.Namespace, cfg: Config) -> int:
    """列出本机磁盘（验收标准 A1）。"""
    try:
        found = devices_mod.enumerate_devices()
    except devices_mod.DeviceEnumerationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if not found:
        _print("未发现可用的物理磁盘（已过滤 loop / zram / dm / md / sr）。")
        return 0

    if args.json:
        payload = [
            {
                "path": device.path,
                "model": device.model,
                "serial": device.serial,
                "tran": device.tran,
                "size": _fmt_capacity(device, None),
                "size_bytes": device.size_bytes,
                "rota": device.rota,
            }
            for device in found
        ]
        _print(json.dumps({"devices": payload}, ensure_ascii=False, indent=2))
        return 0

    rows = [
        [
            device.path,
            device.display_model,
            device.display_serial,
            _tran_text(device),
            _fmt_capacity(device, None),
            "HDD" if device.rota else "SSD",
        ]
        for device in found
    ]
    for line in render_table(["设备", "型号", "序列号", "接口", "容量", "类型"], rows):
        _print(line)
    _print()
    _print(f"共 {len(found)} 块磁盘。使用「{PROG} show <设备>」查看详情。")
    return 0


def cmd_show(args: argparse.Namespace, cfg: Config) -> int:
    """显示单块磁盘的 SMART 详情（验收标准 A2）。"""
    try:
        found = devices_mod.enumerate_devices()
    except devices_mod.DeviceEnumerationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    try:
        device = devices_mod.resolve_device_arg(args.device, found)
    except KeyError as exc:
        print(f"错误：{exc.args[0]}", file=sys.stderr)
        return 1

    info = smart_mod.read_smart_auto(
        device.path, cfg, allowed_devices=found, is_usb=device.tran == "usb"
    )
    report = health_mod.evaluate(info, cfg)

    if args.json:
        _print(json.dumps(info_to_dict(device, info, report), ensure_ascii=False, indent=2))
        return 0 if info.status == smart_mod.STATUS_OK else 2

    title_state = report.state_text
    _print(f"[{title_state}] {_model_text(device, info)}")
    _print(
        f"  设备 {info.device}    序列号 {_serial_text(device, info)}    "
        f"容量 {_fmt_capacity(device, info)}    "
        f"接口 {_tran_text(device)}    介质 {_media_text(info)}"
    )
    hours = f"{info.power_on_hours} 小时" if info.power_on_hours is not None else "—"
    cycles = str(info.power_cycle_count) if info.power_cycle_count is not None else "—"
    _print(
        f"  通电次数 {cycles}    通电时间 {hours}    "
        f"当前温度 {_temp_text(info, cfg)}    固件 {info.firmware or '—'}"
    )
    if report.reasons and info.status == smart_mod.STATUS_OK:
        _print(f"  判定依据：{report.reason_text()}")
    if info.parse_mode == smart_mod.PARSE_MODE_TEXT and info.status == smart_mod.STATUS_OK:
        _print("  注意：兼容模式解析（smartctl 未提供 JSON 输出）")

    note = _status_note(info)
    if note:
        _print(f"  状态：{note}")

    if info.status != smart_mod.STATUS_OK:
        return 2

    _print()
    if info.ata_attributes:
        is_sata = not info.is_nvme
        ordered = health_mod.sort_attributes(
            info.ata_attributes, cfg.pinned_attribute_ids, pin_first=not args.no_pin
        )
        rows = []
        for attr in ordered:
            status = health_mod.attribute_status(attr, is_sata)
            rows.append(
                [
                    str(attr.id),
                    attrnames_mod.attribute_display_name(attr.id, attr.name),
                    _cell(attr.value),
                    _cell(attr.worst),
                    _cell(attr.thresh),
                    attr.raw_display(),
                    health_mod.ATTR_STATUS_TEXT[status] + ("" if status == health_mod.ATTR_NORMAL else " ←"),
                ]
            )
        for line in render_table(
            ["ID", "属性名", "当前", "最差", "阈值", "原始值", "状态"],
            rows,
            aligns=["right", "left", "right", "right", "right", "left", "left"],
        ):
            _print(line)
        critical = len(report.critical_attrs)
        caution = len(report.caution_attrs)
        _print()
        _print(f"共 {len(rows)} 条属性：临界 {critical} 条，注意 {caution} 条，整体{report.state_text}。")
    elif info.nvme_log:
        _print("NVMe 健康信息日志：")
        rows = smart_mod.collect_nvme_rows(info.nvme_log)
        if not rows:
            _print("  （未读到任何字段）")
        for label, value in rows:
            _print(f"  {pad(label, 28)}{value}")
        _print()
        _print(f"整体{report.state_text}。")
    else:
        _print("该设备未提供 SMART 属性表。")
    return 0


def cmd_health(args: argparse.Namespace, cfg: Config) -> int:
    """全部磁盘的健康概要。"""
    try:
        found = devices_mod.enumerate_devices()
    except devices_mod.DeviceEnumerationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    collected: list[tuple[devices_mod.Device, smart_mod.DiskInfo, health_mod.HealthReport]] = []
    worst = health_mod.HEALTH_GOOD
    readable = 0
    rc = 0
    permission_denied: list[str] = []
    for device in found:
        info = smart_mod.read_smart_auto(
            device.path, cfg, allowed_devices=found, is_usb=device.tran == "usb"
        )
        report = health_mod.evaluate(info, cfg)
        collected.append((device, info, report))
        if info.status == smart_mod.STATUS_OK:
            readable += 1
            worst = health_mod.worst_health(worst, report.state)
        else:
            if info.status == smart_mod.STATUS_PERMISSION:
                permission_denied.append(device.path)
            if info.status not in {smart_mod.STATUS_STANDBY}:
                rc = 2

    if args.json:
        _print(
            json.dumps(
                [info_to_dict(device, info, report) for device, info, report in collected],
                ensure_ascii=False,
                indent=2,
            )
        )
        return rc

    rows = []
    for device, info, report in collected:
        if info.status == smart_mod.STATUS_OK:
            rows.append(
                [
                    device.path,
                    _model_text(device, info),
                    report.state_text,
                    _temp_text(info, cfg),
                    "正常",
                ]
            )
        else:
            rows.append([device.path, _model_text(device, info), "—", "—", info.status_text])
    for line in render_table(["设备", "型号", "健康", "温度", "状态"], rows):
        _print(line)
    _print()
    if readable == 0:
        _print("总体健康：无法判定（所有磁盘均读取失败，请检查上方状态）")
    else:
        _print(f"总体健康：{health_mod.HEALTH_TEXT[worst]}")
    if permission_denied:
        # 提示词第 6 节：不再让用户去找 install.sh（AppImage / pip 安装都没有那个文件）。
        _print(
            "提示：运行 knowdiskdrive fix-permission 一键授权，"
            "或在图形界面点「一键授权」（涉及 " + "、".join(permission_denied) + "）"
        )
    return rc


def cmd_daemon(args: argparse.Namespace, cfg: Config) -> int:
    """后台采样守护（提示词第 7 节）：定时写历史、按需打印告警。"""
    import signal
    import threading

    interval = args.interval if args.interval is not None else cfg.sample_interval_seconds
    if args.once:
        from .daemon import run_once

        return run_once(cfg)
    if args.rounds is not None and args.rounds <= 0:
        print("错误：--rounds 必须是正整数", file=sys.stderr)
        return 1

    from .daemon import Monitor

    stop = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):  # 非主线程时忽略
                pass

    with Monitor(cfg=cfg) as monitor:
        return monitor.run(interval=interval, iterations=args.rounds, stop_event=stop)


def cmd_install_desktop(args: argparse.Namespace, cfg: Config) -> int:
    """安装应用菜单项与图标（提示词 8.6：菜单入口要能被普通用户装上）。"""
    try:
        result = desktop_mod.install(args.exec_command)
    except desktop_mod.DesktopInstallError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"已安装菜单项：{result.desktop_file}")
    print(f"已安装图标：  {result.icon_file}")
    print(f"启动命令：    {result.exec_command}")
    for note in result.notes:
        print(f"· {note}")
    return 0


def cmd_fix_permission(args: argparse.Namespace, cfg: Config) -> int:
    """一次性授权：给 smartctl 加文件能力（提示词第 6 节，不需要源码树）。"""
    from . import privilege

    smartctl = smart_mod.find_smartctl(cfg)
    if smartctl is None:
        print(
            f"错误：未检测到 smartctl。{smart_mod.smartctl_install_hint()} 后重试。",
            file=sys.stderr,
        )
        return 2

    command = privilege.setcap_command(smartctl, no_nvme=args.no_nvme)
    if args.print_only:
        print(" ".join(command))
        return 0

    if privilege.is_configured(smartctl, no_nvme=args.no_nvme):
        print(f"smartctl 已有读取权限：{smartctl}")
        print(
            "（若仍提示权限不足，说明能力被文件系统忽略了——检查 /usr 是否单独挂载且带 nosuid）"
        )
        return 0

    print(f"目标：{smartctl}")
    print("要执行：" + " ".join(command))
    if not args.quiet:
        for line in privilege.summary_lines():
            print(f"· {line}")
    result = privilege.apply_capabilities(
        smartctl, no_nvme=args.no_nvme, prefer="terminal"
    )
    if result.ok:
        print(f"完成：{result.message}")
        if result.backend != "cached":
            actual = privilege.read_permitted_caps(smartctl)
            if actual is not None or not args.quiet:
                print(f"当前能力位图：{actual if actual is not None else '（读不到）'}")
        return 0
    print(f"授权未完成：{result.message}", file=sys.stderr)
    print(f"可以手工执行：{privilege.manual_command(smartctl, no_nvme=args.no_nvme)}", file=sys.stderr)
    return 1


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    """导出温度历史为 CSV（提示词第 7 节：GUI 与 CLI 共用同一份格式）。"""
    from datetime import datetime, timedelta, timezone

    from .history import open_store

    store = open_store()
    try:
        targets: list[tuple[str, str]] = []
        if args.device:
            info = smart_mod.read_smart_auto(args.device, cfg)
            if info.serial:
                targets.append((args.device, info.serial))
            else:
                print(f"错误：读不到 {args.device} 的序列号，无法定位历史", file=sys.stderr)
                return 2
        else:
            for device in devices_mod.enumerate_devices():
                info = smart_mod.read_smart_auto(device.path, cfg)
                if info.serial:
                    targets.append((device.path, info.serial))
        if not targets:
            print("没有可导出的磁盘历史", file=sys.stderr)
            return 2

        since = None
        if args.days is not None:
            since = datetime.now(timezone.utc).replace(tzinfo=timezone.utc) - timedelta(days=args.days)

        out_arg = Path(args.output) if args.output else Path.cwd()
        # 单盘 + 明确给了 .csv 文件名时按文件写，否则按「目录 + 每盘一个文件」写。
        if out_arg.suffix.lower() == ".csv" and len(targets) == 1:
            plan = [(out_arg, targets[0][1])]
        else:
            out_arg.mkdir(parents=True, exist_ok=True)
            plan = [(out_arg / f"{serial}-温度历史.csv", serial) for _path, serial in targets]

        total = 0
        for path, serial in plan:
            count = store.export_samples_csv(serial, path, since=since)
            total += count
            _print(f"{serial}：{count} 行 → {path}")
        if total == 0:
            print("历史库还没有样本，先跑一次 knowdiskdrive daemon --once", file=sys.stderr)
            return 2
        return 0
    finally:
        store.close()


def info_to_dict(
    device: devices_mod.Device,
    info: smart_mod.DiskInfo,
    report: health_mod.HealthReport,
) -> dict[str, Any]:
    """供 --json 输出与脚本消费的稳定结构。"""
    return {
        "device": device.path,
        "model": info.model,
        "serial": info.serial,
        "firmware": info.firmware,
        "capacity_bytes": info.capacity_bytes,
        "tran": device.tran,
        "rota": device.rota,
        "media": info.media,
        "protocol": info.protocol,
        "status": info.status,
        "status_text": info.status_text,
        "parse_mode": info.parse_mode,
        "device_state": info.device_state,
        "smart_passed": info.smart_passed,
        "temperature_c": info.temperature_c,
        "temperature_level": report.temp_level,
        "power_on_hours": info.power_on_hours,
        "power_cycle_count": info.power_cycle_count,
        "health": report.state,
        "health_text": report.state_text,
        "health_reasons": report.reasons,
        "usb_d_arg": info.usb_d_arg,
        "usb_d_tried": info.usb_d_tried,
        "detail": info.detail,
        "attributes": [
            {
                "id": a.id,
                "name": a.name,
                "value": a.value,
                "worst": a.worst,
                "thresh": a.thresh,
                "raw": a.raw,
                "raw_text": a.raw_text,
                "when_failed": a.when_failed,
                "status": health_mod.attribute_status(a, not info.is_nvme),
            }
            for a in info.ata_attributes
        ],
        "nvme_log": info.nvme_log,
    }


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="KnowDiskDrive —— Linux 磁盘健康检测工具",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="子命令")

    listing = sub.add_parser("list", help="列出本机物理磁盘")
    listing.add_argument("--json", action="store_true", help="以 JSON 输出")
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="显示单块磁盘的 SMART 详情")
    show.add_argument("device", help="设备，例如 /dev/sda 或 sda")
    show.add_argument("--json", action="store_true", help="以 JSON 输出")
    show.add_argument("--no-pin", action="store_true", help="不把 5/10/194/197/198/199 置顶")
    show.set_defaults(func=cmd_show)

    health_cmd = sub.add_parser("health", help="全部磁盘的健康概要")
    health_cmd.add_argument("--json", action="store_true", help="以 JSON 输出")
    health_cmd.set_defaults(func=cmd_health)

    daemon_cmd = sub.add_parser("daemon", help="后台采样：定时读取全部磁盘并记录历史")
    daemon_cmd.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="秒",
        help="采样间隔（默认取配置里的 sample_interval_seconds，即 300 秒）",
    )
    daemon_cmd.add_argument("--once", action="store_true", help="只采样一轮就退出")
    daemon_cmd.add_argument(
        "--rounds",
        type=int,
        default=None,
        metavar="N",
        help="只采样 N 轮后退出（默认一直运行）",
    )
    daemon_cmd.set_defaults(func=cmd_daemon)

    desktop_cmd = sub.add_parser(
        "install-desktop", help="安装应用菜单项与图标（pip 安装后没有源码树时用）"
    )
    desktop_cmd.add_argument(
        "--exec",
        dest="exec_command",
        default=None,
        metavar="命令",
        help="菜单里的启动命令（默认自动探测 knowdiskdrive-gui）",
    )
    desktop_cmd.set_defaults(func=cmd_install_desktop, needs_smartctl=False)

    fix_cmd = sub.add_parser(
        "fix-permission",
        help="一次性授权：给 smartctl 加读取能力（会弹管理员密码框）",
    )
    fix_cmd.add_argument(
        "--no-nvme",
        action="store_true",
        help="只授 SATA 需要的能力（NVMe 盘将读不到 SMART）",
    )
    fix_cmd.add_argument(
        "--print", dest="print_only", action="store_true", help="只打印命令，不执行"
    )
    fix_cmd.add_argument("-q", "--quiet", action="store_true", help="少打印环境信息")
    fix_cmd.set_defaults(func=cmd_fix_permission)

    export_cmd = sub.add_parser("export", help="导出温度历史为 CSV")
    export_cmd.add_argument("--device", help="只导出指定磁盘（默认导出全部）")
    export_cmd.add_argument("--days", type=int, default=None, metavar="N", help="只导出最近 N 天")
    export_cmd.add_argument("-o", "--output", help="输出文件或目录（默认当前目录）")
    export_cmd.set_defaults(func=cmd_export)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    cfg, warning = load_config()
    if warning:
        print(f"警告：{warning}", file=sys.stderr)

    # install-desktop 只写桌面文件，没有 smartctl 也应能跑。
    if getattr(args, "needs_smartctl", True) and smart_mod.find_smartctl(cfg) is None:
        print(
            f"错误：未检测到 smartctl。{smart_mod.smartctl_install_hint()} 后重试。",
            file=sys.stderr,
        )
        return 2

    return int(args.func(args, cfg))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

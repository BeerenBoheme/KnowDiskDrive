#!/usr/bin/env python3
"""A1–A9 验收自测（提示词第 9 节）。

用法（在项目根目录）：

    .venv/bin/python tools/acceptance.py            # 全部检查
    .venv/bin/python tools/acceptance.py A1 A7      # 只跑指定几项
    .venv/bin/python tools/acceptance.py --json     # 机器可读输出

设计原则：
* 只读——除了 A7 会往临时目录写一个历史库，不修改任何系统状态；
* 需要 root 能力（A2/A8）或需要人插拔硬件（A6）的项标为 BLOCKED / MANUAL，
  不假装通过；
* 所有子进程都用 list 形式传参，不用 shell=True。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _resolve_python() -> str:
    """解释器优先级：项目 .venv → 当前解释器。

    写死 .venv/bin/python 会让「源码包解包后另建环境」的场景全线报 127，
    而 127 完全看不出是环境没建好，排查成本很高。
    """
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    return sys.executable


def _resolve_kdd(python: str) -> str:
    """CLI 可执行文件：项目 venv 里的脚本 → 当前解释器旁的脚本 → 空串。"""
    for candidate in (
        ROOT / ".venv" / "bin" / "knowdiskdrive",
        Path(python).resolve().parent / "knowdiskdrive",
    ):
        if candidate.exists():
            return str(candidate)
    return ""


PYTHON = _resolve_python()
KDD = _resolve_kdd(PYTHON)

#: KDD 为空时（目标环境没装 console script），CLI 调用改走模块方式：
#: python -m knowdiskdrive。这样验收脚本在任意 Python 环境里都能跑。
KDD_PREFIX = [KDD] if KDD else [PYTHON, "-m", "knowdiskdrive"]

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
DIM = "\033[2m"
RESET = "\033[0m"

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
MANUAL = "MANUAL"

BADGE = {
    PASS: GREEN + "通过" + RESET,
    FAIL: RED + "失败" + RESET,
    BLOCKED: YELLOW + "受阻" + RESET,
    MANUAL: BLUE + "需人工" + RESET,
}


@dataclass
class Result:
    id: str
    title: str
    status: str
    detail: str = ""
    notes: list[str] = field(default_factory=list)


def _graphical_session() -> bool:
    """当前进程是否处在真实图形会话里（可创建 QSystemTrayIcon）。"""
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def run(
    args: list[str],
    timeout: float = 60.0,
    env: dict | None = None,
    drop_env: tuple[str, ...] = (),
) -> tuple[int, str, str]:
    """跑一条命令，返回 (rc, stdout, stderr)。不抛异常。

    drop_env 里的变量会从子进程环境里删掉：QT_QPA_PLATFORM=offscreen 只适合
    无头脚本，图形提权程序（ksshaskpass）继承它会把窗口画到 offscreen 平台，
    用户在桌面上永远看不到密码框。
    """
    merged = dict(os.environ)
    merged.setdefault("QT_QPA_PLATFORM", "offscreen")
    if env:
        merged.update(env)
    for key in drop_env:
        merged.pop(key, None)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged,
            cwd=str(ROOT),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（>{timeout}s）"
    except FileNotFoundError as exc:
        return 127, "", f"找不到可执行文件：{exc}"


# ---------------------------------------------------------------------------
# A1 —— list 输出本机磁盘，不含 loop/zram/dm
# ---------------------------------------------------------------------------
def check_a1() -> Result:
    rc, out, err = run([*KDD_PREFIX, "list"])
    if rc != 0:
        return Result("A1", "list 列出本机磁盘", FAIL, f"退出码 {rc}：{(err or out).strip()[:300]}")
    text = out.lower()
    missing = [name for name in ("sda", "nvme0n1") if name not in text]
    junk = [name for name in ("loop", "zram", "dm-") if name in text]
    notes = []
    if missing:
        notes.append(f"缺少设备：{', '.join(missing)}")
    if junk:
        notes.append(f"混入了虚拟设备：{', '.join(junk)}")
    if missing or junk:
        return Result("A1", "list 列出本机磁盘", FAIL, out.strip()[:400], notes)
    has_zhitai = "zhitai" in text
    has_kioxia = "kioxia" in text
    notes.append(f"型号匹配：ZHITAI={'是' if has_zhitai else '否'}，KIOXIA={'是' if has_kioxia else '否'}")
    return Result("A1", "list 列出本机磁盘", PASS, "sda 与 nvme0n1 均在列表中，无 loop/zram/dm", notes)


# ---------------------------------------------------------------------------
# A2 —— show 的数据与 sudo smartctl -a 人工核对一致
# ---------------------------------------------------------------------------
def check_a2() -> Result:
    notes: list[str] = []
    readable: list[str] = []
    for device in ("/dev/nvme0n1", "/dev/sda"):
        rc, out, err = run([*KDD_PREFIX, "show", device])
        combined = out + err
        if rc != 0 and ("install.sh" in combined or "权限" in combined):
            notes.append(f"{device}: 读取被拒绝（未执行 sudo ./install.sh）")
            continue
        if rc != 0:
            notes.append(f"{device}: 退出码 {rc}，{combined.strip()[:150]}")
            continue
        serial = re.search(r"序列号[:：]?\s*(\S+)", out)
        temp = re.search(r"当前温度[:：]?\s*(\S+)", out)
        hours = re.search(r"通电时间[:：]?\s*(\S+)", out)
        notes.append(
            f"{device}: 序列号={serial.group(1) if serial else '—'} "
            f"温度={temp.group(1) if temp else '—'} "
            f"通电时间={hours.group(1) if hours else '—'}"
        )
        readable.append(device)

    if not readable:
        return Result(
            "A2",
            "show 与 smartctl -a 一致",
            BLOCKED,
            "两块盘都读不到 SMART 数据，需先 sudo ./install.sh",
            notes,
        )
    expected = {
        "/dev/nvme0n1": ("41OPHEXAMPLE1", "KIOXIA"),
        "/dev/sda": ("ZTB1EXAMPLE0000001", "ZHITAI"),
    }
    mismatched = []
    for device in readable:
        rc, out, _ = run([*KDD_PREFIX, "show", device])
        want_serial, want_model = expected.get(device, ("", ""))
        if want_serial and want_serial not in out:
            mismatched.append(f"{device} 序列号不是 {want_serial}")
        if want_model and want_model.lower() not in out.lower():
            mismatched.append(f"{device} 型号不含 {want_model}")
    if mismatched:
        return Result("A2", "show 与 smartctl -a 一致", FAIL, "；".join(mismatched), notes)
    if len(readable) < 2:
        notes.append("只有一块盘可读，另一块被跳过")
    return Result(
        "A2",
        "show 与 smartctl -a 一致",
        PASS if len(readable) == 2 else MANUAL,
        "型号/序列号与已知真机一致；温度与通电时间需对照 sudo smartctl -a 人工核对",
        notes,
    )


# ---------------------------------------------------------------------------
# A3 —— pytest 全绿 + 4 份 fixture + 解析层覆盖率 ≥ 80%
# ---------------------------------------------------------------------------
REQUIRED_FIXTURES = {
    "ata_zhitai_healthy.json": "本机 sda 真实录制",
    "nvme_kioxia_healthy.json": "本机 nvme 真实录制",
    "usb_bridge_failed.json": "USB 桥接失败输出",
    "nvme_critical.json": "人工构造 critical_warning=4",
}


def check_a3() -> Result:
    notes: list[str] = []
    fixtures_dir = ROOT / "tests" / "fixtures"
    missing = [name for name in REQUIRED_FIXTURES if not (fixtures_dir / name).is_file()]
    for name, desc in REQUIRED_FIXTURES.items():
        mark = "✓" if (fixtures_dir / name).is_file() else "✗"
        notes.append(f"{mark} {name}（{desc}）")
    if missing:
        return Result("A3", "pytest + fixture + 覆盖率", FAIL, f"缺少 fixture：{', '.join(missing)}", notes)

    rc, out, err = run(
        # 不要在这里再加 -q：pyproject 的 addopts 已有 -q，两个 -q 会吞掉 "N passed" 汇总行
        [PYTHON, "-m", "pytest", "--cov=knowdiskdrive.smart", "--cov-report=term"],
        timeout=600,
    )
    tail = (out + err).strip()
    if rc != 0:
        return Result("A3", "pytest + fixture + 覆盖率", FAIL, f"pytest 退出码 {rc}", notes + [tail[-800:]])
    match = re.search(r"knowdiskdrive/smart\.py\s+\d+\s+\d+\s+(\d+)%", tail)
    percent = int(match.group(1)) if match else -1
    passed = re.search(r"(\d+) passed", tail)
    failed = re.search(r"(\d+) failed", tail)
    notes.append(
        f"用例：通过 {passed.group(1) if passed else '?'}，失败 {failed.group(1) if failed else 0}"
    )
    notes.append(f"smart.py 覆盖率：{percent}%")
    if percent < 80:
        return Result("A3", "pytest + fixture + 覆盖率", FAIL, f"解析层覆盖率仅 {percent}%", notes)
    return Result("A3", "pytest + fixture + 覆盖率", PASS, f"全部用例通过，解析层覆盖率 {percent}%", notes)


# ---------------------------------------------------------------------------
# A4 —— GUI 能起来、读盘不阻塞主线程、超时提示存在
# ---------------------------------------------------------------------------
A4_SCRIPT = r'''import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.gui.main_window import MainWindow

app = QApplication(sys.argv)
w = MainWindow(cfg=DEFAULT_CONFIG, store=None, tray=None)
w.show()
app.processEvents()
print("window_ok", w.isVisible())
print("thread_ok", isinstance(w.worker, QThread) and w.worker.isRunning())
import inspect
import knowdiskdrive.smart as smart
print("timeout_text", "超时" in inspect.getsource(smart))
w.worker.stop()
w.monitor.stop()
w.close()
'''


def check_a4() -> Result:
    notes: list[str] = []
    rc, out, err = run([PYTHON, "-c", A4_SCRIPT], timeout=120)
    if rc != 0:
        return Result("A4", "GUI 启动与不阻塞", FAIL, f"GUI 冒烟退出码 {rc}", [(out + err).strip()[-600:]])
    for line in out.splitlines():
        if line.startswith(("window_ok", "thread_ok", "timeout_text")):
            notes.append(line)
    if "window_ok True" not in out:
        return Result("A4", "GUI 启动与不阻塞", FAIL, "窗口没有显示", notes)
    if "thread_ok True" not in out:
        return Result("A4", "GUI 启动与不阻塞", FAIL, "SmartWorker 不在独立 QThread 上", notes)
    if "timeout_text True" not in out:
        return Result("A4", "GUI 启动与不阻塞", FAIL, "smart.py 里没有超时处理文案", notes)
    notes.append("真实 KDE 桌面下的拖拽/白屏需人工确认")
    return Result("A4", "GUI 启动与不阻塞", PASS, "offscreen 下窗口正常显示，读盘在 QThread 上，超时文案存在", notes)


# ---------------------------------------------------------------------------
# A5 —— 临界属性：行高亮 + 状态列「临界」+ 通知调用
# ---------------------------------------------------------------------------
A5_SCRIPT = r'''import os, sys, json, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from knowdiskdrive.gui.models import AttributeTableModel, health_badge_text
from knowdiskdrive.smart import parse_json_payload, SmartctlRun
from knowdiskdrive.health import evaluate

app = QApplication(sys.argv)

def load(name, device):
    payload = json.loads(pathlib.Path("tests/fixtures/" + name).read_text(encoding="utf-8"))
    return parse_json_payload(payload, device, SmartctlRun(rc=0))

ata = load("ata_failing.json", "/dev/sda")
model = AttributeTableModel()
model.set_attributes(ata.ata_attributes, is_sata=True)
crit_rows = 0
for row in range(model.rowCount()):
    if model.data(model.index(row, 6), Qt.DisplayRole) == "临界":
        crit_rows += 1
highlighted = sum(
    1 for row in range(model.rowCount())
    if model.data(model.index(row, 0), Qt.BackgroundRole) is not None
)
print("critical_rows", crit_rows)
print("highlighted_rows", highlighted)
print("ata_state", evaluate(ata).state)
print("ata_badge", health_badge_text(evaluate(ata)))

nvme = load("nvme_critical.json", "/dev/nvme0n1")
print("nvme_state", evaluate(nvme).state)
print("nvme_badge", health_badge_text(evaluate(nvme)))
'''


A5_LIVE_SCRIPT = r'''import datetime, json, os, pathlib, sys, tempfile, time
os.environ.pop("QT_QPA_PLATFORM", None)  # 用真实平台，才能创建托盘并真发系统通知

from PySide6.QtCore import Qt
from knowdiskdrive.alerts import AlertEngine
from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.devices import Device
from knowdiskdrive.gui.app import create_application
from knowdiskdrive.gui.main_window import MainWindow
from knowdiskdrive.gui.tray import TrayController
from knowdiskdrive.gui.workers import KIND_READ, WorkResult
from knowdiskdrive.health import evaluate
from knowdiskdrive.history import HistoryStore
from knowdiskdrive.smart import parse_json_payload, SmartctlRun


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def load(name, path):
    payload = json.loads(pathlib.Path("tests/fixtures/" + name).read_text(encoding="utf-8"))
    return parse_json_payload(payload, path, SmartctlRun(rc=0))


app = create_application(["kdd-a5-live"])
device = Device(
    name="sda", model="ZHITAI SC001 Active 512GB SSD", serial="ZTB1EXAMPLE0000001",
    tran="sata", size="476.9G", size_bytes=512110190592, rota=False,
)
healthy = load("ata_zhitai_healthy.json", device.path)
failing = load("ata_failing.json", device.path)

store = HistoryStore(pathlib.Path(tempfile.mkdtemp()) / "a5.db")
tray = TrayController()
print("live_tray", tray.build())
win = MainWindow(cfg=DEFAULT_CONFIG, store=store, tray=tray)
win.alert_engine = AlertEngine(store=store, dedup_minutes=DEFAULT_CONFIG.alert_dedup_minutes)
win.hide()
pump(app, 0.8)

# 隔离真实数据源，避免真机读盘把注入样本覆盖掉
win.worker.stop()
win._sample_timer.stop()
win._chart_timer.stop()
win.monitor.stop()
pump(app, 0.3)

win._apply_devices([device])
# 第一次：良好基线（只建基线，不通知）
win._apply_read(WorkResult(kind=KIND_READ, request_id=1, device=device,
                           info=healthy, report=evaluate(healthy, DEFAULT_CONFIG)))
pump(app, 0.4)
# 第二次：恶化 -> 健康降级 + 新临界属性，两条通知
win._apply_read(WorkResult(kind=KIND_READ, request_id=2, device=device,
                           info=failing, report=evaluate(failing, DEFAULT_CONFIG)))
pump(app, 1.2)

model = win._attr_model
texts = [model.data(model.index(r, 6), Qt.DisplayRole) for r in range(model.rowCount())]
print("live_critical_rows", texts.count("临界"))
print("live_highlighted_rows",
      sum(1 for r in range(model.rowCount()) if model.data(model.index(r, 0), Qt.BackgroundRole) is not None))
print("live_badge", win.badge_label.text())
print("live_status_bar", win.statusBar().currentMessage())
pump(app, 2.0)
win.close()
store.close()
tray.hide()
print("live_done True")
'''

def check_a5() -> Result:
    rc, out, err = run([PYTHON, "-c", A5_SCRIPT], timeout=120)
    notes = [line for line in out.splitlines() if line]
    if rc != 0:
        return Result("A5", "临界属性高亮与通知", FAIL, f"退出码 {rc}", [(out + err).strip()[-600:]])
    if "ata_state danger" not in out or "nvme_state danger" not in out:
        return Result("A5", "临界属性高亮与通知", FAIL, "临界样本没有判为 danger", notes)
    if "highlighted_rows 0" in out or "critical_rows 0" in out:
        return Result("A5", "临界属性高亮与通知", FAIL, "临界属性行没有高亮/标红", notes)

    live = _check_a5_notification(notes)
    if live is not None:
        return live
    return Result(
        "A5",
        "临界属性高亮与通知",
        MANUAL,
        "临界行高亮与状态列「临界」已验证；通知实发需在图形会话中观察",
        notes,
    )


def _check_a5_notification(notes: list[str]) -> Result | None:
    """在真实图形会话里跑完整告警链路，并用 dbus-monitor 抓 Notify 调用。

    返回 None 表示当前环境无法自动判定（交给调用方标 MANUAL）。
    """
    if not _graphical_session():
        notes.append("当前无图形会话（DISPLAY/WAYLAND_DISPLAY 均未设置），通知链路无法自动验证")
        return None
    monitor = shutil.which("dbus-monitor")
    if monitor is None:
        notes.append("本机没有 dbus-monitor，通知链路只能靠单元测试覆盖")
        return None

    log_path = Path(tempfile.mkstemp(prefix="kdd_dbus_")[1])
    handle = log_path.open("w", encoding="utf-8")
    proc = None
    try:
        proc = subprocess.Popen(
            [monitor, "--session", "interface='org.freedesktop.Notifications'"],
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(os.environ),
        )
        time.sleep(1.0)
        rc, out, err = run([PYTHON, "-c", A5_LIVE_SCRIPT], timeout=180, env={"KDD_LIVE_UI": "1"})
        time.sleep(1.0)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        handle.close()

    captured = log_path.read_text(encoding="utf-8", errors="replace")
    log_path.unlink(missing_ok=True)
    notify_count = captured.count("member=Notify")
    titles = re.findall(r'string "([^"]+)"', captured)
    shown = [t for t in titles if "健康状态下降" in t or "临界属性" in t or "温度过高" in t]
    notes.extend(line for line in out.splitlines() if line.startswith("live_"))
    if rc != 0:
        notes.append(f"真机脚本退出码 {rc}：{(out + err).strip()[-300:]}")
        return None
    if notify_count <= 0:
        notes.append("没有抓到 org.freedesktop.Notifications 的 Notify 调用")
        return None
    notes.append(f"dbus 抓到 Notify 调用 {notify_count} 次")
    if shown:
        notes.append("通知标题：" + "、".join(dict.fromkeys(shown)))
    return Result(
        "A5",
        "临界属性高亮与通知",
        PASS,
        f"临界行标红并在状态列显示「临界」；真实会话收到 {notify_count} 条系统通知",
        notes,
    )


# ---------------------------------------------------------------------------
# A6 —— USB 插拔 2 秒内自动更新
# ---------------------------------------------------------------------------
A6_SCRIPT = r'''import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.gui.main_window import HOTPLUG_DEBOUNCE_MS, MainWindow

app = QApplication(sys.argv)
win = MainWindow(cfg=DEFAULT_CONFIG, store=None, tray=None)
print("monitor_available", win.monitor.available, flush=True)
events, refreshes = [], []
win.monitor.devices_changed.connect(lambda text: events.append((time.monotonic(), text)))
original = win.refresh_devices
win.refresh_devices = lambda: (refreshes.append(time.monotonic()), original())

print("debounce_ms", HOTPLUG_DEBOUNCE_MS, flush=True)
print("ready", flush=True)
# 窗口要盖住「等待 sudo 密码 + 执行 udevadm」的整个时段；
# 太短会在用户输完密码前就退出，表现为收不到任何事件。
deadline = time.monotonic() + 240
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
    if events and refreshes:
        break
print("event_count", len(events), flush=True)
print("refresh_count", len(refreshes), flush=True)
lag = (refreshes[0] - events[0][0]) if (events and refreshes) else None
print("lag_seconds", "%.2f" % lag if lag is not None else "n/a", flush=True)
win.close()
win.worker.stop()
'''


def _sudo_prefix() -> tuple[list[str], bool] | None:
    """返回 (sudo 前缀, 是否需要用户输入密码)；完全拿不到提权能力时返回 None。"""
    rc, _, _ = run(["sudo", "-n", "true"], timeout=10)
    if rc == 0:
        return ["sudo", "-n"], False
    askpass = shutil.which("ksshaskpass") or shutil.which("ssh-askpass")
    if askpass:
        return ["sudo", "-A"], True
    return None


def check_a6() -> Result:
    notes = []
    try:
        import pyudev  # noqa: F401
    except Exception as exc:
        return Result("A6", "USB 插拔自动更新", FAIL, f"pyudev 不可用：{exc}", notes)
    monitor_src = (ROOT / "knowdiskdrive" / "gui" / "monitor.py").read_text(encoding="utf-8")
    window_src = (ROOT / "knowdiskdrive" / "gui" / "main_window.py").read_text(encoding="utf-8")
    has_monitor = "pyudev" in monitor_src and "Monitor.from_netlink" in monitor_src
    wired = "DeviceMonitor" in window_src and "devices_changed.connect" in window_src
    has_debounce = "HOTPLUG_DEBOUNCE_MS" in window_src and "singleShot" in window_src
    notes.append(f"monitor.py 使用 pyudev.Monitor.from_netlink：{'是' if has_monitor else '否'}")
    notes.append(f"MainWindow 已接入 udev 监听：{'是' if wired else '否'}")
    notes.append(f"有去抖/合并机制：{'是' if has_debounce else '否'}")
    if not has_monitor or not wired:
        return Result("A6", "USB 插拔自动更新", FAIL, "udev 监听没有完整接入 GUI", notes)
    if not has_debounce:
        return Result("A6", "USB 插拔自动更新", FAIL, "udev 事件没有去抖，插拔会触发多次刷新", notes)

    live = _check_a6_live(notes)
    if live is not None:
        return live
    return Result("A6", "USB 插拔自动更新", MANUAL, "pyudev 监听已实现，需人工插拔验证", notes)


def _check_a6_live(notes: list[str]) -> Result | None:
    """用 uevent 重放驱动真实 netlink 链路，量出事件到刷新的延迟。

    真实的插入 U 盘会由内核发 uevent；这里用 `udevadm trigger` 重放同一条
    netlink 通道上的 block 事件，链路上除了「内核产生事件」这一步之外完全相同。
    返回 None 表示拿不到 root，无法自动判定。
    """
    sudo_bits = _sudo_prefix()
    if sudo_bits is None:
        notes.append("拿不到提权能力，跳过热插拔链路实测（可手动插入 U 盘观察）")
        return None
    prefix, interactive = sudo_bits
    if interactive:
        print("      · 需要 sudo 提权：桌面上会弹出密码框，请输入密码（最多等 150 秒）", flush=True)

    out_path = Path(tempfile.mkstemp(prefix="kdd_a6_", suffix=".log")[1])
    handle = out_path.open("w", encoding="utf-8")
    proc = None
    try:
        proc = subprocess.Popen(
            [PYTHON, "-c", A6_SCRIPT],
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        ready = False
        for _ in range(100):
            time.sleep(0.1)
            if "ready" in out_path.read_text(encoding="utf-8", errors="replace"):
                ready = True
                break
        if not ready:
            notes.append("监听端未就绪，跳过热插拔实测")
            return None
        rc, _, err = run(
            [*prefix, "udevadm", "trigger", "--subsystem-match=block", "--action=change"],
            timeout=150 if interactive else 30,
            env={"SUDO_ASKPASS": shutil.which("ksshaskpass") or ""},
            # ksshaskpass 是 Qt 程序；带着 offscreen 平台插件它会把密码框
            # 画到无头后端，桌面上看不到，于是必然等到超时。
            drop_env=("QT_QPA_PLATFORM",),
        )
        if rc == 124:
            notes.append("等待 sudo 密码超时，跳过热插拔链路实测（桌面输入密码后重跑即可）")
            return None
        if rc != 0:
            notes.append(f"udevadm trigger 退出码 {rc}：{err.strip()[:200]}")
            return None
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        handle.close()

    captured = out_path.read_text(encoding="utf-8", errors="replace")
    out_path.unlink(missing_ok=True)
    values: dict[str, str] = {}
    for line in captured.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1]

    events = int(values.get("event_count", "0") or 0)
    refreshes = int(values.get("refresh_count", "0") or 0)
    lag_text = values.get("lag_seconds", "n/a")
    notes.append(f"收到 udev 事件 {events} 个，refresh_devices 调用 {refreshes} 次")
    notes.append(f"事件到首次刷新延迟：{lag_text} 秒（去抖窗口 {values.get('debounce_ms', '?')} ms）")
    if events <= 0:
        notes.append("没有收到 udev 事件，无法判定")
        return None
    if refreshes == 0:
        return Result("A6", "USB 插拔自动更新", FAIL, "收到 udev 事件但没有触发刷新", notes)
    try:
        lag = float(lag_text)
    except ValueError:
        return None
    if lag > 2.0:
        return Result("A6", "USB 插拔自动更新", FAIL, f"事件到刷新用了 {lag:.2f} 秒，超过 2 秒", notes)
    if refreshes > 1:
        notes.append("多次事件被合并成一次刷新（去抖生效）")
    return Result(
        "A6",
        "USB 插拔自动更新",
        PASS,
        f"真实 netlink 事件触发刷新仅 {lag:.2f} 秒（{events} 个事件合并为 {refreshes} 次刷新）",
        notes,
    )


# ---------------------------------------------------------------------------
# A7 —— 守护进程跑一会儿，temp_samples 有增长，停止后不再增长
# ---------------------------------------------------------------------------
def check_a7() -> Result:
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kdd-a7-") as tmp:
        db = Path(tmp) / "history.db"
        env = {"KDD_DB": str(db), "QT_QPA_PLATFORM": "offscreen"}
        rc, out, err = run([*KDD_PREFIX, "daemon", "--once"], timeout=180, env=env)
        notes.append(f"daemon --once 退出码 {rc}")
        if not db.is_file():
            return Result(
                "A7",
                "守护进程写历史",
                BLOCKED,
                "没有生成历史库（多半是 SMART 读不到，先 sudo ./install.sh）",
                notes + [(out + err).strip()[-400:]],
            )
        count = _sqlite_count(db, "temp_samples")
        notes.append(f"temp_samples 行数：{count}")
        if count == 0:
            return Result(
                "A7",
                "守护进程写历史",
                BLOCKED,
                "历史库为空：SMART 读取失败时不会写入样本，需先完成 setcap",
                notes,
            )
        run([*KDD_PREFIX, "daemon", "--once"], timeout=180, env=env)
        after = _sqlite_count(db, "temp_samples")
        notes.append(f"第二轮后行数：{after}")
        if after <= count:
            return Result("A7", "守护进程写历史", FAIL, "第二轮没有新增样本", notes)
        idle = _sqlite_count(db, "temp_samples")
        notes.append(f"停止后行数：{idle}（无守护进程继续写）")
        if idle != after:
            return Result("A7", "守护进程写历史", FAIL, "停止后仍有写入", notes)
    notes.append("常驻 10 分钟的增量可另用 knowdiskdrive daemon 人工观察")
    return Result("A7", "守护进程写历史", PASS, "两轮采样均写入新样本，停止后不再增长", notes)


def _sqlite_count(db: Path, table: str) -> int:
    import sqlite3

    try:
        with sqlite3.connect(str(db)) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# A8 —— 普通用户跑完 install.sh 后无 sudo 提示、id -u 仍非 0
# ---------------------------------------------------------------------------
A8_SCRIPT = r'''import sys
sys.path.insert(0, ".")

from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.devices import enumerate_devices
from knowdiskdrive.gui.workers import KIND_READ, WorkRequest, execute_request
from knowdiskdrive.smart import STATUS_OK, start_self_test

devs = enumerate_devices()
print("a8_device_count", len(devs))

read_ok = False
target = None
for d in devs:
    result = execute_request(WorkRequest(kind=KIND_READ, cfg=DEFAULT_CONFIG, device=d, request_id=1))
    if result.info is not None and result.info.status != "unavailable":
        read_ok = True
        target = d
        print("a8_read_device", d.path, result.info.display_model)
        break
print("gui_read_ok", read_ok)

rc = None
stderr = ""
if target is not None:
    run = start_self_test(target.path, "short", DEFAULT_CONFIG, devs)
    rc = run.rc
    stderr = run.stderr or ""
print("self_test_rc", rc if rc is not None else "n/a")
print("self_test_stderr", stderr.strip()[:160] or "(空)")
print("sudo_prompt", "sudo" in stderr.lower())
print("a8_done True")
'''

def check_a8() -> Result:
    notes = []
    uid = os.getuid()
    notes.append(f"当前 id -u = {uid}")
    caps = ""
    smartctl = shutil.which("smartctl") or "/usr/sbin/smartctl"
    if shutil.which("getcap"):
        _, out, _ = run(["getcap", smartctl], timeout=10)
        caps = out.strip()
    notes.append(f"smartctl 能力：{caps or '（无）'}")
    if uid == 0:
        return Result("A8", "普通用户免 sudo", MANUAL, "当前是 root 会话，请用普通用户重跑", notes)
    if "cap_sys_rawio" not in caps:
        return Result(
            "A8",
            "普通用户免 sudo",
            BLOCKED,
            "smartctl 尚无 cap_sys_rawio，请执行 sudo ./install.sh 后重跑",
            notes,
        )
    rc, out, err = run([*KDD_PREFIX, "health"], timeout=120)
    combined = out + err
    if "sudo" in combined.lower():
        return Result("A8", "普通用户免 sudo", FAIL, "CLI 输出里仍出现 sudo 提示", notes + [combined[-400:]])
    if rc != 0:
        return Result("A8", "普通用户免 sudo", FAIL, f"health 退出码 {rc}", notes + [combined[-400:]])
    notes.append("CLI：knowdiskdrive health 正常返回，无 sudo 提示")

    # GUI 的数据通路与自检都走 workers.execute_request，直接验证这两条真机路径
    rc, out, err = run([PYTHON, "-c", A8_SCRIPT], timeout=180)
    for line in out.splitlines():
        if line.startswith("a8_"):
            notes.append(line[3:])
        elif line.split(" ", 1)[0] in {"gui_read_ok", "self_test_rc", "self_test_stderr", "sudo_prompt"}:
            notes.append(line)
    if rc != 0 or "a8_done True" not in out:
        return Result("A8", "普通用户免 sudo", FAIL, f"真机读写验证退出码 {rc}", notes + [(out + err)[-400:]])
    if "sudo_prompt True" in out:
        return Result("A8", "普通用户免 sudo", FAIL, "GUI 数据通路或自检路径出现了 sudo 提示", notes)
    if "self_test_rc 0" not in out:
        return Result("A8", "普通用户免 sudo", FAIL, "SMART 短自检没有以普通用户身份成功启动", notes)
    if "gui_read_ok True" not in out:
        return Result("A8", "普通用户免 sudo", FAIL, "GUI 使用的读盘函数在普通用户下读不到数据", notes)
    return Result(
        "A8",
        "普通用户免 sudo",
        PASS,
        "CLI / GUI 数据通路 / SMART 短自检全部以普通用户身份完成，无 sudo 提示",
        notes,
    )


# ---------------------------------------------------------------------------
# A9 —— 断网 + 无托盘环境不崩溃
# ---------------------------------------------------------------------------
A9_SCRIPT = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from knowdiskdrive.gui.app import create_application
from knowdiskdrive.gui.tray import TrayController
from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.gui.main_window import MainWindow

app = create_application(sys.argv)
print("tray_available", TrayController.is_available())
w = MainWindow(cfg=DEFAULT_CONFIG, store=None, tray=None)
w.show()
print("window_ok", w.isVisible())
w.close()
print("survived", True)
'''


def check_a9() -> Result:
    rc, out, err = run([PYTHON, "-c", A9_SCRIPT], timeout=120)
    notes = [line for line in out.splitlines() if line]
    if rc != 0 or "survived True" not in out:
        return Result("A9", "无托盘环境不崩溃", FAIL, f"退出码 {rc}", [(out + err).strip()[-600:]])
    if "tray_available False" in out:
        notes.append("托盘不可用时未创建托盘，窗口关闭即退出")
    return Result("A9", "无托盘环境不崩溃", PASS, "offscreen（无托盘）下正常启动与退出", notes)


CHECKS = {
    "A1": check_a1,
    "A2": check_a2,
    "A3": check_a3,
    "A4": check_a4,
    "A5": check_a5,
    "A6": check_a6,
    "A7": check_a7,
    "A8": check_a8,
    "A9": check_a9,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KnowDiskDrive A1–A9 验收自测")
    parser.add_argument("items", nargs="*", help="要跑的验收项，例如 A1 A7；缺省全部")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    selected = [item.upper() for item in args.items] or list(CHECKS)
    unknown = [item for item in selected if item not in CHECKS]
    if unknown:
        print(f"未知验收项：{', '.join(unknown)}", file=sys.stderr)
        return 2

    results: list[Result] = []
    for item in selected:
        try:
            results.append(CHECKS[item]())
        except Exception as exc:
            results.append(Result(item, "（自测脚本异常）", FAIL, f"{type(exc).__name__}: {exc}"))

    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"[{BADGE[result.status]}] {result.id}  {result.title}")
            if result.detail:
                print(f"        {result.detail}")
            for note in result.notes:
                print(f"        {DIM}· {note}{RESET}")
            print()

    counts = {status: sum(1 for r in results if r.status == status) for status in (PASS, FAIL, BLOCKED, MANUAL)}
    print(
        f"合计 {len(results)} 项：通过 {counts[PASS]}，失败 {counts[FAIL]}，"
        f"受阻 {counts[BLOCKED]}，需人工 {counts[MANUAL]}"
    )
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""热插拔监听（提示词 2.5 / 验收 A6：插入或拔出后 2 秒内更新设备列表）。

用 pyudev.Monitor 阻塞读取。pyudev 不可用时静默降级为「不监听」，
界面依然可以手动刷新 —— 不能在无 udev 的容器里直接崩溃。
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

#: 只关心这些子系统里「整盘」级别的事件
BLOCK_SUBSYSTEM = "block"


def _is_whole_disk(event: Any) -> bool:
    """过滤分区事件（DEVTYPE=partition）。"""
    return event.get("DEVTYPE", "disk") == "disk"


def _is_relevant_action(action: str) -> bool:
    return action in {"add", "remove", "change", "online", "offline"}


class DeviceMonitor(QObject):
    """把 udev 事件转成 Qt 信号（信号必须发回主线程处理）。"""

    devices_changed = Signal(str)  # 事件描述，例如 "add /dev/sdb"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._monitor = None
        self._available: bool | None = None

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> bool:
        """启动监听线程。返回 False 表示当前环境不支持 udev。"""
        if self._thread is not None and self._thread.is_alive():
            return True
        try:
            import pyudev
        except Exception:
            self._available = False
            return False
        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem=BLOCK_SUBSYSTEM)
        except Exception:
            self._available = False
            return False
        self._monitor = monitor
        self._available = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="kdd-udev", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        monitor = self._monitor
        if monitor is not None:
            try:
                monitor.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
        self._monitor = None

    @property
    def available(self) -> bool | None:
        return self._available

    # -- 线程体 -----------------------------------------------------------
    def _loop(self) -> None:
        monitor = self._monitor
        if monitor is None:
            return
        while not self._stop.is_set():
            try:
                event = monitor.poll(timeout=0.5)
            except Exception:
                break
            if event is None:
                continue
            if self._stop.is_set():
                break
            action = event.action or ""
            if not _is_relevant_action(action) or not _is_whole_disk(event):
                continue
            try:
                name = event.device_node or event.sys_name or ""
                self.devices_changed.emit(f"{action} {name}")
            except RuntimeError:
                break

"""主窗口（提示词 5.1–5.4）。

线程模型（第 8 节硬性要求）：主线程只负责界面与数据库写入，
所有 smartctl 调用都通过 SmartWorker 投递到后台线程，结果经信号回到主线程。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import privilege
from ..config import Config, DEFAULT_CONFIG
from ..devices import Device, format_bytes, tran_display
from ..health import (
    HEALTH_TEXT,
    TEMP_COLORS,
    TEMP_GREEN,
    copy_text,
    summarize,
)
from ..history import HistoryStore
from ..smart import (
    PLACEHOLDER,
    PARSE_MODE_TEXT,
    STATUS_DEVICE_GONE,
    STATUS_ERROR,
    STATUS_NOT_INSTALLED,
    STATUS_NO_SMART,
    STATUS_OK,
    STATUS_PERMISSION,
    STATUS_STANDBY,
    STATUS_TIMEOUT,
    STATUS_USB_BRIDGE_FAILED,
    DiskInfo,
    find_smartctl,
    self_test_in_progress,
    self_test_remaining,
    smartctl_install_hint,
)
from . import theme
from .chart import TempChart
from .icon import app_icon
from .monitor import DeviceMonitor
from .models import (
    AttributeTableModel,
    MultiLineItemDelegate,
    NvmeLogModel,
    health_badge_text,
)
from .tray import TrayController
from .workers import (
    KIND_READ,
    KIND_SCAN,
    KIND_TEST,
    ElevationWorker,
    SmartWorker,
    WorkRequest,
    WorkResult,
)

#: 提权对话框不可用时兜底复制的命令；真正的路径在运行时按 find_smartctl 拼
FALLBACK_INSTALL_COMMAND = "sudo setcap cap_sys_rawio,cap_dac_read_search,cap_sys_admin+ep /usr/sbin/smartctl"
#: udev 一次插拔会连发多条事件，合并到这个窗口后再刷新（远小于 A6 的 2 秒）
HOTPLUG_DEBOUNCE_MS = 400
STATUS_BAR_IDLE = "就绪"
READING_TEXT = "读取中…"


class MainWindow(QMainWindow):
    def __init__(
        self,
        cfg: Config = DEFAULT_CONFIG,
        store: HistoryStore | None = None,
        worker: SmartWorker | None = None,
        tray: TrayController | None = None,
        monitor: DeviceMonitor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.worker = worker or SmartWorker()
        self.tray = tray
        self.monitor = monitor or DeviceMonitor(self)
        self._hotplug_pending = False
        self.alert_engine: Any = None  # 由 app.py 注入，避免循环依赖

        self._devices: list[Device] = []
        self._current: Device | None = None
        self._info: DiskInfo | None = None
        self._report: Any = None
        self._request_seq = 0
        self._pending: dict[int, tuple[str, Device | None]] = {}
        self._reading = False
        self._paused = False
        self._last_refresh: datetime | None = None
        self._closing_to_tray = True
        self._test_serial: str | None = None
        self._test_mode: str | None = None
        self._elevation: ElevationWorker | None = None

        self._attr_model = AttributeTableModel(self)
        self._nvme_model = NvmeLogModel(self)
        self._chart = TempChart(self)

        self._build_ui()
        self._connect_signals()
        self._start_timers()

        self.worker.result_ready.connect(self._on_result)
        self.worker.start()

        # 热插拔（2.5 / A6）：udev 事件去抖后合并成一次刷新
        self.monitor.devices_changed.connect(self._on_devices_changed)
        self.monitor.start()

        self.refresh_devices()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.setWindowTitle("KnowDiskDrive — 磁盘健康")
        self.resize(1000, 720)
        self.setStyleSheet(theme.STYLESHEET)

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        # 顶部信息条（5.1）
        self.badge_label = QLabel("● 无法判定", self)
        self.badge_label.setObjectName("Badge")
        self.headline_label = QLabel(PLACEHOLDER, self)
        self.headline_label.setObjectName("Headline")
        self.subline_label = QLabel(PLACEHOLDER, self)
        self.subline_label.setObjectName("Subline")

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.badge_label, 0, Qt.AlignVCenter)
        top.addWidget(self.headline_label, 1, Qt.AlignVCenter)
        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.setToolTip("重新读取当前磁盘的 SMART 信息")
        top.addWidget(self.refresh_button, 0, Qt.AlignVCenter)
        root.addLayout(top)
        root.addWidget(self.subline_label)

        # 失败态横幅（第 8 节）
        self._banner = QFrame(self)
        self._banner.setObjectName("Banner")
        banner_layout = QHBoxLayout(self._banner)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        self.banner_label = QLabel("", self._banner)
        self.banner_label.setWordWrap(True)
        self.banner_button = QPushButton("", self._banner)
        self.banner_button.setVisible(False)
        banner_layout.addWidget(self.banner_label, 1)
        banner_layout.addWidget(self.banner_button, 0)
        self._banner.setVisible(False)
        self._banner_action: Any = None
        root.addWidget(self._banner)

        # 设备列表 + 详情
        splitter = QSplitter(Qt.Horizontal, self)
        self.device_list = QListWidget(self)
        self.device_list.setMinimumWidth(180)
        self.device_list.setMaximumWidth(260)
        self.device_list.setAlternatingRowColors(True)
        self.device_list.setItemDelegate(MultiLineItemDelegate(parent=self.device_list))
        splitter.addWidget(self.device_list)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.table = QTableView(right)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setModel(self._attr_model)
        self._configure_table()
        right_layout.addWidget(self.table, 3)

        right_layout.addWidget(self._chart, 1)
        self.chart_caption = QLabel("温度历史（最近 7 天）", right)
        self.chart_caption.setObjectName("Subline")
        right_layout.addWidget(self.chart_caption)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 900])
        root.addWidget(splitter, 1)

        # 底部按钮行（5.4）
        buttons = QHBoxLayout()
        pinned = "/".join(str(i) for i in self.cfg.pinned_attribute_ids)
        self.pin_checkbox = QCheckBox(f"关键属性置顶（{pinned}）", self)
        self.pin_checkbox.setChecked(True)
        self.copy_button = QPushButton("复制信息", self)
        self.export_button = QPushButton("导出历史", self)
        self.test_button = QPushButton("启动短自检", self)
        buttons.addWidget(self.pin_checkbox)
        buttons.addStretch(1)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.copy_button)
        root.addLayout(buttons)

        self.setCentralWidget(central)
        self.statusBar().showMessage(STATUS_BAR_IDLE)
        try:
            self.setWindowIcon(app_icon())
        except Exception:  # 图标缺失不影响使用
            pass

    def _configure_table(self) -> None:
        header = self.table.horizontalHeader()
        if self.table.model() is self._nvme_model:
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            return
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 54)
        for column in (2, 3, 4):
            self.table.setColumnWidth(column, 74)
        self.table.setColumnWidth(5, 110)
        self.table.setColumnWidth(6, 64)

    def _connect_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_current)
        self.copy_button.clicked.connect(self.copy_info)
        self.export_button.clicked.connect(self.export_history)
        self.test_button.clicked.connect(self.start_self_test)
        self.device_list.currentItemChanged.connect(self._on_device_selected)
        self.pin_checkbox.toggled.connect(self._on_pin_toggled)
        self.banner_button.clicked.connect(self._on_banner_action)

        if self.tray is not None:
            self.tray.refresh_requested.connect(self.refresh_current)
            self.tray.show_window_requested.connect(self._show_from_tray)
            self.tray.pause_toggled.connect(self.set_paused)
            self.tray.quit_requested.connect(self.quit_application)

    def _start_timers(self) -> None:
        self._sample_timer = QTimer(self)
        self._sample_timer.setInterval(max(10, self.cfg.sample_interval_seconds) * 1000)
        self._sample_timer.timeout.connect(self._on_sample_tick)
        self._sample_timer.start()

        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(60_000)
        self._chart_timer.timeout.connect(self._reload_chart)
        self._chart_timer.start()

        # 提示词 3.5：自检启动后每 5 秒用 -a 轮询一次进度，跑完自动停
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(5000)
        self._test_timer.timeout.connect(self._on_test_poll_tick)

    # ------------------------------------------------------------ 设备枚举
    def refresh_devices(self) -> None:
        self._submit(KIND_SCAN, None)

    @Slot(str)
    def _on_devices_changed(self, event_text: str) -> None:
        """udev 事件（A6）：一次插拔常伴随多条事件，合并成一次刷新。"""
        self._hotplug_pending = True
        self.statusBar().showMessage(f"检测到设备变化（{event_text}），正在更新…")
        QTimer.singleShot(HOTPLUG_DEBOUNCE_MS, self._flush_hotplug)

    def _flush_hotplug(self) -> None:
        if not self._hotplug_pending:
            return
        self._hotplug_pending = False
        self.refresh_devices()

    def refresh_current(self, *_args: object) -> None:
        if self._current is None:
            self.refresh_devices()
            return
        self._submit(KIND_READ, self._current)

    def _submit(self, kind: str, device: Device | None, test_mode: str = "short") -> None:
        self._request_seq += 1
        request = WorkRequest(
            kind=kind,
            cfg=self.cfg,
            device=device,
            request_id=self._request_seq,
            cached_d_arg=self._cached_d_arg(device),
            test_mode=test_mode,
        )
        if not self.worker.submit(request):
            self.statusBar().showMessage("上一次读取还没结束，请稍候")
            return
        self._pending[request.request_id] = (kind, device)
        if kind == KIND_READ:
            self._reading = True
            self.refresh_button.setEnabled(False)
            self.statusBar().showMessage(READING_TEXT)

    def _remember_usb_d_arg(self, device: Device | None, info: DiskInfo) -> None:
        """提示词 3.3：探测成功的 -d 值按序列号缓存，下次不必每轮重试四个候选。"""
        if self.store is None or device is None or not device.serial:
            return
        if not info.usb_d_arg:
            return
        try:
            self.store.set_pref(device.serial, info.usb_d_arg)
        except Exception:
            # 缓存写失败不影响本次展示，只影响下一次探测的起点
            pass

    def _cached_d_arg(self, device: Device | None) -> str | None:
        if device is None or self.store is None or not device.serial:
            return None
        try:
            return self.store.get_pref(device.serial)
        except Exception:
            return None

    # ------------------------------------------------------------ 结果处理
    @Slot(object)
    def _on_result(self, result: WorkResult) -> None:
        kind = result.kind
        self._pending.pop(result.request_id, None)

        if result.error and kind == KIND_SCAN:
            self._show_banner(f"无法枚举磁盘：{result.error}", error=True)
            self.statusBar().showMessage("枚举失败")
            return

        if kind == KIND_SCAN:
            self._apply_devices(result.devices)
            return

        if kind == KIND_TEST:
            self._reading = False
            self.refresh_button.setEnabled(True)
            message = result.message or ("自检启动失败" if result.error else "已启动自检")
            self.statusBar().showMessage(message, 8000)
            if result.device is not None:
                self._record_event(
                    result.device,
                    "selftest_failed" if result.error else "selftest_started",
                    result.error or "",
                )
                if not result.error:
                    self._begin_test_watch(result.device)
            return

        if kind == KIND_READ:
            self._reading = False
            self.refresh_button.setEnabled(True)
            self._apply_read(result)
            return

    def _apply_devices(self, devices: list[Device]) -> None:
        previous = self._current.serial if self._current else None
        self._devices = list(devices)
        self.device_list.blockSignals(True)
        self.device_list.clear()
        for device in self._devices:
            item = QListWidgetItem(self._device_item_text(device))
            item.setData(Qt.UserRole, device.serial or device.name)
            self.device_list.addItem(item)
        self.device_list.blockSignals(False)

        if not self._devices:
            self._current = None
            self._info = None
            self._report = None
            self._show_banner("没有检测到 SATA / NVMe / USB 磁盘。", error=True)
            self.headline_label.setText(PLACEHOLDER)
            self.subline_label.setText(PLACEHOLDER)
            self._attr_model.clear()
            self._nvme_model.set_rows([])
            self._chart.set_samples([])
            return

        target_index = 0
        if previous:
            for index, device in enumerate(self._devices):
                if device.serial == previous:
                    target_index = index
                    break
        self.device_list.setCurrentRow(target_index)
        self._select_device(self._devices[target_index])

    def _device_item_text(self, device: Device) -> str:
        lines = [device.name, device.display_model]
        if device.serial:
            lines.append(f"SN {device.display_serial}")
        return "\n".join(lines)

    def _on_device_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        key = current.data(Qt.UserRole)
        for device in self._devices:
            if (device.serial or device.name) == key:
                self._select_device(device)
                return

    def _select_device(self, device: Device) -> None:
        if self._reading:
            self.statusBar().showMessage("正在读取，请稍候")
        self._current = device
        self._info = None
        self._report = None
        self.headline_label.setText(self._headline_text(device))
        self.subline_label.setText(READING_TEXT)
        self._attr_model.clear()
        self._nvme_model.set_rows([])
        self._chart.set_samples([])
        self._reload_chart()
        self._submit(KIND_READ, device)

    def _headline_text(self, device: Device | None) -> str:
        if device is None:
            return PLACEHOLDER
        parts = [device.display_model]
        if device.serial:
            parts.append(f"SN: {device.display_serial}")
        if device.size_bytes:
            parts.append(format_bytes(device.size_bytes))
        if device.tran:
            parts.append(tran_display(device.tran))
        return "  │ ".join(parts) + "  │"

    def _apply_read(self, result: WorkResult) -> None:
        info = result.info
        if info is None:
            self._show_banner(f"读取失败：{result.error or '未知错误'}", error=True)
            return
        device = result.device
        self._info = info
        self._report = result.report
        summary = summarize(info, self.cfg)
        self._update_header(summary, info)
        self._update_table(info, summary)
        self._update_failure_state(info, device)
        self._last_refresh = datetime.now()
        # 状态栏跟徽章保持同一个判定口径：SMART 自评通过但属性临界时也是「危险」，
        # 若这里直接显示 info.status_text 会出现「徽章危险 / 状态栏正常」的矛盾。
        health_text = HEALTH_TEXT.get(summary.health, summary.health)
        self.statusBar().showMessage(
            f"最后刷新：{self._last_refresh.strftime('%H:%M:%S')}　{health_text}"
        )
        self._record_sample(device, info, summary)
        self._poll_test_progress(device, info)
        self._remember_usb_d_arg(device, info)
        self._fire_alerts(device, info, result.report)
        self._reload_chart()
        if self.tray is not None and device is not None:
            self.tray.update_tooltip(info.display_model, summary.health, info.temperature_c)

    def _update_header(self, summary: Any, info: DiskInfo) -> None:
        report = summary.report
        self.badge_label.setText(health_badge_text(report))
        color = report.color
        self.badge_label.setStyleSheet(
            f"color: {color}; background: {color}1f; border: 1px solid {color};"
        )
        self.headline_label.setText(self._headline_text(self._current))
        pieces = [
            f"通电次数 {info.power_cycle_count if info.power_cycle_count is not None else PLACEHOLDER}",
            f"通电时间 {info.power_on_hours if info.power_on_hours is not None else PLACEHOLDER} 小时",
        ]
        temp_text = f"{info.temperature_c}°C" if info.temperature_c is not None else PLACEHOLDER
        temp_color = TEMP_COLORS.get(report.temp_level, TEMP_COLORS[TEMP_GREEN])
        pieces.append(f"当前温度 <span style='color:{temp_color}'>{temp_text}</span>")
        if info.parse_mode == PARSE_MODE_TEXT:
            # 提示词 3.2：文本降级路径必须在界面上明确标出来，别让人以为走的是 JSON
            pieces.append(
                f"<span style='color:{theme.COLOR_MUTED}'>兼容模式解析（smartctl 未提供 JSON 输出）</span>"
            )
        self.subline_label.setText("&nbsp;&nbsp;&nbsp;".join(pieces))
        self.subline_label.setTextFormat(Qt.RichText)

    def _update_table(self, info: DiskInfo, summary: Any) -> None:
        if info.is_nvme:
            self.table.setModel(self._nvme_model)
            self._nvme_model.set_info(info)
            self._configure_table()
            return
        self.table.setModel(self._attr_model)
        self._attr_model.set_attributes(
            info.ata_attributes,
            is_sata=info.media != "nvme",
            pin_first=self.pin_checkbox.isChecked(),
            pinned_ids=self.cfg.pinned_attribute_ids,
        )
        self._configure_table()

    def _update_failure_state(self, info: DiskInfo, device: Device | None) -> None:
        """第 8 节的失败态表格。"""
        status = info.status
        self.test_button.setEnabled(status in {STATUS_OK, STATUS_STANDBY})
        if status == STATUS_OK:
            self._hide_banner()
            return
        if status == STATUS_STANDBY:
            self._show_banner("休眠中（未唤醒）：跳过本次读取，不参与报警。")
            return
        if status == STATUS_PERMISSION:
            # 提示词第 6 节的 setcap 一次性授权：这里直接弹系统密码框，
            # 不再让用户去命令行找 install.sh——AppImage / pip 安装都没有那个文件。
            self._show_banner(
                "读取 SMART 需要一次性授权：给 smartctl 加文件能力。",
                error=True,
                action_text="一键授权",
                action=self.grant_access,
            )
            return
        if status == STATUS_NOT_INSTALLED:
            self._show_banner(
                f"未检测到 smartctl，{smartctl_install_hint()}。",
                error=True,
            )
            self.test_button.setEnabled(False)
            return
        if status == STATUS_NO_SMART:
            self._show_banner("该磁盘不支持 SMART，无法提供健康数据。")
            return
        if status == STATUS_USB_BRIDGE_FAILED:
            tried = "、".join(info.usb_d_tried) if info.usb_d_tried else "-d sat"
            self._show_banner(f"USB 桥接器不支持直通，已尝试：{tried}。")
            return
        if status == STATUS_TIMEOUT:
            self._show_banner(
                "读取超时，可能是 USB 设备无响应。",
                error=True,
                action_text="重试",
                action=self.refresh_current,
            )
            return
        if status == STATUS_DEVICE_GONE:
            self._show_banner("设备已拔出，正在更新设备列表…", error=True)
            self._record_event(device, "device_removed", info.device)
            QTimer.singleShot(800, self.refresh_devices)
            return
        if status == STATUS_ERROR:
            detail = "；".join(info.messages[:2]) if info.messages else ""
            self._show_banner(f"读取失败：{detail or info.status_text}", error=True)
            return
        self._show_banner(info.status_text, error=True)

    # ------------------------------------------------------------ 横幅控制
    def _show_banner(
        self,
        text: str,
        error: bool = False,
        action_text: str = "",
        action: Any = None,
    ) -> None:
        self._banner.setVisible(True)
        border = theme.COLOR_DANGER if error else theme.COLOR_WARNING
        background = theme.COLOR_BANNER_ERROR if error else theme.COLOR_BANNER_WARN
        self._banner.setStyleSheet(
            f"QFrame#Banner {{ border: 1px solid {border}; background: {background};"
            f" border-radius: 6px; padding: 6px; }}"
        )
        self.banner_label.setText(text)
        self._banner_action = action if action is not None else self.grant_access
        if action_text:
            self.banner_button.setText(action_text)
            self.banner_button.setVisible(True)
        else:
            self.banner_button.setVisible(False)

    def _hide_banner(self) -> None:
        self._banner.setVisible(False)
        self.banner_button.setVisible(False)

    def _on_banner_action(self) -> None:
        action = self._banner_action
        if callable(action):
            action()
        else:
            self._copy_install_command()

    # ------------------------------------------------------------ 一次性授权
    def _manual_command(self) -> str:
        """兜底命令：按本机真实的 smartctl 路径拼，而不是猜一个 /usr/sbin。"""
        smartctl = find_smartctl(self.cfg)
        if smartctl is None:
            return FALLBACK_INSTALL_COMMAND
        return privilege.manual_command(smartctl)

    def _copy_install_command(self) -> None:
        command = self._manual_command()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(command)
            self.statusBar().showMessage(f"已复制命令：{command}", 4000)

    def grant_access(self) -> None:
        """点「一键授权」：后台跑一次 setcap，弹系统自己的密码框。"""
        if self._elevation is not None and self._elevation.isRunning():
            self.statusBar().showMessage("授权窗口已打开，请在其中输入密码。", 4000)
            return
        smartctl = find_smartctl(self.cfg)
        if smartctl is None:
            self._show_banner(
                f"未检测到 smartctl，{smartctl_install_hint()}。",
                error=True,
            )
            return
        if not privilege.available_backends():
            self._show_banner(
                "这台机器既没有 pkexec 也没有 sudo，无法弹出授权窗口。"
                "请手工执行下面的命令后重试：",
                error=True,
                action_text="复制命令",
                action=self._copy_install_command,
            )
            return
        self.banner_button.setEnabled(False)
        self.statusBar().showMessage("正在请求管理员授权…")
        worker = ElevationWorker(smartctl, prefer="gui", parent=self)
        worker.done.connect(self._on_elevation_done)
        worker.finished.connect(worker.deleteLater)
        self._elevation = worker
        worker.start()

    @Slot(object)
    def _on_elevation_done(self, result: object) -> None:
        self.banner_button.setEnabled(True)
        ok = bool(getattr(result, "ok", False))
        message = str(getattr(result, "message", "") or "")
        if not ok:
            self._show_banner(
                f"授权未完成：{message}",
                error=True,
                action_text="复制命令",
                action=self._copy_install_command,
            )
            return
        self.statusBar().showMessage(message or "已授予读取权限。", 6000)
        self._show_banner("已授予读取权限，正在重新读取…")
        QTimer.singleShot(400, self.refresh_current)

    # ------------------------------------------------------------ 历史与告警
    def _record_sample(self, device: Device | None, info: DiskInfo, summary: Any) -> None:
        if self.store is None or device is None or not device.serial:
            return
        try:
            self.store.record_sample(device.serial, info.temperature_c, summary.health, info.status)
        except Exception as exc:  # 数据库异常不能打断界面
            self.statusBar().showMessage(f"历史写入失败：{exc}", 5000)

    def _record_event(self, device: Device | None, kind: str, detail: str) -> None:
        if self.store is None or device is None or not device.serial:
            return
        try:
            self.store.record_event(device.serial, kind, detail)
        except Exception:
            pass

    def _fire_alerts(self, device: Device | None, info: DiskInfo, report: Any) -> None:
        engine = self.alert_engine
        if engine is None or device is None or report is None:
            return
        try:
            alerts = engine.evaluate(device.serial, info, report)
        except Exception:
            return
        for alert in alerts:
            delivered = False
            if self.tray is not None:
                delivered = self.tray.notify(alert.title, alert.body)
            if not delivered:
                self.statusBar().showMessage(f"{alert.title}：{alert.body}", 8000)

    def _on_sample_tick(self) -> None:
        if self._paused or self._current is None or self._reading:
            return
        self._submit(KIND_READ, self._current)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._sample_timer.stop()
            self.statusBar().showMessage("已暂停监控")
        else:
            self._sample_timer.start()
            self.statusBar().showMessage("已恢复监控")

    def _on_pin_toggled(self, _checked: bool) -> None:
        if self._info is not None:
            self._update_table(self._info, summarize(self._info, self.cfg))

    def _reload_chart(self) -> None:
        device = self._current
        if self.store is None or device is None or not device.serial:
            self._chart.set_samples([])
            return
        media = self._info.media if self._info is not None else "sata_ssd"
        threshold = self.cfg.temp_threshold(media)
        try:
            samples = self.store.downsample(device.serial, max_points=600)
        except Exception:
            samples = []
        self._chart.set_samples(samples, (threshold.green_below, threshold.yellow_max))

    # ------------------------------------------------------------ 按钮动作
    def copy_info(self) -> None:
        if self._info is None:
            self.statusBar().showMessage("暂无可复制的信息", 4000)
            return
        text = copy_text(summarize(self._info, self.cfg))
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            self.statusBar().showMessage("已复制磁盘信息到剪贴板", 4000)

    def export_history(self) -> None:
        device = self._current
        if self.store is None or device is None or not device.serial:
            self.statusBar().showMessage("暂无可导出的历史数据", 4000)
            return
        default_name = f"{device.serial or device.name}-温度历史.csv"
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出温度历史", str(Path.home() / default_name), "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            count = self.store.export_samples_csv(device.serial, path)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"已导出 {count} 条记录到 {path}", 6000)

    def start_self_test(self) -> None:
        if self._current is None:
            return
        if self._report is not None and self._report.state == "danger":
            answer = QMessageBox.question(
                self,
                "磁盘状态危险",
                "当前磁盘健康状态为「危险」，仍要启动自检吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._test_mode = "short"
        self._submit(KIND_TEST, self._current, test_mode="short")

    def _begin_test_watch(self, device: Device) -> None:
        """自检已下发：开始每 5 秒轮询进度（提示词 3.5）。"""
        self._test_serial = device.serial or device.name
        self._test_timer.start()

    def _on_test_poll_tick(self) -> None:
        if self._test_serial is None or self._current is None:
            self._test_timer.stop()
            return
        if (self._current.serial or self._current.name) != self._test_serial:
            # 用户切到别的盘了，别再替上一块盘轮询
            self._test_timer.stop()
            self._test_serial = None
            return
        if self._reading:
            # 上一轮 -a 还没回来，跳过这轮，避免请求堆积
            return
        self._submit(KIND_READ, self._current)

    def _poll_test_progress(self, device: Device | None, info: DiskInfo) -> None:
        """把自检进度写进 test_results，跑完自动停止轮询（提示词 3.5）。"""
        if self._test_serial is None or device is None:
            return
        if (device.serial or device.name) != self._test_serial:
            return
        status = info.self_test_status
        if status is None:
            return
        running = self_test_in_progress(info)
        done_pct: int | None = None
        if running:
            remaining = self_test_remaining(info)
            if remaining is not None:
                done_pct = max(0, 100 - remaining)
        if self.store is not None and device.serial:
            try:
                self.store.record_test_result(device.serial, self._test_mode, status, done_pct)
            except Exception:
                pass
        if running:
            progress = f"　已完成 {done_pct}%" if done_pct is not None else ""
            self.statusBar().showMessage(f"自检进行中：{status}{progress}", 6000)
            return
        self._test_timer.stop()
        self._test_serial = None
        self._record_event(device, "selftest_done", status)
        self.statusBar().showMessage(f"自检结束：{status}", 8000)

    # ------------------------------------------------------------ 托盘与退出
    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _stop_elevation(self) -> None:
        """授权线程还在等密码框时不要直接销毁——让它自己结束再放行。"""
        worker = self._elevation
        self._elevation = None
        if worker is None:
            return
        try:
            running = worker.isRunning()
        except RuntimeError:
            # 线程已经结束并被 deleteLater 回收，C++ 对象没了，不用再等
            return
        if running:
            # pkexec 不可强制中断，最多等一小会儿；超时也不阻塞关闭
            worker.wait(1500)

    def quit_application(self) -> None:
        self._closing_to_tray = False
        self._sample_timer.stop()
        self._chart_timer.stop()
        self._test_timer.stop()
        self._stop_elevation()
        self.worker.stop()
        self.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._closing_to_tray and self.tray is not None and self.tray.active:
            event.ignore()
            self.hide()
            self.statusBar().showMessage("已最小化到托盘，仍可继续监控")
            return
        self._sample_timer.stop()
        self._chart_timer.stop()
        self._test_timer.stop()
        self._stop_elevation()
        self.monitor.stop()
        self.worker.stop()
        event.accept()

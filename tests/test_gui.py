"""GUI 层单元测试（offscreen，不访问真实磁盘）。

所有 smartctl / lsblk 调用都在 workers 模块里被 monkeypatch 掉，
因此本文件既不读裸盘也不依赖权限配置（对应验收 A9）。
"""

from __future__ import annotations

import pytest

from knowdiskdrive import devices as devices_mod
from knowdiskdrive import health as health_mod
from knowdiskdrive import history as history_mod
from knowdiskdrive import smart as smart_mod
from knowdiskdrive.config import DEFAULT_CONFIG
from knowdiskdrive.history import HistoryStore
from knowdiskdrive.smart import AtaAttribute, DiskInfo, SmartctlRun, parse_json_payload

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from knowdiskdrive.gui.app import create_application

    app = create_application(["pytest"])
    yield app


def make_device(name="sda", tran="sata", serial="SN123") -> devices_mod.Device:
    return devices_mod.Device(
        name=name,
        model="ZHITAI SC001 Active 512GB SSD",
        serial=serial,
        tran=tran,
        size="476.9G",
        size_bytes=512110190592,
        rota=False,
    )


def info_from(payload: dict, device="/dev/sda") -> DiskInfo:
    return parse_json_payload(payload, device, SmartctlRun(rc=0))


def attr(attr_id: int, name: str, value=100, worst=100, thresh=0, raw=0) -> AtaAttribute:
    return AtaAttribute(
        id=attr_id,
        name=name,
        value=value,
        worst=worst,
        thresh=thresh,
        raw=raw,
        raw_text=str(raw),
        when_failed=None,
    )


# --------------------------------------------------------------- 属性表模型
def test_attribute_model_headers_and_rows(qapp, ata_healthy_payload):
    from knowdiskdrive.gui.models import ATTR_HEADERS, AttributeTableModel

    model = AttributeTableModel()
    info = info_from(ata_healthy_payload)
    model.set_attributes(info.ata_attributes)
    assert model.columnCount() == len(ATTR_HEADERS) == 7
    assert model.rowCount() == len(info.ata_attributes)
    assert model.headerData(0, Qt.Horizontal) == "ID"
    assert model.headerData(6, Qt.Horizontal) == "状态"


def test_attribute_model_pins_key_ids_first(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    attrs = [attr(1, "读错误率"), attr(199, "CRC 错误"), attr(5, "重映射扇区"), attr(9, "通电时间")]
    model = AttributeTableModel()
    model.set_attributes(attrs, pin_first=True)
    ids = [model.data(model.index(row, 0)) for row in range(model.rowCount())]
    assert ids[:2] == ["5", "199"]
    assert ids[2:] == ["1", "9"]


def test_attribute_model_without_pin_is_ascending(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    attrs = [attr(199, "CRC 错误"), attr(5, "重映射扇区"), attr(9, "通电时间")]
    model = AttributeTableModel()
    model.set_attributes(attrs, pin_first=False)
    ids = [model.data(model.index(row, 0)) for row in range(model.rowCount())]
    assert ids == ["5", "9", "199"]


def test_attribute_model_status_text_and_color(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    attrs = [attr(5, "重映射扇区", value=10, worst=10, thresh=20, raw=8)]
    model = AttributeTableModel()
    model.set_attributes(attrs, is_sata=True)
    assert model.data(model.index(0, 6)) == "临界"
    background = model.data(model.index(0, 0), Qt.BackgroundRole)
    assert isinstance(background, QColor)


def test_attribute_model_placeholder_for_missing_numbers(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    attrs = [attr(190, "气流温度", value=None, worst=None, thresh=None)]
    model = AttributeTableModel()
    model.set_attributes(attrs)
    assert model.data(model.index(0, 2)) == "—"
    assert model.data(model.index(0, 4)) == "—"


def test_attribute_model_rows_text(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    model = AttributeTableModel()
    model.set_attributes([attr(5, "重映射扇区")])
    text = model.rows_text()[0]
    assert text.split("\t")[0] == "5"
    assert text.split("\t")[-1] == "正常"


def test_attribute_model_clear(qapp):
    from knowdiskdrive.gui.models import AttributeTableModel

    model = AttributeTableModel()
    model.set_attributes([attr(5, "x")])
    model.clear()
    assert model.rowCount() == 0


# ------------------------------------------------------------- NVMe 键值模型
def test_nvme_model_shows_chinese_keys(qapp, nvme_healthy_payload):
    from knowdiskdrive.gui.models import NvmeLogModel

    model = NvmeLogModel()
    model.set_info(info_from(nvme_healthy_payload, "/dev/nvme0n1"))
    assert model.rowCount() > 5
    keys = [model.data(model.index(row, 0)) for row in range(model.rowCount())]
    assert "寿命消耗（%）" in keys


def test_nvme_model_handles_missing_log(qapp):
    from knowdiskdrive.gui.models import NvmeLogModel

    model = NvmeLogModel()
    model.set_info(None)
    assert model.rowCount() == 0


def test_health_badge_text(qapp, ata_healthy_payload):
    from knowdiskdrive.gui.models import health_badge_text
    from knowdiskdrive.health import evaluate

    report = evaluate(info_from(ata_healthy_payload))
    assert health_badge_text(report) == "● 良好"


# ------------------------------------------------------------------- 温度图
def test_chart_accepts_samples_and_paints(qapp):
    from knowdiskdrive.gui.chart import TempChart
    from knowdiskdrive.history import Sample

    chart = TempChart()
    chart.resize(400, 200)
    samples = [
        Sample("2026-01-01T00:00:00Z", 30, "good", "active"),
        Sample("2026-01-01T01:00:00Z", 42, "good", "active"),
    ]
    chart.set_samples(samples)
    pixmap = QPixmap(400, 200)
    chart.render(pixmap)
    assert not pixmap.isNull()


def test_chart_handles_empty_and_none_temperature(qapp):
    from knowdiskdrive.gui.chart import TempChart
    from knowdiskdrive.history import Sample

    chart = TempChart()
    chart.resize(300, 150)
    chart.set_samples([])
    chart.render(QPixmap(300, 150))
    chart.set_samples([Sample("2026-01-01T00:00:00Z", None, "unknown", "standby")])
    chart.render(QPixmap(300, 150))
    chart.set_placeholder("无数据")
    assert chart.samples()[-1].temp_c is None


# --------------------------------------------------------------------- 托盘
def test_tray_unavailable_in_offscreen(qapp):
    from knowdiskdrive.gui.tray import TrayController

    tray = TrayController()
    assert TrayController.is_available() is False
    assert tray.build() is False
    assert tray.active is False
    assert tray.notify("标题", "正文") is False


def test_tray_update_tooltip_is_noop_without_tray(qapp):
    from knowdiskdrive.gui.tray import TrayController

    tray = TrayController()
    tray.update_tooltip("型号", "good", 40)
    tray.set_paused(True)


def test_temp_icon_renders(qapp):
    from knowdiskdrive.gui.tray import health_icon, temp_icon

    icon = temp_icon("red")
    assert not icon.isNull()
    assert not health_icon("warning").isNull()


# ------------------------------------------------------------- 后台任务执行
def test_execute_scan_returns_devices(monkeypatch, qapp):
    from knowdiskdrive.gui import workers

    monkeypatch.setattr(workers, "enumerate_devices", lambda: [make_device()])
    result = workers.execute_request(workers.WorkRequest(kind=workers.KIND_SCAN, cfg=DEFAULT_CONFIG))
    assert result.error is None
    assert [d.name for d in result.devices] == ["sda"]


def test_execute_scan_reports_enumeration_error(monkeypatch, qapp):
    from knowdiskdrive.gui import workers

    def boom():
        raise devices_mod.DeviceEnumerationError("lsblk 超时")

    monkeypatch.setattr(workers, "enumerate_devices", boom)
    result = workers.execute_request(workers.WorkRequest(kind=workers.KIND_SCAN, cfg=DEFAULT_CONFIG))
    assert "lsblk 超时" in result.error


def test_execute_read_uses_auto_with_usb_hint(monkeypatch, qapp, ata_healthy_payload):
    from knowdiskdrive.gui import workers

    calls = {}

    def fake_read(device, cfg, cached_d_arg=None, allowed_devices=None, is_usb=None):
        calls["args"] = (device, cached_d_arg, is_usb)
        return info_from(ata_healthy_payload, device)

    monkeypatch.setattr(workers, "read_smart_auto", fake_read)
    device = make_device(tran="usb")
    result = workers.execute_request(
        workers.WorkRequest(kind=workers.KIND_READ, cfg=DEFAULT_CONFIG, device=device, cached_d_arg="sat")
    )
    assert result.error is None
    assert result.info is not None and result.report is not None
    assert calls["args"] == ("/dev/sda", "sat", True)


def test_execute_read_without_device_errors(qapp):
    from knowdiskdrive.gui import workers

    result = workers.execute_request(workers.WorkRequest(kind=workers.KIND_READ, cfg=DEFAULT_CONFIG))
    assert result.error == "缺少目标设备"


def test_execute_test_success_and_failure(monkeypatch, qapp):
    from knowdiskdrive.gui import workers

    monkeypatch.setattr(workers, "start_self_test", lambda *a, **k: SmartctlRun(rc=0))
    ok = workers.execute_request(
        workers.WorkRequest(kind=workers.KIND_TEST, cfg=DEFAULT_CONFIG, device=make_device(), test_mode="short")
    )
    assert ok.error is None and ok.message == "已启动自检"

    monkeypatch.setattr(workers, "start_self_test", lambda *a, **k: SmartctlRun(rc=1, stderr="self-test failed"))
    bad = workers.execute_request(
        workers.WorkRequest(kind=workers.KIND_TEST, cfg=DEFAULT_CONFIG, device=make_device(), test_mode="long")
    )
    assert bad.error == "self-test failed"


def test_execute_test_timeout_message(monkeypatch, qapp):
    from knowdiskdrive.gui import workers

    monkeypatch.setattr(workers, "start_self_test", lambda *a, **k: SmartctlRun(rc=0, timed_out=True))
    result = workers.execute_request(
        workers.WorkRequest(kind=workers.KIND_TEST, cfg=DEFAULT_CONFIG, device=make_device())
    )
    assert "超时" in result.error


def test_execute_request_wraps_unexpected_exception(monkeypatch, qapp):
    from knowdiskdrive.gui import workers

    def boom(*args, **kwargs):
        raise RuntimeError("磁盘不见了")

    monkeypatch.setattr(workers, "read_smart_auto", boom)
    result = workers.execute_request(
        workers.WorkRequest(kind=workers.KIND_READ, cfg=DEFAULT_CONFIG, device=make_device())
    )
    assert "磁盘不见了" in result.error


# ------------------------------------------------------------------- 主窗口
@pytest.fixture()
def window(monkeypatch, qapp, tmp_path):
    from knowdiskdrive.gui import workers
    from knowdiskdrive.gui.main_window import MainWindow

    monkeypatch.setattr(workers, "enumerate_devices", lambda: [make_device()])
    monkeypatch.setattr(
        workers,
        "read_smart_auto",
        lambda device, cfg, **kwargs: smart_mod.parse_json_payload(
            {"model_name": "TEST", "serial_number": "SN123", "smart_status": {"passed": True}},
            device,
            SmartctlRun(rc=0),
        ),
    )
    store = HistoryStore(tmp_path / "h.db")
    win = MainWindow(cfg=DEFAULT_CONFIG, store=store, tray=None)
    yield win
    win.worker.stop()
    win.close()
    store.close()


def test_main_window_builds_widgets(window):
    assert window.windowTitle().startswith("KnowDiskDrive")
    assert window.badge_label.text().startswith("●")
    assert window.table.model() is window._attr_model
    assert window.pin_checkbox.isChecked() is True
    assert window.test_button.text() == "启动短自检"


def test_main_window_applies_devices(window):
    devices = [make_device()]
    window._apply_devices(devices)
    assert window.device_list.count() == 1
    assert window.device_list.item(0).text().splitlines()[0] == "sda"


def test_main_window_shows_empty_state(window):
    window._apply_devices([])
    assert window.device_list.count() == 0
    assert window._banner.isVisibleTo(window) is True
    assert "没有检测到" in window.banner_label.text()


def test_main_window_header_text_format(window):
    text = window._headline_text(make_device())
    assert text.startswith("ZHITAI SC001 Active 512GB SSD")
    assert "SN: SN123" in text
    assert "476.9 GB" in text
    assert "SATA" in text
    assert text.endswith("│")


def test_main_window_apply_read_updates_ui(window, ata_healthy_payload, tmp_path):
    from knowdiskdrive.gui.workers import KIND_READ, WorkResult
    from knowdiskdrive.health import evaluate

    device = make_device()
    window._current = device
    window._apply_devices([device])
    info = info_from(ata_healthy_payload)
    result = WorkResult(
        kind=KIND_READ, device=device, info=info, report=evaluate(info, DEFAULT_CONFIG)
    )
    window._apply_read(result)
    assert "良好" in window.badge_label.text()
    assert window._attr_model.rowCount() == len(info.ata_attributes)
    assert window._banner.isVisibleTo(window) is False
    assert window.statusBar().currentMessage().startswith("最后刷新")
    assert window.store.count("temp_samples") >= 1


def test_main_window_nvme_uses_key_value_table(window, nvme_healthy_payload):
    from knowdiskdrive.gui.workers import KIND_READ, WorkResult
    from knowdiskdrive.health import evaluate

    device = make_device(name="nvme0n1", tran="nvme", serial="NVMeSN")
    window._current = device
    info = info_from(nvme_healthy_payload, "/dev/nvme0n1")
    window._on_result(
        WorkResult(kind=KIND_READ, device=device, info=info, report=evaluate(info, DEFAULT_CONFIG))
    )
    assert window.table.model() is window._nvme_model
    assert window._nvme_model.rowCount() > 5


@pytest.mark.parametrize(
    "status,expected",
    [
        (smart_mod.STATUS_PERMISSION, "一次性授权"),
        (smart_mod.STATUS_NOT_INSTALLED, "smartmontools"),
        (smart_mod.STATUS_NO_SMART, "不支持 SMART"),
        (smart_mod.STATUS_STANDBY, "休眠中"),
        (smart_mod.STATUS_TIMEOUT, "读取超时"),
        (smart_mod.STATUS_USB_BRIDGE_FAILED, "已尝试"),
    ],
)
def test_main_window_failure_banners(window, status, expected):
    info = DiskInfo(device="/dev/sda", status=status)
    info.usb_d_tried = ["(默认)", "-d sat"]
    window.test_button.setEnabled(True)
    window._update_failure_state(info, make_device())
    assert window._banner.isVisibleTo(window) is True
    assert expected in window.banner_label.text()


def test_main_window_ok_hides_banner(window):
    window._show_banner("旧提示", error=True)
    window._update_failure_state(DiskInfo(device="/dev/sda", status=smart_mod.STATUS_OK), make_device())
    assert window._banner.isVisibleTo(window) is False


def test_main_window_permission_banner_offers_one_click_grant(window):
    """权限不足时按钮是「一键授权」，不再是让用户去命令行找 install.sh。"""
    info = DiskInfo(device="/dev/sda", status=smart_mod.STATUS_PERMISSION)
    window._update_failure_state(info, make_device())
    assert window.banner_button.isVisibleTo(window) is True
    assert window.banner_button.text() == "一键授权"
    assert window._banner_action == window.grant_access


def test_main_window_timeout_banner_has_retry(window):
    info = DiskInfo(device="/dev/sda", status=smart_mod.STATUS_TIMEOUT)
    window._update_failure_state(info, make_device())
    assert window.banner_button.text() == "重试"


def test_main_window_records_device_gone_event(window, monkeypatch):
    from knowdiskdrive.gui import main_window as mw

    monkeypatch.setattr(mw.QTimer, "singleShot", staticmethod(lambda *a, **k: None))
    device = make_device()
    window._current = device
    info = DiskInfo(device="/dev/sda", status=smart_mod.STATUS_DEVICE_GONE)
    window._update_failure_state(info, device)
    assert "已拔出" in window.banner_label.text()
    assert window.store.count("events") == 1


def test_main_window_pause_stops_timer(window):
    window.set_paused(True)
    assert window._sample_timer.isActive() is False
    window.set_paused(False)
    assert window._sample_timer.isActive() is True


def test_main_window_copy_info_without_data(window):
    window._info = None
    window.copy_info()
    assert "暂无可复制" in window.statusBar().currentMessage()


def test_main_window_copy_info_with_data(window, ata_healthy_payload):
    info = info_from(ata_healthy_payload)
    window._info = info
    window.copy_info()
    text = QApplication.clipboard().text()
    assert text.startswith("型号：")
    assert "健康：" in text


def test_main_window_export_without_device(window):
    window._current = None
    window.export_history()
    assert "暂无可导出" in window.statusBar().currentMessage()


def test_main_window_export_writes_csv(window, ata_healthy_payload, tmp_path, monkeypatch):
    from knowdiskdrive.gui import main_window as mw

    device = make_device()
    window._current = device
    window.store.record_sample("SN123", 42, "good", "ok")
    target = tmp_path / "out.csv"
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV"))
    )
    window.export_history()
    assert target.exists()
    assert "已导出" in window.statusBar().currentMessage()


def test_main_window_export_cancelled(window, monkeypatch):
    from knowdiskdrive.gui import main_window as mw

    window._current = make_device()
    monkeypatch.setattr(
        mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    window.export_history()


def test_main_window_alerts_fall_back_to_status_bar(window, ata_failing_payload):
    from knowdiskdrive.alerts import AlertEngine
    from knowdiskdrive.health import evaluate

    window.alert_engine = AlertEngine(store=window.store, dedup_minutes=30)
    device = make_device()
    window._current = device
    healthy = info_from(ata_failing_payload)
    window._fire_alerts(device, healthy, evaluate(healthy, DEFAULT_CONFIG))
    assert window.statusBar().currentMessage() != ""


def test_main_window_self_test_submits_request(window, monkeypatch):
    from knowdiskdrive.gui import workers

    seen = {}

    def fake_submit(self, request):
        seen["kind"] = request.kind
        seen["mode"] = request.test_mode
        return True

    monkeypatch.setattr(workers.SmartWorker, "submit", fake_submit)
    window._current = make_device()
    window.start_self_test()
    assert seen["kind"] == workers.KIND_TEST
    assert seen["mode"] == "short"


def test_main_window_close_stops_timers(window):
    window.close()
    assert window._sample_timer.isActive() is False


# ------------------------------------------------------------ 热插拔（A6）
def test_hotplug_events_are_debounced(window, monkeypatch):
    """udev 一次插拔连发多条事件，只应触发一次扫描。"""
    calls = []
    monkeypatch.setattr(window, "refresh_devices", lambda: calls.append(1))

    window._on_devices_changed("add /dev/sdb")
    window._on_devices_changed("add /dev/sdb")
    window._on_devices_changed("change /dev/sdb")
    assert calls == []  # 还没到去抖窗口

    window._flush_hotplug()
    assert calls == [1]

    window._flush_hotplug()  # 没有新事件时不应重复刷新
    assert calls == [1]


def test_hotplug_reports_event_to_status_bar(window):
    window._on_devices_changed("remove /dev/sdb")
    assert "设备变化" in window.statusBar().currentMessage()
    window._hotplug_pending = False


def test_window_has_monitor_and_stops_it(window):
    from knowdiskdrive.gui.main_window import HOTPLUG_DEBOUNCE_MS

    assert 0 < HOTPLUG_DEBOUNCE_MS < 2000  # 必须远小于验收要求的 2 秒
    assert window.monitor is not None


def test_remember_usb_d_arg_persists_probe_result(window):
    """提示词 3.3：探测成功的 -d 值必须按序列号缓存下来。"""
    device = make_device(tran="usb", serial="USBSN1")
    info = DiskInfo(device="/dev/sda", status=smart_mod.STATUS_OK)
    info.usb_d_arg = "usbmicro"
    window._remember_usb_d_arg(device, info)
    assert window.store.get_pref("USBSN1") == "usbmicro"
    # 同一路径再次缓存不同值时按最新一次覆盖（换桥接芯片/换线后的实际情况）
    info.usb_d_arg = "sat"
    window._remember_usb_d_arg(device, info)
    assert window.store.get_pref("USBSN1") == "sat"


def test_remember_usb_d_arg_skips_non_usb_and_missing_serial(window):
    device = make_device(tran="sata", serial="SN123")
    info = DiskInfo(device="/dev/sda", status=smart_mod.STATUS_OK)
    window._remember_usb_d_arg(device, info)
    assert window.store.get_pref("SN123") is None
    # 序列号缺失时不写（否则会串盘）
    info.usb_d_arg = "sat"
    window._remember_usb_d_arg(make_device(serial=""), info)
    assert window.store.get_pref("") is None


def test_header_flags_text_parse_mode(window):
    """提示词 3.2：文本降级必须在界面上标出来。"""
    payload = {
        "model_name": "TEST",
        "serial_number": "SN123",
        "smart_status": {"passed": True},
        "device": {"protocol": "ATA"},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", SmartctlRun(rc=0))
    window._apply_devices([make_device()])
    window._current = make_device()
    summary = health_mod.summarize(info, DEFAULT_CONFIG)
    window._update_header(summary, info)
    assert "兼容模式" not in window.subline_label.text()
    # 换成文本模式（模拟 -j 不可用时的降级）
    text = (
        "Device Model:     TEST\n"
        "SMART overall-health self-assessment test result: PASSED\n"
    )
    text_info = smart_mod.parse_text_payload(text, "/dev/sda", SmartctlRun(rc=0))
    window._update_header(health_mod.summarize(text_info, DEFAULT_CONFIG), text_info)
    assert "兼容模式解析" in window.subline_label.text()


def test_attr_header_alignment_matches_cells(qapp):
    from knowdiskdrive.gui.models import ATTR_HEADERS, AttributeTableModel

    model = AttributeTableModel()
    right = {2, 3, 4}  # 当前值 / 最差值 / 阈值
    for column in range(len(ATTR_HEADERS)):
        align = model.headerData(column, Qt.Horizontal, Qt.TextAlignmentRole)
        assert align is not None, f"表头 {ATTR_HEADERS[column]} 没给对齐方式"
        is_right = bool(align & int(Qt.AlignRight))
        assert is_right is (column in right), ATTR_HEADERS[column]


def test_device_items_are_three_lines_tall(window):
    """设备项是三行文本，委托必须按三行给高度（否则三行挤在一起）。"""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QStyleOptionViewItem

    devices = [make_device("sda"), make_device("sdb", serial="SN456")]
    window._apply_devices(devices)
    delegate = window.device_list.itemDelegate()
    option = QStyleOptionViewItem()
    option.font = window.device_list.font()
    option.fontMetrics = QFontMetrics(option.font)
    model = window.device_list.model()
    for row in range(window.device_list.count()):
        text = window.device_list.item(row).text()
        assert text.count(chr(10)) == 2  # 三行文本
        height = delegate.sizeHint(option, model.index(row, 0)).height()
        assert height >= option.fontMetrics.lineSpacing() * 3


def test_application_loads_chinese_translation(qapp):
    """Qt 自带翻译必须装上，否则 QMessageBox 会显示 Yes / No。"""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox()
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    assert "是" in box.button(QMessageBox.Yes).text()
    assert "否" in box.button(QMessageBox.No).text()

# ------------------------------------------------------- 自检进度轮询（3.5）
def test_begin_test_watch_arms_poll_timer(window):
    window._begin_test_watch(make_device())
    assert window._test_serial == "SN123"
    assert window._test_timer.isActive() is True
    assert window._test_timer.interval() == 5000
    window._test_timer.stop()


def test_poll_test_progress_records_and_stops(window):
    from knowdiskdrive.gui.workers import KIND_READ, WorkResult
    from knowdiskdrive.health import evaluate

    window._test_serial = "SN123"
    window._test_mode = "short"
    window._test_timer.start()
    device = make_device()

    running = info_from(
        {
            "model_name": "TEST",
            "serial_number": "SN123",
            "smart_status": {"passed": True},
            "ata_smart_data": {
                "self_test": {
                    "status": {
                        "value": 244,
                        "string": "in progress, 40% remaining",
                        "remaining_percent": 40,
                    }
                }
            },
        }
    )
    window._apply_read(
        WorkResult(kind=KIND_READ, device=device, info=running, report=evaluate(running, DEFAULT_CONFIG))
    )
    rows = window.store.test_results("SN123")
    assert rows and rows[0].status == "in progress, 40% remaining"
    assert rows[0].progress_pct == 60
    assert window._test_timer.isActive() is True

    done = info_from(
        {
            "model_name": "TEST",
            "serial_number": "SN123",
            "smart_status": {"passed": True},
            "ata_smart_data": {
                "self_test": {
                    "status": {
                        "value": 0,
                        "string": "The previous self-test routine completed without error",
                    }
                }
            },
        }
    )
    window._apply_read(
        WorkResult(kind=KIND_READ, device=device, info=done, report=evaluate(done, DEFAULT_CONFIG))
    )
    assert window._test_timer.isActive() is False
    assert window._test_serial is None
    assert any(r.status.startswith("The previous") for r in window.store.test_results("SN123"))

# ------------------------------------------------- 浅色调色板（深色主题加固）
def test_stop_elevation_survives_deleted_worker(window):
    """线程已被 deleteLater 回收时，关闭窗口不能再抛 RuntimeError。"""

    class Dead:
        def isRunning(self):
            raise RuntimeError("Internal C++ object already deleted")

    window._elevation = Dead()
    window._stop_elevation()
    assert window._elevation is None


def test_stylesheet_covers_item_views_and_menus():
    """样式表要覆盖 item view 的选中色与菜单，缺一条就会落回深色调色板。"""
    from knowdiskdrive.gui import theme

    css = theme.STYLESHEET
    for selector in ("QAbstractItemView", "QListWidget::item:selected", "QMenu", "QToolTip"):
        assert selector in css, selector
    assert "selection-background-color: #dbeafe" in css


def test_build_palette_matches_light_stylesheet():
    from knowdiskdrive.gui import theme

    palette = theme.build_palette()
    C = palette.ColorRole
    assert palette.color(C.Base).name() == "#ffffff"
    assert palette.color(C.AlternateBase).name() == "#fafafa"
    assert palette.color(C.Window).name() == theme.COLOR_BG
    assert palette.color(C.Text).name() == theme.COLOR_TEXT
    assert palette.color(C.WindowText).name() == theme.COLOR_TEXT
    assert palette.color(C.ButtonText).name() == theme.COLOR_TEXT
    assert palette.color(C.Highlight).name() == "#dbeafe"
    assert palette.color(C.HighlightedText).name() == theme.COLOR_TEXT
    assert palette.color(C.ToolTipBase).name() == "#ffffff"


def test_build_palette_disables_use_muted_text_but_light_base():
    from knowdiskdrive.gui import theme

    palette = theme.build_palette()
    C, G = palette.ColorRole, palette.ColorGroup
    assert palette.color(G.Disabled, C.Text).name() == theme.COLOR_MUTED
    assert palette.color(G.Disabled, C.Base).name() == "#ffffff"


def _dark_palette():
    from PySide6.QtGui import QColor, QPalette

    dark = QPalette()
    for role, value in (
        (QPalette.ColorRole.Window, "#26292e"),
        (QPalette.ColorRole.Base, "#26292e"),
        (QPalette.ColorRole.Text, "#fcfcfc"),
        (QPalette.ColorRole.WindowText, "#fcfcfc"),
        (QPalette.ColorRole.Highlight, "#ff6376"),
    ):
        dark.setColor(role, QColor(value))
    return dark


def test_apply_light_palette_overrides_dark_app_palette(qapp):
    from knowdiskdrive.gui import theme

    original = qapp.palette()
    try:
        qapp.setPalette(_dark_palette())
        assert qapp.palette().color(qapp.palette().ColorRole.Base).name() == "#26292e"
        theme.apply_light_palette(qapp)
        C = qapp.palette().ColorRole
        assert qapp.palette().color(C.Base).name() == "#ffffff"
        assert qapp.palette().color(C.Text).name() == theme.COLOR_TEXT
        assert qapp.palette().color(C.Highlight).name() == "#dbeafe"
    finally:
        qapp.setPalette(original)


def test_apply_light_palette_tolerates_stub():
    from knowdiskdrive.gui import theme

    class Stub:
        pass

    theme.apply_light_palette(Stub())  # 没有 setPalette 也不能抛

    class Boom:
        def setPalette(self, palette):
            raise RuntimeError("boom")

    theme.apply_light_palette(Boom())


def test_application_uses_light_palette(qapp):
    from knowdiskdrive.gui import theme

    C = qapp.palette().ColorRole
    assert qapp.palette().color(C.Base).name() == "#ffffff"
    assert qapp.palette().color(C.Text).name() == theme.COLOR_TEXT


def test_device_list_viewport_uses_light_base(window):
    """深色系统主题下左侧列表曾整片发黑（用户截图实测 (38, 41, 47)）。

    量的是 viewport：它才是列表真正绘制背景的控件，样式表只画到
    QListWidget 自身，viewport 靠应用调色板兜底。
    """
    lw = window.device_list
    assert lw.viewport().palette().color(lw.palette().ColorRole.Base).name() == "#ffffff"
    assert lw.palette().color(lw.palette().ColorRole.Base).name() == "#ffffff"


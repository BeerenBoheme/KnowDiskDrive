"""USB 桥接探测链与自检接口（提示词 3.3 / 3.5）。"""

from __future__ import annotations

import pytest

from knowdiskdrive import smart as smart_mod
from knowdiskdrive.config import DEFAULT_CONFIG

from conftest import make_info


def _stub_read(mapping, calls):
    """伪造 read_smart：按 -d 值返回预置结果，并记录探测顺序。"""

    def fake(device, cfg=DEFAULT_CONFIG, d_arg=None, allowed_devices=None, use_json=True):
        calls.append(d_arg)
        result = mapping.get(d_arg)
        if result is None:
            return smart_mod.DiskInfo(device=device, status=smart_mod.STATUS_ERROR)
        return make_info(result, device) if isinstance(result, dict) else result

    return fake


def test_first_attempt_succeeds(monkeypatch, ata_healthy_payload):
    calls: list = []
    monkeypatch.setattr(smart_mod, "read_smart", _stub_read({None: ata_healthy_payload}, calls))
    info = smart_mod.read_smart_auto("/dev/sda", DEFAULT_CONFIG)
    assert info.status == smart_mod.STATUS_OK
    assert calls == [None]
    assert info.usb_d_tried == ["(默认)"]


def test_cached_d_arg_is_reused_first(monkeypatch, nvme_healthy_payload):
    calls: list = []
    monkeypatch.setattr(smart_mod, "read_smart", _stub_read({"sat": nvme_healthy_payload}, calls))
    info = smart_mod.read_smart_auto("/dev/sdc", DEFAULT_CONFIG, cached_d_arg="sat")
    assert info.status == smart_mod.STATUS_OK
    assert calls == ["sat"]


def test_permission_short_circuits(monkeypatch):
    """权限不足时不要再去试 USB 桥接，避免无意义地连开设备。"""
    denied = smart_mod.DiskInfo(device="/dev/sda", status=smart_mod.STATUS_PERMISSION)
    calls: list = []
    monkeypatch.setattr(smart_mod, "read_smart", _stub_read({None: denied}, calls))
    info = smart_mod.read_smart_auto("/dev/sda", DEFAULT_CONFIG)
    assert info.status == smart_mod.STATUS_PERMISSION
    assert calls == [None]


def test_usb_probe_chain_order(monkeypatch):
    """默认失败 → -d sat → -d usbjmicron → -d usbsunplus，顺序必须与配置一致。"""
    calls: list = []
    monkeypatch.setattr(smart_mod, "read_smart", _stub_read({}, calls))
    info = smart_mod.read_smart_auto("/dev/sdc", DEFAULT_CONFIG, is_usb=True)
    assert calls == [None, "sat", "usbjmicron", "usbsunplus"]
    assert info.status == smart_mod.STATUS_USB_BRIDGE_FAILED
    assert "已尝试" in (info.detail or "")


def test_usb_probe_stops_on_success(monkeypatch, nvme_healthy_payload):
    calls: list = []
    monkeypatch.setattr(
        smart_mod, "read_smart", _stub_read({"usbjmicron": nvme_healthy_payload}, calls)
    )
    info = smart_mod.read_smart_auto("/dev/sdc", DEFAULT_CONFIG, is_usb=True)
    assert info.status == smart_mod.STATUS_OK
    assert calls == [None, "sat", "usbjmicron"]


def test_non_usb_device_skips_bridge_probe(monkeypatch):
    calls: list = []
    monkeypatch.setattr(smart_mod, "read_smart", _stub_read({}, calls))
    info = smart_mod.read_smart_auto("/dev/sda", DEFAULT_CONFIG, is_usb=False)
    assert calls == [None]
    assert info.status == smart_mod.STATUS_ERROR


def test_start_self_test_accepts_short_and_long(monkeypatch, fake_devices):
    seen: list = []

    def fake_run(device, args, cfg=DEFAULT_CONFIG, allowed_devices=None):
        seen.append(list(args))
        return smart_mod.SmartctlRun(rc=0, stdout="{}")

    monkeypatch.setattr(smart_mod, "run_smartctl", fake_run)
    smart_mod.start_self_test("/dev/sda", "short", DEFAULT_CONFIG, fake_devices)
    smart_mod.start_self_test("/dev/sda", "long", DEFAULT_CONFIG, fake_devices)
    assert seen == [["-t", "short"], ["-t", "long"]]


def test_start_self_test_rejects_other_modes(fake_devices):
    with pytest.raises(ValueError):
        smart_mod.start_self_test("/dev/sda", "offline", DEFAULT_CONFIG, fake_devices)
    with pytest.raises(ValueError):
        smart_mod.start_self_test("/dev/sda", "conveyance", DEFAULT_CONFIG, fake_devices)


def test_collect_nvme_rows_is_chinese(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    rows = smart_mod.collect_nvme_rows(info.nvme_log)
    labels = dict(rows)
    assert "严重警告" in labels
    assert "寿命消耗（%）" in labels
    assert labels["寿命消耗（%）"] == "14"
    # 提示词要求中文界面：键名必须是中文
    assert all(any("\u4e00" <= ch <= "\u9fff" for ch in label) for label, _ in rows)


def test_collect_nvme_rows_empty():
    assert smart_mod.collect_nvme_rows(None) == []
    assert smart_mod.collect_nvme_rows({}) == []


def test_nvme_data_units_use_1000x512_not_512():
    """NVMe 的 data unit 是 1000 个 512 字节；按 512 字节算会小 1000 倍。"""
    log = {
        "data_units_read": 124041849,
        "data_units_written": 84330400,
    }
    labels = dict(smart_mod.collect_nvme_rows(log))
    assert labels["累计读取"] == "63.5 TB"
    assert labels["累计写入"] == "43.2 TB"


def test_format_data_units_edge_cases():
    assert smart_mod.format_data_units(None) == "—"
    assert smart_mod.format_data_units(0) == "0"
    # 1 个单位 = 512000 字节 = 512 KB（十进制）
    assert smart_mod.format_data_units(1) == "512.0 KB"
    assert smart_mod.format_data_units(2) == "1.0 MB"

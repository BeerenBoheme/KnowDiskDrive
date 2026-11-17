"""smartctl JSON / 文本解析与失败归类（提示词第 5 节）。"""

from __future__ import annotations

import json

import pytest

from knowdiskdrive import smart as smart_mod
from knowdiskdrive.config import DEFAULT_CONFIG

from conftest import load_fixture, make_info


def test_parse_ata_healthy(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    assert info.status == smart_mod.STATUS_OK
    assert info.model == "ZHITAI SC001 Active 512GB SSD"
    assert info.serial == ata_healthy_payload["serial_number"]
    assert info.firmware == ata_healthy_payload["firmware_version"]
    assert info.capacity_bytes == 512110190592
    assert info.temperature_c == 33
    assert info.power_on_hours == ata_healthy_payload["power_on_time"]["hours"]
    assert info.smart_available is True
    assert info.smart_passed is True
    assert info.rotation_rate == 0  # SSD
    assert len(info.ata_attributes) == 30
    assert info.nvme_log is None


def test_parse_nvme_healthy(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    assert info.status == smart_mod.STATUS_OK
    assert info.is_nvme is True
    assert info.model == "KBG40ZNV512G KIOXIA"
    assert info.ata_attributes == []
    log = info.nvme_log
    assert log["critical_warning"] == 0
    assert log["available_spare"] == 100
    assert log["percentage_used"] == 14
    assert log["media_errors"] == 0
    assert info.power_on_hours == 7756


def test_parse_ata_carries_raw_value(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    attr_9 = next(a for a in info.ata_attributes if a.id == 9)
    raw_9 = next(
        t["raw"]["value"]
        for t in ata_healthy_payload["ata_smart_attributes"]["table"]
        if t["id"] == 9
    )
    assert attr_9.raw == raw_9
    assert attr_9.value == 100
    assert attr_9.name == "Power_On_Hours"


def test_permission_denied_maps_to_permission():
    """真实抓取样本：exit_status=2 + Permission denied。"""
    payload = load_fixture("ata_permission_denied.json")
    run = smart_mod.SmartctlRun(rc=2)
    info = smart_mod.parse_json_payload(payload, "/dev/sda", run)
    assert info.status == smart_mod.STATUS_PERMISSION
    # 提示词 8：失败态必须给用户可执行的中文提示，不能只丢原始英文
    assert "一次性授权" in info.status_text
    assert info.ata_attributes == []
    assert info.smart_passed is None


def test_permission_denied_nvme_fixture():
    payload = load_fixture("nvme_permission_denied.json")
    run = smart_mod.SmartctlRun(rc=2)
    info = smart_mod.parse_json_payload(payload, "/dev/nvme0n1", run)
    assert info.status == smart_mod.STATUS_PERMISSION


def test_timeout_maps_to_timeout():
    run = smart_mod.SmartctlRun(rc=None, timed_out=True)
    info = smart_mod.parse_json_payload({}, "/dev/sda", run)
    assert info.status == smart_mod.STATUS_TIMEOUT


def test_missing_smartctl_maps_to_not_installed():
    run = smart_mod.SmartctlRun(rc=None, error_kind=smart_mod.STATUS_NOT_INSTALLED)
    info = smart_mod.parse_json_payload({}, "/dev/sda", run)
    assert info.status == smart_mod.STATUS_NOT_INSTALLED


def test_standby_detected():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sda", "type": "scsi", "protocol": "ATA"},
        "power_mode": "STANDBY",
        "smart_support": {"available": True, "enabled": True},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.status == smart_mod.STATUS_STANDBY
    assert info.device_state == "standby"


def test_no_smart_support():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sdc", "type": "sat", "protocol": "ATA"},
        "smart_support": {"available": False, "enabled": False},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sdc", smart_mod.SmartctlRun(rc=0))
    assert info.status in (smart_mod.STATUS_NO_SMART, smart_mod.STATUS_OK)


def test_parse_text_fallback():
    text = (
        "smartctl 7.5 2025-09-01 r5714 [x86_64-linux-7.0.0-43-generic]\n"
        "=== START OF INFORMATION SECTION ===\n"
        "Device Model:     SAMSUNG HD103SJ\n"
        "Serial Number:    S246J9AZB12345\n"
        "Firmware Version: 1AJ10001\n"
        "User Capacity:    1,000,204,886,016 bytes [1.00 TB]\n"
        "Rotation Rate:    7200 rpm\n"
        "SMART overall-health self-assessment test result: PASSED\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/sdb", smart_mod.SmartctlRun(rc=0))
    assert info.model == "SAMSUNG HD103SJ"
    assert info.serial == "S246J9AZB12345"
    assert info.rotation_rate == 7200
    assert info.smart_passed is True
    assert info.parse_mode == smart_mod.PARSE_MODE_TEXT


def test_text_fallback_parses_ata_attribute_table():
    """文本降级也要能读出属性行，否则文本模式会「没有依据地显示良好」。"""
    text = (
        "=== START OF INFORMATION SECTION ===\n"
        "Device Model:     ZHITAI SC001 Active 512GB SSD\n"
        "SATA Version is:  SATA 3.2, 6.0 Gb/s (current: 6.0 Gb/s)\n"
        "=== START OF READ SMART DATA SECTION ===\n"
        "SMART overall-health self-assessment test result: PASSED\n"
        "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE\n"
        "  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0\n"
        "  9 Power_On_Hours          0x0012   100   100   000    Old_age   Always       -       7734\n"
        " 12 Power_Cycle_Count       0x0012   100   100   000    Old_age   Always       -       6055\n"
        "190 Airflow_Temperature_Cel 0x0022   033   033   000    Old_age   Always       -       33 (Min/Max 30/42)\n"
        "197 Current_Pending_Sector  0x0032   100   100   000    Old_age   Always       -       8\n"
        "199 UDMA_CRC_Error_Count    0x0032   200   200   000    Old_age   Always   FAILING_NOW  12\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/sdb", smart_mod.SmartctlRun(rc=0))
    assert info.protocol == "ata"
    assert [a.id for a in info.ata_attributes] == [5, 9, 12, 190, 197, 199]
    attr = {a.id: a for a in info.ata_attributes}
    assert attr[5].value == 100 and attr[5].worst == 100 and attr[5].thresh == 10
    assert attr[5].when_failed is None
    assert attr[190].raw == 33
    assert attr[190].raw_text == "33 (Min/Max 30/42)"
    assert attr[190].raw_display() == "33 (Min/Max 30/42)"
    assert attr[199].when_failed == "FAILING_NOW"
    # 文本输出没有独立的温度/通电行，这三项要从属性表里回填
    assert info.power_on_hours == 7734
    assert info.power_cycle_count == 6055
    assert info.temperature_c == 33


def test_text_fallback_parses_nvme_log():
    text = (
        "Model Number:                       KBG40ZNV512G KIOXIA\n"
        "Serial Number:                      41OPHEXAMPLE1\n"
        "=== START OF SMART DATA SECTION ===\n"
        "SMART overall-health self-assessment test result: PASSED\n"
        "Critical Warning:                   0x00\n"
        "Temperature:                        32 Celsius\n"
        "Available Spare:                    100%\n"
        "Available Spare Threshold:          10%\n"
        "Percentage Used:                    14%\n"
        "Data Units Read:                    124,042,141 [63.5 TB]\n"
        "Data Units Written:                 84,330,400 [43.2 TB]\n"
        "Power Cycles:                       6,912\n"
        "Power On Hours:                     7,982\n"
        "Unsafe Shutdowns:                   220\n"
        "Media and Data Integrity Errors:    0\n"
        "Number of Error Information Log Entries: 1\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/nvme0", smart_mod.SmartctlRun(rc=0))
    assert info.protocol == "nvme"
    log = info.nvme_log or {}
    assert log["critical_warning"] == 0
    assert log["temperature"] == 32
    assert log["available_spare"] == 100
    assert log["percentage_used"] == 14
    assert log["data_units_read"] == 124042141
    assert log["power_on_hours"] == 7982
    assert log["power_cycles"] == 6912
    # 文本模式下 NVMe 也要能顶上温度，与日志里的 temperature 一致
    assert info.temperature_c == 32
    rows = dict(smart_mod.collect_nvme_rows(log))
    assert rows["累计读取"] == "63.5 TB"
    assert rows["寿命消耗（%）"] == "14"


def test_as_int_tolerates_thousands_separator():
    assert smart_mod._as_int("7,982") == 7982
    assert smart_mod._as_int("0x0033") == 51
    assert smart_mod._as_int("abc") is None


def test_text_failed_health_marks_not_passed():
    text = "SMART overall-health self-assessment test result: FAILED!\n"
    info = smart_mod.parse_text_payload(text, "/dev/sdb", smart_mod.SmartctlRun(rc=0))
    assert info.smart_passed is False


def test_dig_handles_missing_and_wrong_types():
    assert smart_mod.dig({"a": {"b": 1}}, "a", "b") == 1
    assert smart_mod.dig({"a": {"b": 1}}, "a", "c") is None
    assert smart_mod.dig(None, "a") is None
    assert smart_mod.dig({"a": [1, 2]}, "a", 0) == 1
    assert smart_mod.dig({"a": [1]}, "a", 5) is None
    assert smart_mod.dig({"a": "str"}, "a") == "str"


def test_interpret_run_classifies_returncodes():
    assert smart_mod.interpret_run("/dev/sda", smart_mod.SmartctlRun(rc=0, stdout="{}")).status == smart_mod.STATUS_OK
    assert smart_mod.interpret_run("/dev/sda", smart_mod.SmartctlRun(rc=None, timed_out=True)).status == smart_mod.STATUS_TIMEOUT
    assert (
        smart_mod.interpret_run(
            "/dev/sda", smart_mod.SmartctlRun(rc=None, error_kind=smart_mod.STATUS_NOT_INSTALLED)
        ).status
        == smart_mod.STATUS_NOT_INSTALLED
    )
    assert (
        smart_mod.interpret_run(
            "/dev/sda", smart_mod.SmartctlRun(rc=None, error_kind=smart_mod.STATUS_DEVICE_GONE)
        ).status
        == smart_mod.STATUS_DEVICE_GONE
    )
    # 无 JSON 输出、文本也解析不出东西时降级为文本模式而不是崩溃
    info = smart_mod.interpret_run("/dev/sda", smart_mod.SmartctlRun(rc=2, stdout="garbage"))
    assert info.parse_mode == smart_mod.PARSE_MODE_TEXT


def test_run_smartctl_uses_list_args_and_validates(monkeypatch, fake_devices):
    """第 6 节：禁止 shell=True，设备名先过白名单。"""
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = b"{}"
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeResult()

    monkeypatch.setattr(smart_mod.subprocess, "run", fake_run)
    run = smart_mod.run_smartctl("/dev/sda", ("-H",), DEFAULT_CONFIG, fake_devices)
    assert run.rc == 0
    assert captured["argv"][1:] == ["-H", "/dev/sda"]
    assert isinstance(captured["argv"], list)
    assert captured["kwargs"].get("shell") in (None, False)
    assert captured["kwargs"]["timeout"] == DEFAULT_CONFIG.timeout_seconds


def test_run_smartctl_rejects_unlisted_device(fake_devices, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("白名单未命中时绝不应启动子进程")

    monkeypatch.setattr(smart_mod.subprocess, "run", boom)
    run = smart_mod.run_smartctl("/dev/sdz", ("-H",), DEFAULT_CONFIG, fake_devices)
    assert run.rc is None
    assert run.error_kind == smart_mod.STATUS_ERROR


def test_run_smartctl_rejects_shell_metacharacter(fake_devices, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("含 shell 元字符的设备名绝不应启动子进程")

    monkeypatch.setattr(smart_mod.subprocess, "run", boom)
    run = smart_mod.run_smartctl("/dev/sda;touch /tmp/pwn", ("-H",), DEFAULT_CONFIG, fake_devices)
    assert run.error_kind == smart_mod.STATUS_ERROR


# --------------------------------------------------------------------------
# USB 桥接失败（A3 要求的样本之一）
# --------------------------------------------------------------------------
def test_usb_bridge_fixture_yields_usb_bridge_failed(monkeypatch):
    payload = load_fixture("usb_bridge_failed.json")
    # 三层探测全部失败后，状态必须变成 usb_bridge_failed 并列出尝试过的 -d
    monkeypatch.setattr(
        smart_mod,
        "read_smart",
        lambda device, cfg=DEFAULT_CONFIG, d_arg=None, allowed_devices=None, use_json=True: smart_mod.parse_json_payload(
            payload, device, smart_mod.SmartctlRun(rc=2)
        ),
    )
    info = smart_mod.read_smart_auto("/dev/sdb", DEFAULT_CONFIG, is_usb=True)
    assert info.status == smart_mod.STATUS_USB_BRIDGE_FAILED
    assert "-d sat" in info.usb_d_tried
    assert "已尝试" in (info.detail or "")
    assert info.status_text == "该 USB 设备不支持 SMART 透传"


def test_usb_bridge_fixture_message_kept():
    payload = load_fixture("usb_bridge_failed.json")
    info = smart_mod.parse_json_payload(payload, "/dev/sdb", smart_mod.SmartctlRun(rc=2))
    assert info.status == smart_mod.STATUS_ERROR
    assert any("USB bridge" in m for m in info.messages)


# --------------------------------------------------------------------------
# 进程级异常
# --------------------------------------------------------------------------
def test_run_smartctl_timeout(monkeypatch, fake_devices):
    def fake_run(argv, **kwargs):
        raise smart_mod.subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr(smart_mod.subprocess, "run", fake_run)
    run = smart_mod.run_smartctl("/dev/sda", ("-a",), DEFAULT_CONFIG, fake_devices)
    assert run.timed_out is True
    assert run.error_kind == smart_mod.STATUS_TIMEOUT


def test_run_smartctl_missing_binary(monkeypatch, fake_devices):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(smart_mod.subprocess, "run", fake_run)
    run = smart_mod.run_smartctl("/dev/sda", ("-a",), DEFAULT_CONFIG, fake_devices)
    assert run.error_kind == smart_mod.STATUS_NOT_INSTALLED


def test_run_smartctl_oserror_means_device_gone(monkeypatch, fake_devices):
    def fake_run(argv, **kwargs):
        raise OSError("No such device")

    monkeypatch.setattr(smart_mod.subprocess, "run", fake_run)
    run = smart_mod.run_smartctl("/dev/sda", ("-a",), DEFAULT_CONFIG, fake_devices)
    assert run.error_kind == smart_mod.STATUS_DEVICE_GONE


def test_run_smartctl_without_smartctl_installed(monkeypatch, fake_devices):
    monkeypatch.setattr(smart_mod, "find_smartctl", lambda cfg=None: None)
    run = smart_mod.run_smartctl("/dev/sda", ("-a",), DEFAULT_CONFIG, fake_devices)
    assert run.error_kind == smart_mod.STATUS_NOT_INSTALLED


def test_read_smart_builds_standby_safe_args(monkeypatch, fake_devices):
    seen = {}

    def fake_run(device, args, cfg=DEFAULT_CONFIG, allowed_devices=None):
        seen["args"] = list(args)
        return smart_mod.SmartctlRun(rc=0, stdout='{"smartctl": {"exit_status": 0}}')

    monkeypatch.setattr(smart_mod, "run_smartctl", fake_run)
    smart_mod.read_smart("/dev/sda", DEFAULT_CONFIG, allowed_devices=fake_devices)
    # 必须带 -j 与 -n standby,3（第 3.4 节：不能唤醒休眠盘）
    assert seen["args"][0] == "-j"
    assert "-n" in seen["args"]
    assert seen["args"][seen["args"].index("-n") + 1] == DEFAULT_CONFIG.standby_policy
    assert seen["args"][-1] == "-a"


# --------------------------------------------------------------------------
# 文本降级的分支
# --------------------------------------------------------------------------
def test_text_standby():
    info = smart_mod.parse_text_payload(
        "Device is in STANDBY mode, exit(2)\n", "/dev/sda", smart_mod.SmartctlRun(rc=2)
    )
    assert info.status == smart_mod.STATUS_STANDBY
    assert info.device_state == "standby"


def test_text_permission():
    info = smart_mod.parse_text_payload(
        "Smartctl open device: /dev/sda failed: Permission denied\n",
        "/dev/sda",
        smart_mod.SmartctlRun(rc=2),
    )
    assert info.status == smart_mod.STATUS_PERMISSION


def test_text_no_smart():
    info = smart_mod.parse_text_payload(
        "SMART support is: Unavailable\n", "/dev/sdc", smart_mod.SmartctlRun(rc=0)
    )
    assert info.status == smart_mod.STATUS_NO_SMART


def test_text_empty_output():
    info = smart_mod.parse_text_payload("", "/dev/sda", smart_mod.SmartctlRun(rc=2))
    assert info.status == smart_mod.STATUS_ERROR
    assert info.detail


def test_text_parses_capacity_and_power_fields():
    text = (
        "User Capacity:    500,107,862,016 bytes [500 GB]\n"
        "Power On Time:    1234h+56m\n"
        "Power Cycles:     789\n"
        "Temperature:      42 Celsius\n"
        "Rotation Rate:    Solid State Device\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.capacity_bytes == 500107862016
    assert info.power_on_hours == 1234
    assert info.power_cycle_count == 789
    assert info.temperature_c == 42
    assert info.rotation_rate == 0


# --------------------------------------------------------------------------
# 其他解析细节
# --------------------------------------------------------------------------
def test_raw_display_prefers_raw_string(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    attr = next(a for a in info.ata_attributes if a.id == 9)
    assert attr.raw_display() == str(attr.raw)
    attr.raw_text = "7732h+00m"
    assert attr.raw_display() == "7732h+00m"
    attr.raw = None
    attr.raw_text = None
    assert attr.raw_display() == smart_mod.PLACEHOLDER


def test_attribute_name_fallback():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sda", "type": "scsi", "protocol": "ATA"},
        "smart_status": {"passed": True},
        "ata_smart_attributes": {"table": [{"id": 999, "value": 1}]},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.ata_attributes[0].name == "属性 999"
    assert info.ata_attributes[0].display_name == "属性 999"


def test_model_falls_back_to_device_name():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sda", "type": "scsi", "protocol": "ATA"},
        "smart_status": {"passed": True},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.model == "/dev/sda"
    assert info.smart_passed is True
    assert info.serial is None


def test_no_smart_support_sets_status():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sdc", "type": "sat", "protocol": "ATA"},
        "smart_support": {"available": False, "enabled": False},
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sdc", smart_mod.SmartctlRun(rc=0))
    assert info.status == smart_mod.STATUS_NO_SMART


def test_exit_bit_open_failed_with_denied_is_permission():
    payload = {
        "smartctl": {"exit_status": 2, "messages": []},
        "device": {"name": "/dev/sda", "type": "scsi", "protocol": "ATA"},
        "messages": [{"string": "Smartctl open device: /dev/sda failed: Permission denied", "severity": "error"}],
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", smart_mod.SmartctlRun(rc=2))
    assert info.status == smart_mod.STATUS_PERMISSION


# --------------------------------------------------------------- 自检进度（3.5）
def test_json_ata_self_test_in_progress():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/sda", "type": "scsi", "protocol": "ATA"},
        "ata_smart_data": {
            "self_test": {
                "status": {"value": 244, "string": "in progress, 40% remaining", "remaining_percent": 40}
            }
        },
    }
    info = smart_mod.parse_json_payload(payload, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.self_test_status == "in progress, 40% remaining"
    assert info.self_test_value == 244
    assert smart_mod.self_test_in_progress(info) is True
    assert smart_mod.self_test_remaining(info) == 40


def test_json_nvme_self_test_reports_no_test():
    payload = {
        "smartctl": {"exit_status": 0},
        "device": {"name": "/dev/nvme0", "type": "nvme", "protocol": "NVMe"},
        "nvme_smart_health_information_log": {"critical_warning": 0},
        "nvme_self_test_log": {
            "current_self_test_operation": {"value": 0, "string": "No self-test in progress"}
        },
    }
    info = smart_mod.parse_json_payload(payload, "/dev/nvme0", smart_mod.SmartctlRun(rc=0))
    assert info.self_test_status == "No self-test in progress"
    assert smart_mod.self_test_in_progress(info) is False


def test_text_ata_self_test_in_progress_parsed():
    text = (
        "=== START OF READ SMART DATA SECTION ===\n"
        "SMART overall-health self-assessment test result: PASSED\n"
        "Self-test execution status:      ( 244)\tSelf-test routine in progress...\n"
        "\t\t\t\t\t40% of test remaining.\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/sda", smart_mod.SmartctlRun(rc=0))
    assert info.self_test_value == 244
    assert smart_mod.self_test_in_progress(info) is True
    assert smart_mod.self_test_remaining(info) == 40


def test_text_nvme_self_test_status_parsed():
    text = (
        "NVMe Version: 1.3\n"
        "Self-test Log (NVMe Log 0x06, NSID 0xffffffff)\n"
        "Self-test status: No self-test in progress\n"
    )
    info = smart_mod.parse_text_payload(text, "/dev/nvme0", smart_mod.SmartctlRun(rc=0))
    assert info.self_test_status == "No self-test in progress"
    assert smart_mod.self_test_in_progress(info) is False

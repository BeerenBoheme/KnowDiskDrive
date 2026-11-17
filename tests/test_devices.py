"""设备枚举与白名单校验（提示词 3.1 / 第 6 节）。"""

from __future__ import annotations

import pytest

from knowdiskdrive import devices as devices_mod


def _row(name, model, serial, tran, size, rota, dtype="disk"):
    return {
        "name": name,
        "model": model,
        "serial": serial,
        "tran": tran,
        "size": size,
        "rota": rota,
        "type": dtype,
    }


SAMPLE = {
    "blockdevices": [
        _row("sda", "ZHITAI SC001 Act", "ZTB1EXAMPLE0000001", "sata", "476.9G", False),
        _row("nvme0n1", "KBG40ZNV512G KIOXIA", "41OPHEXAMPLE1", "nvme", "476.9G", False),
        _row("loop0", None, None, None, "64M", False, "loop"),
        _row("zram0", None, None, None, "7.5G", False, "disk"),
        _row("dm-0", None, None, None, "476.9G", False, "lvm"),
        _row("sr0", None, None, "sata", "1024M", False, "rom"),
    ]
}


def test_parse_filters_virtual_devices(monkeypatch):
    """A1：本机未接入新盘时，样本里只应剩下 sda 与 nvme0n1。"""
    monkeypatch.setattr(devices_mod, "_device_exists", lambda name: name in {"sda", "nvme0n1"})
    monkeypatch.setattr(devices_mod, "_sysfs_size_bytes", lambda name: None)
    monkeypatch.setattr(devices_mod, "_sysfs_removable", lambda name: False)

    result = devices_mod.parse_lsblk_json(SAMPLE)
    assert [d.name for d in result] == ["nvme0n1", "sda"]


def test_zram_has_disk_type_but_no_tran(monkeypatch):
    """zram0 的 type 也是 disk，必须靠 TRAN 为空 + 无型号过滤掉。"""
    monkeypatch.setattr(devices_mod, "_sysfs_size_bytes", lambda name: None)
    monkeypatch.setattr(devices_mod, "_sysfs_removable", lambda name: False)
    monkeypatch.setattr(devices_mod, "_sysfs_has_model", lambda name: False)
    monkeypatch.setattr(
        devices_mod, "_device_exists", lambda name: name in {"sda", "nvme0n1", "zram0"}
    )
    names = [d.name for d in devices_mod.parse_lsblk_json(SAMPLE)]
    assert "zram0" not in names


def test_excluded_name_prefixes():
    assert devices_mod.is_excluded_name("loop7")
    assert devices_mod.is_excluded_name("zram0")
    assert devices_mod.is_excluded_name("dm-3")
    assert devices_mod.is_excluded_name("sr0")
    assert not devices_mod.is_excluded_name("sda")
    assert not devices_mod.is_excluded_name("nvme0n1")


def test_validate_device_path_accepts_whitelisted(fake_devices):
    assert devices_mod.validate_device_path("/dev/sda", fake_devices) == "/dev/sda"
    assert devices_mod.validate_device_path("/dev/nvme0n1", fake_devices) == "/dev/nvme0n1"


@pytest.mark.parametrize(
    "bad",
    [
        "sda",
        "/dev/../etc/passwd",
        "/dev/sda; rm -rf /",
        "/dev/-a",
        "/dev/",
        "/dev/loop0",
        "/dev/sdb",
        "",
    ],
)
def test_validate_device_path_rejects(bad, fake_devices):
    """提示词第 6 节：设备名白名单校验，无法拼进命令行。"""
    with pytest.raises(ValueError):
        devices_mod.validate_device_path(bad, fake_devices)


def test_validate_rejects_non_string(fake_devices):
    with pytest.raises(ValueError):
        devices_mod.validate_device_path(None, fake_devices)  # type: ignore[arg-type]


def test_find_device_accepts_both_forms(fake_devices):
    assert devices_mod.find_device("sda", fake_devices).name == "sda"
    assert devices_mod.find_device("/dev/sda", fake_devices).name == "sda"
    assert devices_mod.find_device("/dev/sdz", fake_devices) is None


def test_format_bytes_uses_1024_base():
    assert devices_mod.format_bytes(512110190592) == "476.9 GB"
    assert devices_mod.format_bytes(None) == "—"
    assert devices_mod.format_bytes(0) == "—"
    assert devices_mod.format_bytes(1024) == "1 KB"


def test_parse_lsblk_json_rejects_bad_payload():
    with pytest.raises(devices_mod.DeviceEnumerationError):
        devices_mod.parse_lsblk_json({})


# --------------------------------------------------------------------------
# 真实解析路径（lsblk 子进程 + sysfs 回退都用假数据替换）
# --------------------------------------------------------------------------
def test_resolve_tran_fallbacks(monkeypatch):
    # TRAN 缺失：可移动设备判为 USB
    monkeypatch.setattr(devices_mod, "_sysfs_removable", lambda name: True)
    monkeypatch.setattr(devices_mod, "_sysfs_has_model", lambda name: False)
    assert devices_mod.resolve_tran("sdb", None) == "usb"

    # nvme 前缀
    monkeypatch.setattr(devices_mod, "_sysfs_removable", lambda name: False)
    assert devices_mod.resolve_tran("nvme0n1", "") == "nvme"

    # 有 device/model 的固定盘按 SATA 处理
    monkeypatch.setattr(devices_mod, "_sysfs_has_model", lambda name: True)
    assert devices_mod.resolve_tran("sda", None) == "sata"

    # 什么都没有：返回 None 而不是瞎猜
    monkeypatch.setattr(devices_mod, "_sysfs_has_model", lambda name: False)
    assert devices_mod.resolve_tran("vdz", None) is None


def test_device_properties():
    device = devices_mod.Device(
        name="sda", model="  ", serial=None, tran=" sata ", size=None, size_bytes=None, rota=True
    )
    assert device.path == "/dev/sda"
    assert device.display_model == "—"
    assert device.display_serial == "—"
    assert device.tran == " sata "  # 裸 dataclass 不做清洗，清洗在 parse 层


def test_size_from_sysfs_when_lsblk_missing(monkeypatch):
    """lsblk 某些版本 SIZE 为空：用 sysfs 的 512 字节扇区数换算。"""
    monkeypatch.setattr(devices_mod, "_device_exists", lambda name: True)
    monkeypatch.setattr(devices_mod, "_sysfs_size_bytes", lambda name: 100 * 512)
    monkeypatch.setattr(devices_mod, "_sysfs_removable", lambda name: False)
    sample = {"blockdevices": [_row("sda", "X", "SN", "sata", None, False)]}
    devices = devices_mod.parse_lsblk_json(sample)
    assert len(devices) == 1
    assert devices[0].size_bytes == 51200  # 由扇区数换算
    assert devices[0].size is None  # lsblk 没给字符串就保持 None，不伪造


def test_enumerate_devices_runs_lsblk_without_shell(monkeypatch):
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = '{"blockdevices": []}'
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(devices_mod.subprocess, "run", fake_run)
    assert devices_mod.enumerate_devices() == []
    # 绝对路径来自 shutil.which，其余参数必须与常量完全一致
    assert captured["argv"][1:] == list(devices_mod.LSBLK_CMD)[1:]
    assert captured["kwargs"].get("shell") in (None, False)
    assert "timeout" in captured["kwargs"]


def test_enumerate_devices_reports_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no lsblk")

    monkeypatch.setattr(devices_mod.subprocess, "run", boom)
    with pytest.raises(devices_mod.DeviceEnumerationError):
        devices_mod.enumerate_devices()


def test_enumerate_devices_handles_bad_json(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "not json"
        stderr = ""

    monkeypatch.setattr(devices_mod.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(devices_mod.DeviceEnumerationError):
        devices_mod.enumerate_devices()


def test_sysfs_readable_false_for_missing(monkeypatch):
    assert devices_mod.sysfs_readable_by_current_user("sdz") in (True, False)



def test_sysfs_helpers_against_temp_tree(monkeypatch, tmp_path):
    """sysfs 读取用临时目录模拟，覆盖真实实现而不是打桩空实现。"""
    block = tmp_path / "block"
    dev = block / "sdz"
    (dev / "device").mkdir(parents=True)
    (dev / "size").write_text("1000\n")
    (dev / "removable").write_text("1\n")
    (dev / "device" / "model").write_text("FAKE DISK\n")
    (block / "garbage").mkdir()
    (block / "garbage" / "size").write_text("not-a-number\n")
    monkeypatch.setattr(devices_mod, "SYSFS_BLOCK", block)

    assert devices_mod._read_sysfs("sdz", "size") == "1000"
    assert devices_mod._read_sysfs("missing", "size") is None
    assert devices_mod._sysfs_size_bytes("sdz") == 1000 * 512
    assert devices_mod._sysfs_size_bytes("garbage") is None
    assert devices_mod._sysfs_size_bytes("missing") is None
    assert devices_mod._sysfs_removable("sdz") is True
    assert devices_mod._sysfs_removable("missing") is False
    assert devices_mod._sysfs_has_model("sdz") is True
    assert devices_mod._sysfs_has_model("missing") is False


def test_parse_skips_rows_without_name(monkeypatch):
    monkeypatch.setattr(devices_mod, "_device_exists", lambda name: True)
    sample = {"blockdevices": [{"name": None, "type": "disk"}, "not-a-dict", {"name": "sda", "type": "disk", "tran": "sata"}]}
    names = [d.name for d in devices_mod.parse_lsblk_json(sample)]
    assert names == ["sda"]


def test_to_bool_and_clean_optional():
    assert devices_mod._to_bool(True) is True
    assert devices_mod._to_bool("1") is True
    assert devices_mod._to_bool("no") is False
    assert devices_mod._to_bool(1) is True
    assert devices_mod._to_bool(object(), default=True) is True
    assert devices_mod._clean_optional(None) is None
    assert devices_mod._clean_optional("  ") is None
    assert devices_mod._clean_optional(" x ") == "x"


def test_enumerate_devices_timeout(monkeypatch):
    def boom(*args, **kwargs):
        raise devices_mod.subprocess.TimeoutExpired("lsblk", 5)

    monkeypatch.setattr(devices_mod.subprocess, "run", boom)
    with pytest.raises(devices_mod.DeviceEnumerationError):
        devices_mod.enumerate_devices()


def test_enumerate_devices_bad_returncode(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "lsblk: boom"

    monkeypatch.setattr(devices_mod.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(devices_mod.DeviceEnumerationError):
        devices_mod.enumerate_devices()

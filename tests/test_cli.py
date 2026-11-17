"""CLI 行为与退出码（验收标准 A1 / A2 / A4 + 提示词第 8 节）。"""

from __future__ import annotations

import json

import pytest

from knowdiskdrive import cli
from knowdiskdrive import desktop as desktop_mod
from knowdiskdrive import devices as devices_mod
from knowdiskdrive import privilege as priv_mod
from knowdiskdrive import smart as smart_mod
from knowdiskdrive.config import DEFAULT_CONFIG

from conftest import load_fixture, make_info


@pytest.fixture
def cli_env(monkeypatch, fake_devices):
    """把 main() 的外部依赖全部替换掉：不碰真实磁盘、不启动子进程。"""
    monkeypatch.setattr(cli, "load_config", lambda: (DEFAULT_CONFIG, ""))
    monkeypatch.setattr(smart_mod, "find_smartctl", lambda cfg=None: "/usr/bin/smartctl")
    monkeypatch.setattr(devices_mod, "enumerate_devices", lambda: list(fake_devices))
    return fake_devices


def _patch_reader(monkeypatch, mapping):
    """按设备路径返回预置的 DiskInfo。"""

    def fake_read(device, cfg=DEFAULT_CONFIG, **kwargs):
        return mapping[device]

    monkeypatch.setattr(smart_mod, "read_smart_auto", fake_read)


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
def test_list_table(cli_env, capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "/dev/sda" in out
    assert "/dev/nvme0n1" in out
    assert "ZHITAI" in out
    assert "SATA" in out and "NVMe" in out
    assert "476.9 GB" in out
    assert "共 2 块磁盘" in out


def test_list_json(cli_env, capsys):
    assert cli.main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [d["path"] for d in payload["devices"]] == ["/dev/sda", "/dev/nvme0n1"]
    assert payload["devices"][0]["size_bytes"] == 512110190592
    assert payload["devices"][1]["tran"] == "nvme"


def test_list_empty(monkeypatch, cli_env, capsys):
    monkeypatch.setattr(devices_mod, "enumerate_devices", lambda: [])
    assert cli.main(["list"]) == 0
    assert "未发现可用的物理磁盘" in capsys.readouterr().out


def test_list_enumeration_failure(monkeypatch, cli_env, capsys):
    def boom():
        raise devices_mod.DeviceEnumerationError("lsblk 不可用")

    monkeypatch.setattr(devices_mod, "enumerate_devices", boom)
    assert cli.main(["list"]) == 2
    assert "lsblk 不可用" in capsys.readouterr().err


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------
def test_show_healthy_ata(monkeypatch, cli_env, ata_healthy_payload, capsys):
    info = make_info(ata_healthy_payload, "/dev/sda")
    _patch_reader(monkeypatch, {"/dev/sda": info})
    assert cli.main(["show", "/dev/sda"]) == 0
    out = capsys.readouterr().out
    assert "[良好]" in out
    assert info.serial in out
    assert "33°C" in out
    assert f"{info.power_on_hours} 小时" in out
    assert "共 30 条属性" in out
    # 置顶属性应排在表格最前面
    body = [line for line in out.splitlines() if line.strip().startswith(("5 ", "9 "))]
    assert body and body[0].split()[0] == "5"


def test_show_json(monkeypatch, cli_env, nvme_healthy_payload, capsys):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    _patch_reader(monkeypatch, {"/dev/nvme0n1": info})
    assert cli.main(["show", "/dev/nvme0n1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["health"] == "good"
    assert payload["protocol"] == "nvme"
    assert payload["nvme_log"]["percentage_used"] == 14
    assert payload["attributes"] == []


def test_show_danger(monkeypatch, cli_env, ata_failing_payload, capsys):
    info = make_info(ata_failing_payload, "/dev/sda")
    _patch_reader(monkeypatch, {"/dev/sda": info})
    assert cli.main(["show", "sda"]) == 0
    out = capsys.readouterr().out
    assert "[危险]" in out
    assert "判定依据" in out


def test_show_permission_denied(monkeypatch, cli_env, capsys):
    """A2 的真机现状：无 cap 时给出可执行的中文指引，退出码 2。"""
    info = smart_mod.parse_json_payload(
        load_fixture("ata_permission_denied.json"), "/dev/sda", smart_mod.SmartctlRun(rc=2)
    )
    _patch_reader(monkeypatch, {"/dev/sda": info})
    assert cli.main(["show", "/dev/sda"]) == 2
    out = capsys.readouterr().out
    assert "无法判定" in out
    assert "fix-permission" in out


def test_show_unknown_device(cli_env, capsys):
    assert cli.main(["show", "/dev/sdz"]) == 1
    assert "/dev/sdz" in capsys.readouterr().err


def test_show_invalid_device_string(cli_env, capsys):
    assert cli.main(["show", "sda;rm -rf /"]) == 1
    assert "错误" in capsys.readouterr().err


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------
def test_health_all_good(monkeypatch, cli_env, ata_healthy_payload, nvme_healthy_payload, capsys):
    _patch_reader(
        monkeypatch,
        {
            "/dev/sda": make_info(ata_healthy_payload, "/dev/sda"),
            "/dev/nvme0n1": make_info(nvme_healthy_payload, "/dev/nvme0n1"),
        },
    )
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert "总体健康：良好" in out


def test_health_mixed_permission(monkeypatch, cli_env, ata_healthy_payload, capsys):
    denied = smart_mod.parse_json_payload(
        load_fixture("nvme_permission_denied.json"), "/dev/nvme0n1", smart_mod.SmartctlRun(rc=2)
    )
    _patch_reader(
        monkeypatch,
        {"/dev/sda": make_info(ata_healthy_payload, "/dev/sda"), "/dev/nvme0n1": denied},
    )
    assert cli.main(["health"]) == 2
    out = capsys.readouterr().out
    assert "总体健康：良好" in out  # 只有一块失败时仍按可读盘的结论
    assert "fix-permission" in out


def test_health_all_failed(monkeypatch, cli_env, capsys):
    denied_ata = smart_mod.parse_json_payload(
        load_fixture("ata_permission_denied.json"), "/dev/sda", smart_mod.SmartctlRun(rc=2)
    )
    denied_nvme = smart_mod.parse_json_payload(
        load_fixture("nvme_permission_denied.json"), "/dev/nvme0n1", smart_mod.SmartctlRun(rc=2)
    )
    _patch_reader(monkeypatch, {"/dev/sda": denied_ata, "/dev/nvme0n1": denied_nvme})
    assert cli.main(["health"]) == 2
    assert "无法判定" in capsys.readouterr().out


def test_health_json(monkeypatch, cli_env, ata_healthy_payload, capsys):
    _patch_reader(
        monkeypatch,
        {
            "/dev/sda": make_info(ata_healthy_payload, "/dev/sda"),
            "/dev/nvme0n1": make_info(ata_healthy_payload, "/dev/nvme0n1"),
        },
    )
    assert cli.main(["health", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    assert all(item["health"] == "good" for item in payload)


# --------------------------------------------------------------------------
# daemon（提示词第 7 节）
# --------------------------------------------------------------------------
def _stub_reader(device, cfg=None, **kwargs):
    info = smart_mod.DiskInfo(
        device=device, serial="SN", model="STUB", status=smart_mod.STATUS_OK
    )
    info.temperature_c = 33
    return info


def _patch_daemon_store(monkeypatch, path):
    """让 daemon 用临时文件库：Monitor 退出时会关连接，:memory: 会丢数据。"""
    from knowdiskdrive import daemon as daemon_mod
    from knowdiskdrive.history import HistoryStore

    def factory(_path=None):
        return HistoryStore(path)

    monkeypatch.setattr(daemon_mod, "open_store", factory)
    return HistoryStore(path)


def test_daemon_once(monkeypatch, cli_env, capsys, tmp_path):
    """--once 只采一轮，直接写历史库后退出。"""
    monkeypatch.setattr(smart_mod, "read_smart_auto", _stub_reader)
    store = _patch_daemon_store(monkeypatch, tmp_path / "history.db")
    try:
        assert cli.main(["daemon", "--once"]) == 0
        assert capsys.readouterr().out.strip()
        assert store.count("temp_samples") == len(cli_env)
    finally:
        store.close()


def test_daemon_rounds(monkeypatch, cli_env, tmp_path):
    """--rounds N 跑满 N 轮就退出（不进入无限循环）。"""
    from knowdiskdrive import daemon as daemon_mod

    monkeypatch.setattr(smart_mod, "read_smart_auto", _stub_reader)
    store = _patch_daemon_store(monkeypatch, tmp_path / "history.db")
    # 不真的 sleep：把 stop_event 的 wait 变成立即返回 False
    monkeypatch.setattr(daemon_mod._NullEvent, "wait", lambda self, timeout: False)
    try:
        assert cli.main(["daemon", "--rounds", "1", "--interval", "10"]) == 0
        assert store.count("temp_samples") == len(cli_env)
    finally:
        store.close()


def test_daemon_rejects_bad_rounds(cli_env, capsys):
    assert cli.main(["daemon", "--rounds", "0"]) == 1
    assert "--rounds" in capsys.readouterr().err


def test_daemon_parser_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["daemon"])
    assert args.once is False
    assert args.rounds is None
    assert args.interval is None


# --------------------------------------------------------------------------
# 入口与其他
# --------------------------------------------------------------------------
def test_no_subcommand_prints_help(cli_env):
    assert cli.main([]) == 1


def test_missing_smartctl(monkeypatch, cli_env, capsys):
    monkeypatch.setattr(smart_mod, "find_smartctl", lambda cfg=None: None)
    assert cli.main(["list"]) == 2
    assert "smartmontools" in capsys.readouterr().err


def test_install_desktop_runs_without_smartctl(monkeypatch, cli_env, capsys, tmp_path):
    """install-desktop 只写桌面文件，没装 smartctl 也不该被拦。"""
    monkeypatch.setattr(smart_mod, "find_smartctl", lambda cfg=None: None)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(desktop_mod, "detect_exec_command", lambda: "/usr/bin/knowdiskdrive-gui")
    assert cli.main(["install-desktop"]) == 0
    out = capsys.readouterr().out
    assert "已安装菜单项" in out
    assert (tmp_path / "applications" / "knowdiskdrive.desktop").is_file()


def test_render_table_aligns_wide_chars():
    lines = cli.render_table(["设备", "型号"], [["/dev/sda", "知态盘"], ["/dev/nvme0n1", "KIOXIA"]])
    widths = {cli.display_width(line) for line in lines[2:]}
    assert len(widths) == 1
    assert cli.display_width("设备") == 4


def test_truncate_keeps_width():
    text = "ZHITAI SC001 Active 512GB SSD"
    clipped = cli.truncate(text, 12)
    assert cli.display_width(clipped) <= 12
    assert clipped.endswith("…")


def test_pad_right_align():
    assert cli.pad("7", 4, "right") == "   7"
    assert cli.pad("中文", 6) == "中文  "


# --------------------------------------------------------------------------
# export（CSV 温度历史）
# --------------------------------------------------------------------------
def _patch_export_store(monkeypatch, path, samples):
    """把 export 用的 open_store 换成本地文件库，并按刻度预置样本。"""
    from knowdiskdrive import history as history_mod

    store = history_mod.HistoryStore(path)
    for temp in samples:
        store.record_sample("SN-EXPORT", temp, "good")
    store.close()

    def factory(_path=None):
        return history_mod.HistoryStore(path)

    monkeypatch.setattr(history_mod, "open_store", factory)


def test_export_single_device_csv(monkeypatch, cli_env, tmp_path):
    _patch_export_store(monkeypatch, tmp_path / "history.db", [31, 33, 35])
    device = cli_env[0]
    info = make_info(load_fixture("ata_zhitai_healthy.json"), device.path)
    info.serial = "SN-EXPORT"
    _patch_reader(monkeypatch, {device.path: info})

    target = tmp_path / "out.csv"
    assert cli.main(["export", "--device", device.path, "--output", str(target)]) == 0
    lines = target.read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(lines) == 4  # 表头 + 3 行
    assert "温度" in lines[0]


def test_export_without_samples_returns_2(monkeypatch, cli_env, tmp_path):
    _patch_export_store(monkeypatch, tmp_path / "history.db", [])
    device = cli_env[0]
    info = make_info(load_fixture("ata_zhitai_healthy.json"), device.path)
    info.serial = "SN-EXPORT"
    _patch_reader(monkeypatch, {device.path: info})

    assert cli.main(["export", "--device", device.path, "--output", str(tmp_path / "x.csv")]) == 2


def test_export_without_serial_returns_2(monkeypatch, cli_env, tmp_path, capsys):
    _patch_export_store(monkeypatch, tmp_path / "history.db", [30])
    device = cli_env[0]
    info = make_info(load_fixture("ata_zhitai_healthy.json"), device.path)
    info.serial = ""
    _patch_reader(monkeypatch, {device.path: info})

    assert cli.main(["export", "--device", device.path]) == 2
    assert "序列号" in capsys.readouterr().err


def test_export_parser_defaults():
    args = cli.build_parser().parse_args(["export"])
    assert args.device is None and args.days is None and args.output is None

def test_version_flag_reports_package_version(capsys):
    """--version 必须与包内 __version__ 同源，不能各写一份硬编码。"""
    import knowdiskdrive

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert knowdiskdrive.__version__ in capsys.readouterr().out


def test_pyproject_version_matches_package_version():
    """发布版的 pyproject.toml 与 __init__.py 版本号必须一致，否则 wheel 名与
    --version 输出会对不上（打包脚本都从 pyproject.toml 读版本）。"""
    import re
    from pathlib import Path

    import knowdiskdrive

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    assert match is not None, "pyproject.toml 里找不到 version"
    assert match.group(1) == knowdiskdrive.__version__



# --------------------------------------------------------------------------
# fix-permission（提示词第 6 节：AppImage / pip 安装后没有 install.sh）
# --------------------------------------------------------------------------
def test_fix_permission_print_only(cli_env, capsys):
    assert cli.main(["fix-permission", "--print"]) == 0
    assert capsys.readouterr().out.strip() == (
        "setcap cap_sys_rawio,cap_dac_read_search,cap_sys_admin+ep /usr/bin/smartctl"
    )


def test_fix_permission_reports_already_configured(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(priv_mod, "is_configured", lambda *a, **kw: True)
    assert cli.main(["fix-permission"]) == 0
    out = capsys.readouterr().out
    assert "已有读取权限" in out
    assert "nosuid" in out


def test_fix_permission_without_smartctl_errors(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(smart_mod, "find_smartctl", lambda cfg=None: None)
    assert cli.main(["fix-permission"]) == 2
    assert "smartctl" in capsys.readouterr().err


def test_fix_permission_failure_prints_manual_command(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(priv_mod, "is_configured", lambda *a, **kw: False)
    monkeypatch.setattr(
        priv_mod,
        "apply_capabilities",
        lambda *a, **kw: priv_mod.ElevationResult(
            ok=False, backend="sudo", message="已取消授权。"
        ),
    )
    assert cli.main(["fix-permission", "--quiet"]) == 1
    err = capsys.readouterr().err
    assert "授权未完成" in err
    assert "sudo setcap" in err


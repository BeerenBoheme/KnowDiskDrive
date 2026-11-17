"""配置加载（提示词第 11 节：TOML 可覆盖，坏配置不能崩）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowdiskdrive import config as config_mod


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_file(tmp_path):
    cfg, warning = config_mod.load_config(tmp_path / "missing.toml")
    assert warning is None
    assert cfg.timeout_seconds == 15.0
    assert cfg.sample_interval_seconds == 300
    assert cfg.retention_days == 90
    assert cfg.pinned_attribute_ids == (5, 10, 194, 197, 198, 199)


def test_explicit_path_overrides(monkeypatch, tmp_path):
    path = _write(
        tmp_path,
        "[smartctl]\ntimeout_seconds = 7.5\nstandby_policy = \"standby,1\"\n\n"
        "[history]\nsample_interval_seconds = 60\nretention_days = 30\n",
    )
    monkeypatch.setenv("KDD_CONFIG", str(path))
    cfg, warning = config_mod.load_config()
    assert warning is None
    assert cfg.timeout_seconds == 7.5
    assert cfg.standby_policy == "standby,1"
    assert cfg.sample_interval_seconds == 60
    assert cfg.retention_days == 30


def test_temperature_thresholds_from_toml(tmp_path):
    path = _write(
        tmp_path,
        "[temp_thresholds.hdd]\ngreen_below = 38\nyellow_max = 46\n",
    )
    cfg, warning = config_mod.load_config(path)
    assert warning is None
    assert cfg.temp_thresholds["hdd"].green_below == 38
    assert cfg.temp_thresholds["hdd"].yellow_max == 46
    # 未覆盖的介质保持默认
    assert cfg.temp_thresholds["nvme"].green_below == 65


def test_broken_toml_returns_warning_and_defaults(tmp_path):
    path = _write(tmp_path, "this is = not toml [[[")
    cfg, warning = config_mod.load_config(path)
    assert warning and "回退默认配置" in warning
    assert cfg.timeout_seconds == 15.0


def test_absurd_values_are_clamped(tmp_path):
    """坏的数值不能变成负间隔无限采样（第 7 节性能约束）。"""
    path = _write(
        tmp_path,
        "[smartctl]\ntimeout_seconds = -3\n\n[history]\nsample_interval_seconds = 0\nretention_days = -5\n",
    )
    cfg, warning = config_mod.load_config(path)
    assert warning is None
    assert cfg.sample_interval_seconds == 10  # 下限保护
    assert cfg.retention_days == 1
    assert cfg.timeout_seconds == -3.0  # 原样保留，不做静默修正


def test_unknown_keys_ignored(tmp_path):
    path = _write(tmp_path, "[nonsense]\nfoo = 1\n\n[ui]\npinned_attribute_ids = [5, 'x', 199]\n")
    cfg, warning = config_mod.load_config(path)
    assert warning is None
    assert cfg.pinned_attribute_ids == (5, 199)


def test_db_path_env(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("KDD_DB", str(target))
    assert config_mod.default_db_path() == target


def test_db_path_default(monkeypatch):
    monkeypatch.delenv("KDD_DB", raising=False)
    path = config_mod.default_db_path()
    assert path.name == "history.db"
    assert path.parent.name == "knowdiskdrive"


def test_config_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KDD_CONFIG", str(tmp_path / "a.toml"))
    assert config_mod.default_config_path() == tmp_path / "a.toml"


def test_temp_threshold_lookup_falls_back_to_sata_ssd():
    assert config_mod.DEFAULT_CONFIG.temp_threshold("nvme").green_below == 65
    assert config_mod.DEFAULT_CONFIG.temp_threshold("weird").green_below == 50


def _readme_toml_block() -> str:
    """取出开发文档第 6 节里的那份示例配置（README 只留简介，长文在 docs/）。"""
    readme = Path(__file__).resolve().parent.parent / "docs" / "DEVELOPMENT.md"
    text = readme.read_text(encoding="utf-8")
    start = text.index("```toml") + len("```toml")
    end = text.index("```", start)
    return text[start:end]


def test_readme_example_config_is_valid(tmp_path):
    """文档里贴的配置必须真能加载——文档和代码不能各说各话。"""
    path = _write(tmp_path, _readme_toml_block())
    cfg, warning = config_mod.load_config(path)
    assert warning is None
    assert cfg.smartctl_path == ""
    assert cfg.timeout_seconds == 15.0
    assert cfg.standby_policy == "standby,3"
    assert cfg.usb_bridge_d_args == ("sat", "usbjmicron", "usbsunplus")
    assert cfg.sample_interval_seconds == 300
    assert cfg.retention_days == 90
    assert cfg.alert_dedup_minutes == 30
    assert cfg.pinned_attribute_ids == (5, 10, 194, 197, 198, 199)
    for media in ("hdd", "sata_ssd", "nvme"):
        assert media in cfg.temp_thresholds


def test_every_config_field_is_reachable_from_toml():
    """TOML 键名与 Config 字段错位的话，本测试会拦下来。"""
    cfg = config_mod.config_from_dict(
        {
            "smartctl": {
                "path": "/opt/smartctl",
                "timeout_seconds": 9,
                "standby_policy": "standby,7",
                "usb_bridge_d_args": ["sat"],
            },
            "history": {"sample_interval_seconds": 45, "retention_days": 7},
            "alerts": {"dedup_minutes": 3},
            "temp_thresholds": {"hdd": {"green_below": 33, "yellow_max": 44}},
            "ui": {"pinned_attribute_ids": [9]},
        }
    )
    assert cfg.smartctl_path == "/opt/smartctl"
    assert cfg.timeout_seconds == 9.0
    assert cfg.standby_policy == "standby,7"
    assert cfg.usb_bridge_d_args == ("sat",)
    assert cfg.sample_interval_seconds == 45
    assert cfg.retention_days == 7
    assert cfg.alert_dedup_minutes == 3
    assert cfg.temp_thresholds["hdd"].green_below == 33
    assert cfg.pinned_attribute_ids == (9,)

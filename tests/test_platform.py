"""跨发行版兼容性：smartctl 定位与安装提示、tomllib 缺失时的兜底。

这几条对应「打包到别的电脑上能不能用」里的关键假设：
从应用菜单启动时 PATH 未必含 /usr/sbin；目标机的包管理器不一定是 apt。
"""

from __future__ import annotations

import dataclasses
import stat
from pathlib import Path

import pytest

from knowdiskdrive import config as config_mod
from knowdiskdrive import smart as smart_mod


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\necho fake-smartctl\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_os_release(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "os-release"
    target.write_text(body, encoding="utf-8")
    return target


# --------------------------------------------------------------------------
# find_smartctl
# --------------------------------------------------------------------------
def test_find_smartctl_prefers_path(monkeypatch):
    monkeypatch.setattr(smart_mod.shutil, "which", lambda name: "/usr/bin/smartctl")
    assert smart_mod.find_smartctl() == "/usr/bin/smartctl"


def test_find_smartctl_falls_back_to_sbin(monkeypatch, tmp_path):
    """PATH 里没有也要能找到：.desktop 启动继承的 PATH 常常不含 /usr/sbin。"""
    monkeypatch.setattr(smart_mod.shutil, "which", lambda name: None)
    fake = _make_executable(tmp_path / "smartctl")
    monkeypatch.setattr(smart_mod, "SMARTCTL_FALLBACK_DIRS", (str(tmp_path),))
    assert smart_mod.find_smartctl() == str(fake)


def test_find_smartctl_ignores_non_executable_in_fallback_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(smart_mod.shutil, "which", lambda name: None)
    (tmp_path / "smartctl").write_text("not executable", encoding="utf-8")
    monkeypatch.setattr(smart_mod, "SMARTCTL_FALLBACK_DIRS", (str(tmp_path),))
    assert smart_mod.find_smartctl() is None


def test_find_smartctl_honours_configured_absolute_path(tmp_path):
    fake = _make_executable(tmp_path / "my-smartctl")
    cfg = dataclasses.replace(smart_mod.DEFAULT_CONFIG, smartctl_path=str(fake))
    assert smart_mod.find_smartctl(cfg) == str(fake)


def test_find_smartctl_rejects_missing_configured_path(tmp_path):
    """配置写错时要报「找不到」，不能静默用 PATH 上的另一个 smartctl。"""
    cfg = dataclasses.replace(smart_mod.DEFAULT_CONFIG, smartctl_path=str(tmp_path / "nope"))
    assert smart_mod.find_smartctl(cfg) is None


def test_find_smartctl_none_when_nowhere(monkeypatch, tmp_path):
    monkeypatch.setattr(smart_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(smart_mod, "SMARTCTL_FALLBACK_DIRS", (str(tmp_path),))
    assert smart_mod.find_smartctl() is None


# --------------------------------------------------------------------------
# 安装提示按发行版给出
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('ID=ubuntu\nID_LIKE=debian\n', "sudo apt install smartmontools"),
        ('ID="debian"\n', "sudo apt install smartmontools"),
        ("ID=arch\n", "sudo pacman -S smartmontools"),
        ("ID=manjaro\nID_LIKE=arch\n", "sudo pacman -S smartmontools"),
        ("ID=fedora\n", "sudo dnf install smartmontools"),
        ("ID=rocky\nID_LIKE=\"rhel centos fedora\"\n", "sudo dnf install smartmontools"),
        ("ID=opensuse-tumbleweed\nID_LIKE=opensuse\n", "sudo zypper install smartmontools"),
        ("ID=alpine\n", "sudo apk add smartmontools"),
        ("ID=gentoo\n", "sudo emerge --ask sys-apps/smartmontools"),
    ],
)
def test_install_hint_per_distro(tmp_path, body, expected):
    path = _write_os_release(tmp_path, body)
    assert smart_mod.smartctl_install_hint(path) == expected


def test_install_hint_unknown_distro_mentions_pretty_name(tmp_path):
    path = _write_os_release(tmp_path, 'NAME="NixOS"\nID=nixos\nPRETTY_NAME="NixOS 24.05"\n')
    hint = smart_mod.smartctl_install_hint(path)
    assert "NixOS 24.05" in hint
    assert "包管理器" in hint


def test_install_hint_without_os_release(tmp_path):
    hint = smart_mod.smartctl_install_hint(tmp_path / "missing")
    assert "smartmontools" in hint


def test_read_os_release_tolerates_missing_file(tmp_path):
    assert smart_mod._read_os_release(tmp_path / "nope") == {}


def test_read_os_release_strips_quotes(tmp_path):
    path = _write_os_release(tmp_path, 'NAME="Ubuntu"\nID=ubuntu\n')
    values = smart_mod._read_os_release(path)
    assert values["NAME"] == "Ubuntu"
    assert values["ID"] == "ubuntu"


def test_not_installed_detail_uses_distro_hint(tmp_path, monkeypatch):
    """读盘失败信息里要带对当前发行版可用的安装命令，不能写死 apt。"""
    path = _write_os_release(tmp_path, "ID=arch\n")
    monkeypatch.setattr(smart_mod, "OS_RELEASE_PATH", path)
    info = smart_mod.interpret_run("/dev/sda", smart_mod.SmartctlRun(rc=None, error_kind=smart_mod.STATUS_NOT_INSTALLED))
    assert info.status == smart_mod.STATUS_NOT_INSTALLED
    assert "pacman" in (info.detail or "")


# --------------------------------------------------------------------------
# tomllib 缺失（Python 3.10 且没装 tomli）
# --------------------------------------------------------------------------
def test_load_config_without_toml_parser(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("retention_days = 7\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "tomllib", None)
    cfg, warning = config_mod.load_config(path)
    assert cfg is config_mod.DEFAULT_CONFIG
    assert warning is not None and "tomli" in warning


def test_load_config_broken_toml_falls_back(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("这不是 toml = = =\n", encoding="utf-8")
    cfg, warning = config_mod.load_config(path)
    assert cfg is config_mod.DEFAULT_CONFIG
    assert warning is not None

"""菜单项安装（knowdiskdrive/desktop.py）与包内资源的测试。

覆盖「pip 安装后没有源码树」这个场景：资源从包内取，装到 XDG 目录，
且不允许以 root 身份写进 root 的家目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowdiskdrive import data as data_mod
from knowdiskdrive import desktop as desktop_mod


@pytest.fixture()
def xdg_home(tmp_path, monkeypatch):
    """把 XDG_DATA_HOME 指到临时目录，避免污染真实的 ~/.local/share。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_packaged_assets_are_importable():
    """.desktop 与图标必须随包发布（wheel 里的 package-data）。"""
    assert data_mod.desktop_path() is not None
    assert data_mod.icon_path() is not None
    assert data_mod.icon_path().read_text(encoding="utf-8").lstrip().startswith("<svg")


def test_resource_path_returns_none_for_missing_name():
    assert data_mod.resource_path("no-such-file.desktop") is None


def test_render_desktop_rewrites_exec_and_tryexec():
    text = desktop_mod.render_desktop("/opt/kdd/bin/knowdiskdrive-gui")
    assert "Exec=/opt/kdd/bin/knowdiskdrive-gui %F" in text
    assert "TryExec=/opt/kdd/bin/knowdiskdrive-gui" in text
    # 其余字段必须原样保留
    assert "Icon=knowdiskdrive" in text
    assert "Categories=System;Monitor;" in text


def test_render_desktop_drops_arguments_from_tryexec():
    """TryExec 只认程序本身，不能带参数（否则菜单会认为找不到程序）。"""
    text = desktop_mod.render_desktop("/usr/bin/python3 -m knowdiskdrive.gui")
    assert "Exec=/usr/bin/python3 -m knowdiskdrive.gui %F" in text
    assert "TryExec=/usr/bin/python3\n" in text


def test_install_writes_desktop_and_icon(xdg_home, monkeypatch):
    monkeypatch.setattr(desktop_mod, "detect_exec_command", lambda: "/usr/bin/knowdiskdrive-gui")
    result = desktop_mod.install()

    assert result.desktop_file == xdg_home / "applications" / "knowdiskdrive.desktop"
    assert result.icon_file == xdg_home / "icons" / "hicolor" / "scalable" / "apps" / "knowdiskdrive.svg"
    assert result.desktop_file.is_file()
    assert result.icon_file.is_file()
    assert "Exec=/usr/bin/knowdiskdrive-gui %F" in result.desktop_file.read_text(encoding="utf-8")
    # 权限要是普通用户可读（菜单服务不一定以 root 读文件）
    assert oct(result.desktop_file.stat().st_mode)[-3:] == "644"


def test_install_honours_explicit_exec_command(xdg_home):
    result = desktop_mod.install("/opt/custom/kdd-gui")
    assert result.exec_command == "/opt/custom/kdd-gui"
    assert "Exec=/opt/custom/kdd-gui %F" in result.desktop_file.read_text(encoding="utf-8")


def test_install_refuses_root(xdg_home, monkeypatch):
    """以 root 跑会把菜单装进 /root，普通用户看不到——必须直接报错。"""
    monkeypatch.setattr(desktop_mod.os, "geteuid", lambda: 0)
    with pytest.raises(desktop_mod.DesktopInstallError) as excinfo:
        desktop_mod.install()
    assert "sudo" in str(excinfo.value)


def test_install_reports_missing_assets(xdg_home, monkeypatch):
    monkeypatch.setattr(desktop_mod.data_mod, "desktop_path", lambda: None)
    with pytest.raises(desktop_mod.DesktopInstallError):
        desktop_mod.install()


def test_detect_exec_command_prefers_console_script(monkeypatch):
    monkeypatch.setattr(desktop_mod.shutil, "which", lambda name: "/usr/bin/knowdiskdrive-gui")
    assert desktop_mod.detect_exec_command() == "/usr/bin/knowdiskdrive-gui"


def test_detect_exec_command_falls_back_to_module(monkeypatch):
    monkeypatch.setattr(desktop_mod.shutil, "which", lambda name: None)
    command = desktop_mod.detect_exec_command()
    assert command.endswith("-m knowdiskdrive.gui")


def test_xdg_dirs_follow_env(xdg_home):
    assert desktop_mod.data_home() == xdg_home
    assert desktop_mod.applications_dir() == xdg_home / "applications"
    assert desktop_mod.icon_dir() == xdg_home / "icons" / "hicolor" / "scalable" / "apps"

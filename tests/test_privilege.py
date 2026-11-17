"""一次性授权（setcap）的单元测试。

不碰真实 /usr/sbin/smartctl，也不真的弹密码框：能力位图用 getxattr 的桩，
提权过程用 subprocess.run 的桩。真机上的端到端验证放在 tools/acceptance.py。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knowdiskdrive import privilege as priv


# --------------------------------------------------------------------------
# 能力位图解析
# --------------------------------------------------------------------------
def _cap_blob(*caps: int) -> bytes:
    """按 capabilities(7) 的 vfs_cap_data 布局造一段 security.capability。"""
    permitted = 0
    for cap in caps:
        permitted |= 1 << cap
    low = permitted & 0xFFFFFFFF
    high = (permitted >> 32) & 0xFFFFFFFF
    magic = 0x02000000 | 0x1  # rev2 + effective 位
    return (
        magic.to_bytes(4, "little")
        + low.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + high.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )


def test_read_permitted_caps_decodes_bitmap(monkeypatch):
    monkeypatch.setattr(
        priv.os, "getxattr", lambda path, name: _cap_blob(priv.CAP_SYS_RAWIO, priv.CAP_DAC_READ_SEARCH)
    )
    bits = priv.read_permitted_caps("/usr/sbin/smartctl")
    assert bits is not None
    assert bits & (1 << priv.CAP_SYS_RAWIO)
    assert bits & (1 << priv.CAP_DAC_READ_SEARCH)
    assert not bits & (1 << priv.CAP_SYS_ADMIN)


def test_read_permitted_caps_returns_none_without_xattr(monkeypatch):
    def boom(path, name):
        raise OSError("No data available")

    monkeypatch.setattr(priv.os, "getxattr", boom)
    assert priv.read_permitted_caps("/usr/sbin/smartctl") is None


def test_read_permitted_caps_rejects_garbage(monkeypatch):
    monkeypatch.setattr(priv.os, "getxattr", lambda path, name: b"\x01\x02")
    assert priv.read_permitted_caps("/usr/sbin/smartctl") is None


def test_is_configured_needs_every_required_cap(monkeypatch):
    # 只授了 SATA 需要的能力：完整授权还差 cap_sys_admin
    monkeypatch.setattr(priv.os, "getxattr", lambda *a: _cap_blob(priv.CAP_SYS_RAWIO, priv.CAP_DAC_READ_SEARCH))
    assert priv.is_configured("/usr/sbin/smartctl") is False
    assert priv.is_configured("/usr/sbin/smartctl", no_nvme=True) is True


def test_is_configured_false_when_xattr_missing(monkeypatch):
    monkeypatch.setattr(priv.os, "getxattr", lambda *a: (_ for _ in ()).throw(OSError()))
    assert priv.is_configured("/usr/sbin/smartctl") is False


# --------------------------------------------------------------------------
# 命令构造
# --------------------------------------------------------------------------
def test_setcap_command_full_and_sata_only():
    full = priv.setcap_command("/usr/sbin/smartctl")
    assert full == ["setcap", priv.CAPSET_FULL, "/usr/sbin/smartctl"]
    sata = priv.setcap_command("/usr/sbin/smartctl", no_nvme=True)
    assert sata[1] == priv.CAPSET_SATA_ONLY
    assert "cap_sys_admin" not in sata[1]


def test_manual_command_is_copy_pasteable():
    text = priv.manual_command("/usr/sbin/smartctl", no_nvme=True)
    assert text.startswith("sudo setcap ")
    assert text.endswith("/usr/sbin/smartctl")


def test_required_caps_drops_sys_admin_for_sata_only():
    assert priv.CAP_SYS_ADMIN not in priv.required_caps(no_nvme=True)
    assert priv.CAP_SYS_ADMIN in priv.required_caps()


# --------------------------------------------------------------------------
# 后端选择
# --------------------------------------------------------------------------
def test_backend_order_prefers_pkexec_in_gui(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    assert priv._backend_order("gui") == ["pkexec", "sudo-askpass", "sudo-tty"]


def test_backend_order_prefers_sudo_in_terminal(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    assert priv._backend_order("terminal") == ["sudo-tty", "sudo-askpass", "pkexec"]


def test_backend_order_skips_missing_tools(monkeypatch):
    monkeypatch.setattr(
        priv.shutil, "which", lambda name: "/usr/bin/sudo" if name == "sudo" else None
    )
    assert priv._backend_order("gui") == ["sudo-askpass", "sudo-tty"]


def test_available_backends_empty_without_tools(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: None)
    assert priv.available_backends() == []


def test_has_gui_session_checks_display_vars(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert priv._has_gui_session() is False
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert priv._has_gui_session() is True


# --------------------------------------------------------------------------
# askpass 脚本
# --------------------------------------------------------------------------
def test_build_askpass_writes_executable_script(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    made = priv._build_askpass()
    assert made is not None
    script, directory = made
    try:
        assert script.exists()
        assert script.stat().st_mode & 0o100
        body = script.read_text(encoding="utf-8")
        assert body.startswith("#!/bin/sh")
        assert "zenity" in body
    finally:
        priv.shutil.rmtree(directory, ignore_errors=True)


def test_build_askpass_returns_none_without_tool(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: None)
    assert priv._build_askpass() is None


# --------------------------------------------------------------------------
# 提权执行
# --------------------------------------------------------------------------
def _completed(rc: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=rc, stdout="", stderr=stderr)


def test_run_elevated_reports_missing_setcap(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: None)
    result = priv.run_elevated(["setcap", "x", "/usr/sbin/smartctl"])
    assert result.ok is False
    assert "setcap" in result.message


def test_run_elevated_succeeds_via_pkexec(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _completed(0)

    monkeypatch.setattr(priv.subprocess, "run", fake_run)
    result = priv.run_elevated(["setcap", "caps", "/usr/sbin/smartctl"], prefer="gui")
    assert result.ok is True
    assert result.backend == "pkexec"
    assert calls[0][0] == "pkexec"


def test_run_elevated_falls_back_when_no_polkit_agent(monkeypatch):
    """pkexec 返回 127（没有认证代理）时应换 sudo 再试一次。"""
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(priv, "_build_askpass", lambda: None)  # 让 sudo-askpass 也跳过
    seen: list[str] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        if cmd[0] == "pkexec":
            return _completed(127, "Error executing command as another user: Not authorized")
        return _completed(0)

    monkeypatch.setattr(priv.subprocess, "run", fake_run)
    result = priv.run_elevated(["setcap", "caps", "/usr/sbin/smartctl"], prefer="gui")
    assert result.ok is True
    assert result.backend == "sudo"
    assert seen == ["pkexec", "sudo"]


def test_run_elevated_stops_when_user_cancels(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    seen: list[str] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        return _completed(126)

    monkeypatch.setattr(priv.subprocess, "run", fake_run)
    result = priv.run_elevated(["setcap", "caps", "/usr/sbin/smartctl"], prefer="gui")
    assert result.ok is False
    assert result.returncode == 126
    assert "取消" in result.message
    assert seen == ["pkexec"]  # 不接连弹第二个密码框


def test_run_elevated_reports_auth_failure(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(priv.subprocess, "run", lambda cmd, **kw: _completed(1, "Sorry, try again."))
    result = priv.run_elevated(["setcap", "caps", "/usr/sbin/smartctl"], prefer="terminal")
    assert result.ok is False
    assert "Sorry" in result.message


def test_run_elevated_handles_timeout(monkeypatch):
    monkeypatch.setattr(priv.shutil, "which", lambda name: "/usr/bin/" + name)

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(priv.subprocess, "run", boom)
    result = priv.run_elevated(["setcap", "caps", "/usr/sbin/smartctl"], prefer="terminal")
    assert result.ok is False
    assert "超时" in result.message


# --------------------------------------------------------------------------
# 一站式
# --------------------------------------------------------------------------
def test_apply_capabilities_skips_when_already_configured(monkeypatch, tmp_path):
    target = tmp_path / "smartctl"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(priv, "is_configured", lambda *a, **kw: True)
    result = priv.apply_capabilities(target)
    assert result.ok is True
    assert result.backend == "cached"


def test_apply_capabilities_reports_missing_target(tmp_path):
    result = priv.apply_capabilities(tmp_path / "nope")
    assert result.ok is False
    assert "找不到" in result.message


def test_apply_capabilities_calls_run_elevated(monkeypatch, tmp_path):
    target = tmp_path / "smartctl"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(priv, "is_configured", lambda *a, **kw: False)
    seen: dict[str, object] = {}

    def fake_elevated(command, prefer="auto", timeout=180):
        seen["command"] = command
        seen["prefer"] = prefer
        return priv.ElevationResult(ok=True, backend="pkexec", command=list(command))

    monkeypatch.setattr(priv, "run_elevated", fake_elevated)
    result = priv.apply_capabilities(target, prefer="gui")
    assert result.ok is True
    assert seen["prefer"] == "gui"
    assert seen["command"][0] == "setcap"
    assert "授予读取权限" in result.message


def test_summary_lines_mentions_backends(monkeypatch):
    monkeypatch.setattr(priv, "available_backends", lambda: ["pkexec"])
    lines = priv.summary_lines()
    assert any("pkexec" in line for line in lines)

"""打包产物的完整性测试（AppImage runtime 头）。

背景：pack/appimage-runtime-head.bin 是随包固化的官方 runtime-x86_64。
如果它是从某个现成 AppImage 里切头得来的，会把对方的 .upd_info（更新源）
和 .digest_md5（包摘要）一起带进来，产物就会声称「我的更新源是别人的发布」——
用户跑 AppImageUpdate 时会下载别人的文件覆盖本程序。这里把这几个段钉成空。
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "pack" / "appimage-runtime-head.bin"

# AppImage 规范里承载更新源/摘要/签名的段，一律不得有内容
METADATA_SECTIONS = (b".digest_md5", b".upd_info", b".sha256_sig", b".sig_key")

EXPECTED_SHA256 = "66f5b22f035022b8bdebb54c066aa6edc7b5db282fe6cdb372e7965f80772557"


def _sections(raw: bytes) -> dict[bytes, tuple[int, int]]:
    """解析 ELF64 小端段表，返回 {段名: (偏移, 长度)}。"""
    e_shoff = struct.unpack_from("<Q", raw, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 0x3A)
    sh = e_shoff + e_shstrndx * e_shentsize
    stroff = struct.unpack_from("<Q", raw, sh + 0x18)[0]
    strsz = struct.unpack_from("<Q", raw, sh + 0x20)[0]
    strtab = raw[stroff : stroff + strsz]
    out: dict[bytes, tuple[int, int]] = {}
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name_off = struct.unpack_from("<I", raw, off)[0]
        name = strtab[name_off : strtab.find(b"\0", name_off)]
        out[name] = (
            struct.unpack_from("<Q", raw, off + 0x18)[0],
            struct.unpack_from("<Q", raw, off + 0x20)[0],
        )
    return out


def test_runtime_head_is_the_official_binary():
    """runtime 头必须与官方 runtime-x86_64 逐字节一致（不被就地改过）。"""
    assert RUNTIME.is_file(), "缺少 pack/appimage-runtime-head.bin"
    digest = hashlib.sha256(RUNTIME.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256, (
        f"runtime 头被改过：sha256={digest}，期望 {EXPECTED_SHA256}"
    )


def test_runtime_head_has_no_inherited_appimage_metadata():
    """更新源/摘要/签名段必须为空，否则产物会指向别人的发布。"""
    raw = RUNTIME.read_bytes()
    sections = _sections(raw)
    for name in METADATA_SECTIONS:
        assert name in sections, f"段 {name!r} 不存在，ELF 结构不对"
        off, size = sections[name]
        if not size:
            continue
        payload = raw[off : off + size]
        assert not any(payload), (
            f"段 {name.decode()} 有内容: {payload[:120]!r}"
            f"——这会把别人的更新源/签名带进产物"
        )


def test_runtime_head_is_a_bare_runtime_not_a_full_appimage():
    """纯 runtime 头：段表末尾就是文件末尾，不带 squashfs。"""
    raw = RUNTIME.read_bytes()
    e_shoff = struct.unpack_from("<Q", raw, 0x28)[0]
    e_shentsize, e_shnum = struct.unpack_from("<HH", raw, 0x3A)
    assert len(raw) == e_shoff + e_shnum * e_shentsize

# ---------- glibc 下限检查（pack/glibc_check.py） ----------
# 为什么要钉：PyInstaller 会把构建机的**系统库**（libglib-2.0.so.0、
# liblcms2.so.2、libstdc++.so.6 …）一起收进产物，于是「产物能在多老的机器上跑」
# 由构建环境决定，不由我们决定。曾经在 Ubuntu 26.04（glibc 2.43）上打包，产物
# 在 Ubuntu 24.04（2.39）上直接 ImportError: version 'GLIBC_2.43' not found。

import sys as _sys

_pack = Path(__file__).resolve().parent.parent / "pack"
_sys.path.insert(0, str(_pack))
import glibc_check  # noqa: E402 - 依赖上面的 sys.path 注入


def test_parse_target_accepts_dotted_and_bare_versions():
    assert glibc_check.parse_target("2.35") == (2, 35)
    assert glibc_check.parse_target("2") == (2, 0)
    assert glibc_check.parse_target(" 2.28 ") == (2, 28)


def test_version_key_orders_numerically_not_lexically():
    # 字符串比较会把 "2.9" 排在 "2.35" 之后，这会让 2.9 被误判成更高版本
    assert glibc_check.version_key("GLIBC_2.9") < glibc_check.version_key("GLIBC_2.35")
    assert glibc_check.version_key("GLIBC_2.43") > glibc_check.version_key("GLIBC_2.35")
    assert glibc_check.version_key("不是版本号") == (0, 0)


def test_scan_finds_version_needs_in_a_real_binary():
    """拿当前解释器当样本：它是 ELF，一定带 GLIBC 版本需求。"""
    report = glibc_check.scan(Path(_sys.executable))
    assert report["max"] is not None
    assert report["max"].startswith("GLIBC_")
    assert report["files"], "至少应该扫到一个 ELF"


def test_scan_ignores_non_elf_files(tmp_path):
    text = tmp_path / "not-elf.txt"
    text.write_text("这不是 ELF\n", encoding="utf-8")
    report = glibc_check.scan(tmp_path)
    assert report["files"] == {}
    assert report["max"] is None


def test_main_fails_when_target_is_lower_than_needed(capsys):
    """目标定成一个不可能达到的低版本，必须失败并指明是哪个文件。"""
    code = glibc_check.main(["-", str(_sys.executable), "--target", "2.0"])
    out = capsys.readouterr().out
    assert code == 1
    assert "失败" in out
    assert "GLIBC_" in out


def test_main_passes_when_target_is_high_enough(capsys):
    report = glibc_check.scan(Path(_sys.executable))
    major, minor = glibc_check.version_key(report["max"])
    code = glibc_check.main(["-", str(_sys.executable), "--target", f"{major}.{minor}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "通过" in out


def test_scan_skips_symlinks(tmp_path):
    """符号链接不重复计数：同一个库在 _internal 里常有好几个名字。"""
    real = tmp_path / "libfoo.so.1"
    real.write_bytes(Path(_sys.executable).read_bytes()[:64] + b"\0" * 4096)
    (tmp_path / "libfoo.so").symlink_to(real)
    report = glibc_check.scan(tmp_path)
    # 链接不参与；截断过的 ELF 解析失败也不该抛异常
    assert not any(p.endswith("libfoo.so") for p in report["files"])


# ---------- 构建脚本必须守住 glibc 门槛 ----------


def test_inner_build_script_checks_glibc_before_packaging():
    """构建脚本要在打包后立刻检查产物，而不是等用户装到老系统上才发现。"""
    script = (Path(__file__).resolve().parent.parent / "pack" / "build-appimage-inner.sh")
    text = script.read_text(encoding="utf-8")
    assert "pack/glibc_check.py" in text
    assert "KDD_GLIBC_TARGET" in text
    assert "exit 1" in text


def test_wrapper_builds_inside_ubuntu_2204_rootfs():
    """外层脚本必须真的在 22.04 rootfs 里构建，否则 glibc 下限还是构建机的。"""
    script = (Path(__file__).resolve().parent.parent / "pack" / "build-appimage.sh")
    text = script.read_text(encoding="utf-8")
    assert "ubuntu-base-22.04" in text
    assert "242cd8898b33ea806ef5f13b1076ed7c76f9f989d18384452f7166692438ff1a" in text
    assert "bwrap" in text
    assert "build-appimage-inner.sh" in text


# ---------- PySide6 版本门槛（PYSIDE-3424 / PYSIDE-3474） ----------
# 为什么要钉：PySide6 6.12.0 在 Python < 3.12 上会改坏 Qt 单例的引用计数，
# 普通 Qt 调用就能让进程在退出时 "Fatal Python error: none_dealloc:
# deallocating None" + SIGABRT。AppImage 里是 Python 3.10，所以必须守住 6.11.x。


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_pyproject() -> dict:
    try:  # Python 3.11+
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    return tomllib.loads((_project_root() / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_excludes_broken_pyside_on_old_python():
    """Python < 3.12 的依赖必须带上 <6.12 上界，3.12+ 才可以不设上界。"""
    deps = _load_pyproject()["project"]["dependencies"]
    old = [d for d in deps if d.startswith("PySide6") and "python_version < '3.12'" in d]
    new = [d for d in deps if d.startswith("PySide6") and "python_version >= '3.12'" in d]
    assert old, f"缺少 Python<3.12 的 PySide6 约束：{deps}"
    assert "<6.12" in old[0], f"Python<3.12 的 PySide6 没有排除 6.12：{old[0]}"
    assert new, f"缺少 Python>=3.12 的 PySide6 依赖：{deps}"


def test_appimage_build_pins_safe_pyside():
    """打包脚本里的 PYSIDE_VERSION 必须是 6.11.x（rootfs 是 Python 3.10）。"""
    text = (_project_root() / "pack" / "build-appimage.sh").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("PYSIDE_VERSION="))
    version = line.split("=", 1)[1].strip()
    assert version.startswith("6.11."), f"PYSIDE_VERSION={version} 会触发 PYSIDE-3474"


def test_binding_guard_flags_broken_combination():
    """运行时兜底检查：能识别会崩的组合，不误报安全的组合。"""
    _sys.path.insert(0, str(_project_root()))
    from knowdiskdrive.gui.app import pyside_binding_problem

    assert pyside_binding_problem("3.10.12", "6.12.0")
    assert pyside_binding_problem("3.11.9", "6.12.1")
    assert pyside_binding_problem("3.10.12", "6.11.3") is None
    assert pyside_binding_problem("3.12.4", "6.12.0") is None


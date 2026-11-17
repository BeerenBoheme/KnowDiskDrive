"""扫描 ELF 需要的最高 GLIBC 版本，用来验证打包产物的兼容下限。

为什么需要：PyInstaller 在构建机上会自动把**系统库**（libglib-2.0.so.0、
liblcms2.so.2、libstdc++.so.6 等）收集进产物。这些库是构建机的发行版编译的，
于是产物的真实 glibc 下限被构建机决定，而不是被「我们希望它多低」决定。
在 Ubuntu 26.04（glibc 2.43）上打包，产物放到 Ubuntu 24.04（2.39）上就报：

    version 'GLIBC_2.43' not found (required by .../_internal/libglib-2.0.so.0)

这个模块直接解析 ELF 的 .gnu.version_r 与 .dynstr，不依赖 objdump/readelf，
因此可以在任何机器上检查别的架构/别的发行版的产物。

命令行用法：

    python3 pack/glibc_check.py <目录或文件> [--target 2.35]

超出 --target 时退出码 1，并把「哪个文件、需要什么版本」打出来。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
SHT_GNU_VERNEED = 0x6FFFFFFE
ELFCLASS64 = 2
ELFDATA2LSB = 1


class ElfError(ValueError):
    """不是可解析的 ELF64 小端文件。"""


def is_elf(path: Path) -> bool:
    """只看魔数，避免把大文件整个读进内存。"""
    try:
        with path.open("rb") as handle:
            return handle.read(4) == ELF_MAGIC
    except OSError:
        return False


def _u16(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<H", raw, offset)[0]


def _u32(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _u64(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", raw, offset)[0]


def _sections(raw: bytes) -> list[dict]:
    """解段表。返回 [{name, type, offset, size, link}]，按段表顺序。"""
    if len(raw) < 0x40 or raw[:4] != ELF_MAGIC:
        raise ElfError("不是 ELF")
    if raw[4] != ELFCLASS64:
        raise ElfError("只支持 ELF64")
    if raw[5] != ELFDATA2LSB:
        raise ElfError("只支持小端")
    e_shoff = _u64(raw, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 0x3A)
    if not e_shoff or not e_shnum:
        return []
    shstr_hdr = e_shoff + e_shstrndx * e_shentsize
    str_off = _u64(raw, shstr_hdr + 0x18)
    str_size = _u64(raw, shstr_hdr + 0x20)
    strtab = raw[str_off : str_off + str_size]
    out = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + e_shentsize > len(raw):
            break
        name_off = _u32(raw, off)
        end = strtab.find(b"\0", name_off)
        name = strtab[name_off : end if end >= 0 else len(strtab)]
        out.append(
            {
                "name": name,
                "type": _u32(raw, off + 0x04),
                "offset": _u64(raw, off + 0x18),
                "size": _u64(raw, off + 0x20),
                "link": _u32(raw, off + 0x28),
            }
        )
    return out


def _cstr(blob: bytes, offset: int) -> str:
    end = blob.find(b"\0", offset)
    chunk = blob[offset : end if end >= 0 else len(blob)]
    return chunk.decode("utf-8", "replace")


def read_required_versions(path: Path) -> dict[str, str]:
    """返回 {GLIBC 版本: 库里引用的第一个符号名}，读的是 .gnu.version_r。

    只收 GLIBC 前缀：同一个段里还有 GLIBCXX_/CXXABI_/GCC_，它们由 libstdc++
    的版本决定，不构成 glibc 下限。
    """
    raw = Path(path).read_bytes()
    sections = _sections(raw)
    if not sections:
        return {}
    found: dict[str, str] = {}
    for section in sections:
        if section["type"] != SHT_GNU_VERNEED or not section["size"]:
            continue
        link = section["link"]
        if link >= len(sections):
            continue
        dynstr_hdr = sections[link]
        dynstr = raw[dynstr_hdr["offset"] : dynstr_hdr["offset"] + dynstr_hdr["size"]]
        base = section["offset"]
        end = base + section["size"]
        pos = base
        while pos + 16 <= end and pos + 16 <= len(raw):
            vn_next = _u32(raw, pos + 12)
            aux = pos + _u32(raw, pos + 8)
            while aux + 16 <= end and aux + 16 <= len(raw):
                name = _cstr(dynstr, _u32(raw, aux + 8))
                if name.startswith("GLIBC_"):
                    found.setdefault(name, _cstr(dynstr, 0) or "?")
                vna_next = _u32(raw, aux + 12)
                if not vna_next:
                    break
                aux += vna_next
            if not vn_next:
                break
            pos += vn_next
    return found


def version_key(name: str) -> tuple[int, int]:
    """'GLIBC_2.43' -> (2, 43)；解析不了返回 (0, 0)，排在最前。"""
    tail = name.rpartition("_")[2]
    major, _, minor = tail.partition(".")
    try:
        return int(major), int(minor)
    except ValueError:
        return 0, 0


def parse_target(text: str) -> tuple[int, int]:
    """'2.35' -> (2, 35)。"""
    major, _, minor = text.strip().partition(".")
    try:
        return int(major), int(minor or 0)
    except ValueError as exc:
        raise ValueError(f"无法解析版本号: {text!r}") from exc


def iter_elf_files(root: Path):
    """递归找 ELF 文件。跳过符号链接，避免同一个库被数两遍。"""
    root = Path(root)
    if root.is_file():
        if is_elf(root):
            yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if is_elf(path):
            yield path


def scan(root: Path) -> dict:
    """扫描目录，汇总每个文件以及整体需要的最高 GLIBC 版本。

    返回 {"files": {路径: {版本: 符号}}, "max": 最高版本或 None,
          "worst": 需要最高版本的那个文件}
    """
    files: dict[str, dict[str, str]] = {}
    for path in iter_elf_files(root):
        try:
            versions = read_required_versions(path)
        except (ElfError, struct.error, OSError):
            continue
        if not versions:
            continue
        files[str(path)] = versions
    best = None
    worst = None
    for name, versions in files.items():
        for version in versions:
            if best is None or version_key(version) > version_key(best):
                best, worst = version, name
    return {"files": files, "max": best, "worst": worst}


def main(argv: list[str]) -> int:
    target = None
    args = list(argv[1:])
    if "--target" in args:
        index = args.index("--target")
        target = parse_target(args[index + 1])
        del args[index : index + 2]
    if not args:
        print("用法: python3 pack/glibc_check.py <目录或文件> [--target 2.35]", file=sys.stderr)
        return 2
    root = Path(args[0])
    if not root.exists():
        print(f"路径不存在: {root}", file=sys.stderr)
        return 2
    report = scan(root)
    files = report["files"]
    summary: dict[str, int] = {}
    for versions in files.values():
        for version in versions:
            summary[version] = summary.get(version, 0) + 1
    print(f"[glibc] 扫描 {root}：{len(files)} 个 ELF 带版本需求")
    for version in sorted(summary, key=version_key, reverse=True):
        print(f"  {version:<12} {summary[version]:>3} 个文件")
    if report["max"] is None:
        print("[glibc] 没有任何 GLIBC 版本需求（静态库或空目录？）")
        return 0
    print(f"[glibc] 最高需求 {report['max']}，来自 {report['worst']}")
    if target is not None and version_key(report["max"]) > target:
        print(
            f"[glibc] 失败：最高需要 {report['max']}，"
            f"超过目标 {target[0]}.{target[1]}"
        )
        flagged = sorted(
            ((version_key(max(v, key=version_key)), n, max(v, key=version_key)) for n, v in files.items()),
            reverse=True,
        )
        for key, name, peak in flagged[:10]:
            if key > target:
                print(f"    {name}: {peak}")
        return 1
    if target is not None:
        print(f"[glibc] 通过：不高于目标 {target[0]}.{target[1]}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main(sys.argv))

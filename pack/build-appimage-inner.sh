#!/bin/sh
# build-appimage-inner.sh — 真正干活的构建脚本，在「构建环境」里执行一次。
#
# 一般不要直接调用，用 pack/build-appimage.sh：它会把本脚本挂进 Ubuntu 22.04
# rootfs 里跑，从而把产物的 glibc 下限钉在 2.35。直接在本机跑也行，但产物下限
# 会跟着本机走，第 3 步的检查会因此失败——这是故意的。
#
# 环境变量（都可省略）：
#   KDD_PYINSTALLER    PyInstaller 可执行文件，默认 .venv/bin/pyinstaller
#   KDD_GLIBC_TARGET   允许的最高 glibc 版本，默认 2.35
#   KDD_SQUASH_PROCS   mksquashfs 并行度，默认 4
#
# 为什么不用 appimagetool：本机没有（也不一定联网），方案是取官方 AppImageKit
# 的 ELF runtime 头，拼上自己 mksquashfs 生成的 squashfs，得到标准 type-2 AppImage。
#
# runtime 头是随包固化的官方 runtime-x86_64（pack/appimage-runtime-head.bin，
# 193728 字节，sha256 66f5b22f035022b8bdebb54c066aa6edc7b5db282fe6cdb372e7965f80772557），
# 来自 https://github.com/AppImage/AppImageKit/releases/download/continuous/runtime-x86_64
# （runtime 版本串 Version: 5735cc5）。别从某个现成 AppImage 里切头代用：那样会把
# 人家的 .upd_info（更新源）和 .digest_md5 一起带过来，见第 1 步的清零断言。
#
# 产物：dist/<版本>/knowdiskdrive-<版本>-x86_64.AppImage
#
# 注意：本脚本刻意只用 set -e 而不用 set -u，靠 test 判断变量是否给值。
set -e

cd "$(dirname "$0")/.."

# 未传参时从 pyproject.toml 读版本，避免硬编码漂移
if [ "$#" -ge 1 ]; then
    VERSION="$1"
else
    VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)"
fi
test -n "$VERSION" || { echo "无法从 pyproject.toml 确定版本号" >&2; exit 1; }

PYINSTALLER=".venv/bin/pyinstaller"
if [ -n "$KDD_PYINSTALLER" ]; then PYINSTALLER="$KDD_PYINSTALLER"; fi
GLIBC_TARGET="2.35"
if [ -n "$KDD_GLIBC_TARGET" ]; then GLIBC_TARGET="$KDD_GLIBC_TARGET"; fi
PROCS="4"
if [ -n "$KDD_SQUASH_PROCS" ]; then PROCS="$KDD_SQUASH_PROCS"; fi

BUILD=build/appimage
APPDIR="$BUILD/knowdiskdrive.AppDir"
RUNTIME_SRC=pack/appimage-runtime-head.bin
OUTDIR="dist/$VERSION"
OUT="$OUTDIR/knowdiskdrive-$VERSION-x86_64.AppImage"

command -v mksquashfs >/dev/null || { echo "缺 mksquashfs（apt install squashfs-tools）" >&2; exit 1; }
test -x "$PYINSTALLER" || { echo "找不到 PyInstaller: $PYINSTALLER" >&2; exit 1; }
test -f "$RUNTIME_SRC" || { echo "找不到 runtime 头: $RUNTIME_SRC" >&2; exit 1; }

mkdir -p "$BUILD" "$OUTDIR"

# ---------- 1) 校验 runtime 头 ----------
python3 - "$RUNTIME_SRC" "$BUILD/runtime-head.bin" <<'PY'
import struct
import sys

src, dst = sys.argv[1], sys.argv[2]
raw = bytearray(open(src, "rb").read())
assert raw[:4] == b"\x7fELF", src + " 不是 ELF"
e_shoff = struct.unpack_from("<Q", raw, 40)[0]
e_shentsize, e_shnum, _ = struct.unpack_from("<HHH", raw, 58)
end = e_shoff + e_shnum * e_shentsize
# 判定顺序关键：纯 runtime 头里也含 "hsqs" 字面串（squashfs 代码里的符号），
# 不能对全文件 find，先看段表末尾这个定点。
if len(raw) == end:
    cut = len(raw)
    print("[appimage] 输入是纯 runtime 头（%d 字节）" % cut)
elif raw[end:end + 4] == b"hsqs":
    cut = end
    print("[appimage] 输入是完整 AppImage，切出 runtime 头（%d 字节）" % cut)
else:
    cut = raw.find(b"hsqs", end)
    assert cut > 0, "段表末尾 %d 起找不到 squashfs superblock" % end
    print("[appimage] 注意: squashfs 起点 %d 与段表末尾 %d 有 %d 字节填充" % (cut, end, cut - end))

# 清零 AppImage 元数据段。若输入是别人打好的 AppImage，这几段里带着对方的更新源
# （.upd_info 形如 gh-releases-zsync|owner|repo|tag|文件名.zsync）和包摘要/签名，
# 不清掉的话我们的产物会声称「我的更新源是别人的发布」——AppImageUpdate 会去
# 下载别人的文件覆盖本程序。官方纯 runtime 里这几段本来就是空的，此步属防御。
META = {b".digest_md5", b".upd_info", b".sha256_sig", b".sig_key"}
e_shstrndx = struct.unpack_from("<H", raw, 62)[0]
_sh = e_shoff + e_shstrndx * e_shentsize
_stroff = struct.unpack_from("<Q", raw, _sh + 0x18)[0]
_strsz = struct.unpack_from("<Q", raw, _sh + 0x20)[0]
strtab = bytes(raw[_stroff:_stroff + _strsz])
cleared = []
for i in range(e_shnum):
    off = e_shoff + i * e_shentsize
    name_off = struct.unpack_from("<I", raw, off)[0]
    name = strtab[name_off:strtab.find(b"\0", name_off)]
    if name not in META:
        continue
    sh_off = struct.unpack_from("<Q", raw, off + 0x18)[0]
    sh_size = struct.unpack_from("<Q", raw, off + 0x20)[0]
    if not sh_size or sh_off + sh_size > cut:
        continue
    seg = raw[sh_off:sh_off + sh_size]
    if any(seg):
        raw[sh_off:sh_off + sh_size] = b"\0" * sh_size
        cleared.append(name.decode())
if cleared:
    print("[appimage] 已清空继承的元数据段: " + ", ".join(cleared))
del end
open(dst, "wb").write(bytes(raw[:cut]))
PY

# ---------- 2) PyInstaller onedir ----------
echo "[appimage] PyInstaller 打包（onedir）..."
rm -rf dist/knowdiskdrive
if ! "$PYINSTALLER" --noconfirm --clean --distpath "$BUILD/pyi-dist" \
        --workpath "$BUILD/pyi-build" pack/knowdiskdrive.spec \
        >"$BUILD/pyinstaller.log" 2>&1; then
    echo "PyInstaller 失败，见 $BUILD/pyinstaller.log" >&2
    tail -20 "$BUILD/pyinstaller.log" >&2
    exit 1
fi
test -x "$BUILD/pyi-dist/knowdiskdrive/knowdiskdrive-gui" ||
    { echo "PyInstaller 产物里没有 knowdiskdrive-gui" >&2; exit 1; }

# ---------- 3) 检查 glibc 下限 ----------
# PyInstaller 会把构建机上的**系统库**（libglib-2.0.so.0、liblcms2.so.2、
# libstdc++.so.6 …）一起收进产物，它们的 ELF 版本需求就是构建机发行版的版本。
# 所以「产物能在多老的机器上跑」是构建环境决定的，必须在打包时卡住，不能等
# 用户装到 Ubuntu 24.04 上才报 version GLIBC_2.43 not found。
echo "[appimage] 检查产物 glibc 下限（目标 <= $GLIBC_TARGET）..."
if ! python3 pack/glibc_check.py "$BUILD/pyi-dist/knowdiskdrive" --target "$GLIBC_TARGET"; then
    echo "[appimage] 失败：产物要求的 glibc 高于 $GLIBC_TARGET。" >&2
    echo "[appimage] 用 pack/build-appimage.sh 构建（它在 Ubuntu 22.04 rootfs 里跑本脚本）。" >&2
    exit 1
fi

# ---------- 4) 组装 AppDir ----------
echo "[appimage] 组装 AppDir ..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
         "$APPDIR/usr/share/doc/knowdiskdrive"

# 整个 PyInstaller 目录（含 _internal/）原样搬到 usr/lib 下。
# 不放 usr/bin：那里放的是单个可执行文件，塞一个带 _internal 的树容易
# 被别的打包流程误当成普通命令处理。
cp -a "$BUILD/pyi-dist/knowdiskdrive" "$APPDIR/usr/lib/knowdiskdrive"

cp knowdiskdrive/data/knowdiskdrive.desktop "$APPDIR/"
cp knowdiskdrive/data/knowdiskdrive.svg "$APPDIR/"
cp knowdiskdrive/data/knowdiskdrive.desktop "$APPDIR/usr/share/applications/"
cp knowdiskdrive/data/knowdiskdrive.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
cp README.md "$APPDIR/usr/share/doc/knowdiskdrive/"
chmod 0644 "$APPDIR"/*.desktop "$APPDIR"/*.svg "$APPDIR/usr/share/doc/knowdiskdrive/README.md"

# AppRun：第一个参数是 CLI 子命令时自动走命令行前端，否则开界面。
# （冻结包里没有独立的 knowdiskdrive 脚本，两条命令共用同一个二进制。）
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# KnowDiskDrive AppRun：同一个二进制，按参数决定开界面还是走命令行。
HERE="$(dirname "$(readlink -f "$0")")"
APP="$HERE/usr/lib/knowdiskdrive/knowdiskdrive-gui"

if [ "$#" -eq 0 ]; then
    exec "$APP"                       # 无参数：开图形界面
fi

case "$1" in
    --version|-h|--help)
        exec "$APP" --cli "$@"        # 终端里问版本/帮助，不该弹窗
        ;;
    -*)
        exec "$APP" "$@"              # 其余以 - 开头的（如 Qt 的 -platform）交给界面
        ;;
    *)
        exec "$APP" --cli "$@"        # 裸词一律当子命令，拼错时由 argparse 报错
        ;;
esac
APPRUN
chmod 0755 "$APPDIR/AppRun"

# ---------- 5) squashfs + 拼接 runtime ----------
# 压缩必须用 xz 或 zlib：官方 runtime 只内置这两种解压器，
# zstd 会报 uses (null) compression ... supports only xz, zlib。
echo "[appimage] mksquashfs (xz, $PROCS 路) ..."
rm -f "$BUILD/squashfs.part"
mksquashfs "$APPDIR" "$BUILD/squashfs.part" -comp xz -b 1M \
    -processors "$PROCS" -noappend -all-root -no-xattrs -mkfs-time 0 -quiet

# 先写临时文件再 rename：直接覆盖正在运行的 AppImage 会报
# cannot create ...: Text file busy（那个 inode 还映射在进程里）。
# rename 是原子的，旧进程继续用旧 inode，新文件立刻生效。
cat "$BUILD/runtime-head.bin" "$BUILD/squashfs.part" > "$OUT.tmp"
chmod 0755 "$OUT.tmp"
mv -f "$OUT.tmp" "$OUT"
rm -f "$BUILD/squashfs.part"

echo "== 产物: $OUT ($(du -h "$OUT" | cut -f1))"

# ---------- 6) 冒烟：走 --appimage-extract，不需要 FUSE 和显示服务器 ----------
echo "[appimage] 冒烟测试 ..."
SMOKE="$BUILD/smoke"
rm -rf "$SMOKE" "$BUILD/squashfs-root"
mkdir -p "$SMOKE"
if (cd "$BUILD" && "$OLDPWD/$OUT" --appimage-extract >/dev/null 2>&1) &&
   test -f "$BUILD/squashfs-root/AppRun"; then
    mv "$BUILD/squashfs-root" "$SMOKE/root"
    echo "[appimage] extract 通过（runtime 与 squashfs 拼接布局正确）"
    # 冻结包自检：资源 / smartctl / 磁盘枚举
    KDD_SELFTEST=1 "$SMOKE/root/AppRun" || echo "[appimage] 警告: 自检返回非 0"
    # CLI 分发是否生效
    "$SMOKE/root/AppRun" --version || echo "[appimage] 警告: --version 未走 CLI 分支"
    # 组装后每个 ELF 再查一遍（防止后续步骤引入别的东西）
    python3 pack/glibc_check.py "$SMOKE/root/usr/lib/knowdiskdrive" --target "$GLIBC_TARGET"
else
    echo "[appimage] 警告: extract 冒烟失败，请手动运行 $OUT 检查"
fi

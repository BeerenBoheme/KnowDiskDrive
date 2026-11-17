#!/bin/sh
# build-appimage.sh [版本] — 在 Ubuntu 22.04 rootfs 内构建 type-2 AppImage
#
# 为什么要在 22.04 rootfs 里构建：PyInstaller 会把**构建机的系统库**
# （libglib-2.0.so.0、liblcms2.so.2、libstdc++.so.6 …）一起收进产物，于是产物
# 真实的 glibc 下限由构建机决定。直接在 Ubuntu 26.04（glibc 2.43）上打包，
# 产物放到 Ubuntu 24.04（2.39）上就报：
#
#     version `GLIBC_2.43' not found (required by .../_internal/libglib-2.0.so.0)
#
# 本脚本先准备一个 Ubuntu 22.04（glibc 2.35）的 base rootfs，装好 Python 3.10、
# PySide6、PyInstaller 和 squashfs-tools，再用 bwrap 把项目目录挂进去、在里面跑
# pack/build-appimage-inner.sh。产物因此只要求 glibc 2.35（Ubuntu 22.04 起）。
#
# 第一次运行会下载 rootfs 并 apt/pip 安装（约 5-10 分钟，看网速），之后复用；
# 缓存目录默认 ~/.cache/knowdiskdrive-build，可用 KDD_BUILD_CACHE 覆盖。
# 想强制重建 rootfs：删掉缓存目录里的 rootfs-2204 即可。
#
# 依赖（宿主机）：bubblewrap（bwrap）、curl 或 wget、tar。不需要 root。
#
# 产物：dist/<版本>/knowdiskdrive-<版本>-x86_64.AppImage
set -e

cd "$(dirname "$0")/.."
PROJECT="$PWD"

VERSION=""
if [ "$#" -ge 1 ]; then
    VERSION="$1"
else
    VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)"
fi
test -n "$VERSION" || { echo "无法从 pyproject.toml 确定版本号" >&2; exit 1; }

CACHE="$HOME/.cache/knowdiskdrive-build"
if [ -n "$KDD_BUILD_CACHE" ]; then CACHE="$KDD_BUILD_CACHE"; fi
ROOT="$CACHE/rootfs-2204"
TARBALL="$CACHE/ubuntu-base-22.04.5-base-amd64.tar.gz"
READY="$ROOT/.kdd-ready"

# 22.04.5 base rootfs：Ubuntu 官方镜像，经清华镜像下载
BASE_URL=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/22.04/release/ubuntu-base-22.04.5-base-amd64.tar.gz
BASE_SHA256=242cd8898b33ea806ef5f13b1076ed7c76f9f989d18384452f7166692438ff1a
APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/ubuntu/
PIP_INDEX=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
# 固定 PySide6 版本：它决定 Qt 库的 glibc 需求，也跟着宿主环境对齐，避免上游
# 悄悄升级导致产物既可复现又不可解释。
#
# 必须是 6.11.x，不能是 6.12.0：rootfs 里是 Python 3.10，而 PySide6 6.12 在
# Python < 3.12 上有引用计数缺陷（PYSIDE-3424 / PYSIDE-3474），退出时会
# "Fatal Python error: none_dealloc: deallocating None" + SIGABRT（Fedora 上
# 点完一键授权关窗口的崩溃，用 4 行最小例子即可复现）。
PYSIDE_VERSION=6.11.3

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

# 在 rootfs 里以 fake root 执行命令
enter() {
    bwrap --unshare-user --uid 0 --gid 0 \
        --bind "$ROOT" / \
        --dev /dev \
        --proc /proc \
        --tmpfs /tmp \
        --tmpfs /run \
        --ro-bind /sys /sys \
        --bind "$PROJECT" /work \
        --tmpfs /root \
        --setenv HOME /root \
        --setenv PATH /opt/kdd-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        --setenv LC_ALL C.UTF-8 \
        --setenv LANG C.UTF-8 \
        --setenv DEBIAN_FRONTEND noninteractive \
        --setenv PIP_INDEX_URL "$PIP_INDEX" \
        --setenv KDD_PYINSTALLER /opt/kdd-venv/bin/pyinstaller \
        --chdir /work \
        "$@"
}

bootstrap() {
    echo "[appimage] 准备 Ubuntu 22.04 构建环境（首次运行需下载，约 5-10 分钟）..."
    mkdir -p "$CACHE"
    if [ ! -f "$TARBALL" ]; then
        echo "[appimage] 下载 rootfs: $BASE_URL"
        fetch "$BASE_URL" "$TARBALL.part"
        mv -f "$TARBALL.part" "$TARBALL"
    fi
    echo "[appimage] 校验 rootfs sha256 ..."
    actual="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
    if [ "$actual" != "$BASE_SHA256" ]; then
        echo "rootfs 校验失败：$actual，期望 $BASE_SHA256" >&2
        echo "删掉 $TARBALL 后重跑即可重新下载。" >&2
        exit 1
    fi
    rm -rf "$ROOT"
    mkdir -p "$ROOT"
    tar -xzf "$TARBALL" -C "$ROOT"
    cp /etc/resolv.conf "$ROOT/etc/resolv.conf"

    # base rootfs 里 sources.list 指向 archive.ubuntu.com（且是 https 无证书），
    # 换成清华 http 镜像：国内直连 archive 太慢，且 https 会因缺 ca-certificates 失败。
    {
        echo "deb $APT_MIRROR jammy main restricted universe multiverse"
        echo "deb $APT_MIRROR jammy-updates main restricted universe multiverse"
        echo "deb $APT_MIRROR jammy-security main restricted universe multiverse"
    } > "$ROOT/etc/apt/sources.list"

    echo "[appimage] apt-get update ..."
    enter apt-get update -qq -o APT::Sandbox::User=root

    # 只装构建与运行时真正需要的东西：Python 工具链、Qt 运行库、squashfs。
    # 不装 gcc/g++（PySide6 与 PyInstaller 都有 wheel），省时间也省空间。
    echo "[appimage] apt-get install ..."
    enter apt-get install -y -qq --no-install-recommends -o APT::Sandbox::User=root \
        ca-certificates python3 python3-pip python3-venv python3-dev pkg-config \
        squashfs-tools file \
        libglib2.0-0 libfontconfig1 libfreetype6 libegl1 libgl1 libxkbcommon0 \
        libxkbcommon-x11-0 libdbus-1-3 libx11-6 libxext6 libxrender1 libxi6 \
        libsm6 libice6 libzstd1 libpcre2-8-0 libuuid1 libbz2-1.0 liblzma5 \
        libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 \
        libxcb-sync1 libxcb-shm0 libxcb-util1 libxau6 libxdmcp6 libbsd0 libmd0 \
        libsqlite3-0 libssl3 zlib1g libffi8 libexpat1 libreadline8 \
        libncursesw6 libtinfo6 libgcc-s1 libstdc++6 libcrypt1

    # fontconfig-config 的 postinst 会 chown 到 root:adm，fake root 下没有 adm 组会
    # 报错（dpkg 返回 1）。配置阶段重跑一次通常就过；不过就重试一次再说。
    enter sh -c "dpkg --configure -a >/dev/null 2>&1 || dpkg --configure -a >/dev/null 2>&1 || true"

    echo "[appimage] 建 venv 并安装 Python 依赖 ..."
    enter sh -c "python3 -m venv /opt/kdd-venv"
    enter sh -c "/opt/kdd-venv/bin/python -m pip install -q --upgrade pip setuptools wheel"
    enter sh -c "/opt/kdd-venv/bin/python -m pip install -q PySide6==$PYSIDE_VERSION pyudev pytest pytest-cov 'pyinstaller>=6.3' tomli"
    enter sh -c "/opt/kdd-venv/bin/python -c 'import PySide6, PyInstaller; print(\"[appimage] PySide6\", PySide6.__version__, \"PyInstaller\", PyInstaller.__version__)'"

    enter sh -c "ldd --version | head -1"
    touch "$ROOT/.kdd-ready"
    echo "[appimage] 构建环境就绪：$ROOT"
}

command -v bwrap >/dev/null || { echo "缺 bubblewrap（apt install bubblewrap）" >&2; exit 1; }

mkdir -p "$ROOT"
if [ ! -f "$READY" ]; then
    bootstrap
fi

echo "[appimage] 在 22.04 rootfs 内构建 v$VERSION ..."
enter /bin/sh /work/pack/build-appimage-inner.sh "$VERSION"

OUT="dist/$VERSION/knowdiskdrive-$VERSION-x86_64.AppImage"
test -f "$OUT" || { echo "构建结束但没看到产物 $OUT" >&2; exit 1; }
echo "== 产物: $OUT ($(du -h "$OUT" | cut -f1))"
echo "== sha256: $(sha256sum "$OUT" | cut -d' ' -f1)"

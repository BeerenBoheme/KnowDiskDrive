#!/bin/sh
# build-source.sh [版本] — 打源码压缩包（发布用）
#
# 产物：dist/<版本>/knowdiskdrive-<版本>-src.tar.gz
# 顶层目录是 knowdiskdrive-<版本>/，解包即得可构建的源码树。
#
# 排除的东西分三类：跑出来的（.venv / build / dist / .pytest_cache / .coverage）、
# 生成的（__pycache__ / *.egg-info）、以及本机特有的（.venv 里的路径记录）。
# tools/ 保留——那些是复现与截图脚本，属于开发工具而非产物。
set -eu

cd "$(dirname "$0")/.."
VERSION="${1:-$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)}"
test -n "$VERSION" || { echo "无法从 pyproject.toml 确定版本号" >&2; exit 1; }

NAME="knowdiskdrive-$VERSION"
STAGE="build/src/$NAME"
OUT="dist/$VERSION/$NAME-src.tar.gz"

rm -rf "$STAGE"
mkdir -p "$(dirname "$STAGE")" "dist/$VERSION"

command -v rsync >/dev/null || { echo "缺 rsync（apt install rsync）" >&2; exit 1; }
rsync -a \
    --exclude='.venv/' \
    --exclude='build/' \
    --exclude='dist/' \
    --exclude='.pytest_cache/' \
    --exclude='.coverage' \
    --exclude='__pycache__/' \
    --exclude='*.egg-info/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='kdd-crash-*.tar.gz' \
    --exclude='*.AppImage' \
    ./ "$STAGE/"

# 别把开发机上的虚拟环境路径或构建脚本的残留带出去
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

tar -czf "$OUT" -C "$(dirname "$STAGE")" "$NAME"

echo "== 产物: $OUT ($(du -h "$OUT" | cut -f1))"
echo "== 内容摘要:"
tar -tzf "$OUT" | sed -n '1,12p'
echo "   ... 共 $(tar -tzf "$OUT" | wc -l) 项"

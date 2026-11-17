#!/usr/bin/env bash
# KnowDiskDrive「一键授权」崩溃取证脚本
# 在出问题的那台机器（Fedora 44 / KDE Wayland）上运行，不需要 root。
#
#   bash kdd-crash.sh env       只收集环境，不改任何东西
#   bash kdd-crash.sh prepare   制造「权限不足」条件（写临时配置 + 无 cap 的 smartctl 副本）
#   bash kdd-crash.sh collect   崩溃后收集现场（core / journal / gdb 栈）并恢复配置
#
# 所有输出都在 $HOME/kdd-crash/ 下（可用 KDD_OUT 覆盖）。
# 关键前提：程序必须由你在桌面会话里手动启动，本脚本不负责拉起 GUI。

set -uo pipefail

OUT=${KDD_OUT:-$HOME/kdd-crash}
mkdir -p "$OUT"
TS=$(date +%Y%m%d-%H%M%S)
CFG_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/knowdiskdrive
CFG=$CFG_DIR/config.toml
NOCAP=/tmp/smartctl-nocap

die() { echo "错误：$*" >&2; exit 1; }

find_app() {
    if [ -n "${KDD_APP:-}" ] && [ -x "${KDD_APP}" ]; then printf '%s\n' "$KDD_APP"; return 0; fi
    local p
    for p in "$HOME"/Downloads/knowdiskdrive-*.AppImage "$HOME"/Desktop/knowdiskdrive-*.AppImage \
             "$HOME"/*.AppImage /tmp/knowdiskdrive-*.AppImage; do
        [ -x "$p" ] && { printf '%s\n' "$p"; return 0; }
    done
    return 1
}

APP=$(find_app) || die "找不到 knowdiskdrive 的 AppImage，请用 KDD_APP=/path/to/xxx.AppImage 指定"

# ---------------------------------------------------------------- env
cmd_env() {
    local f="$OUT/env-$TS.txt"
    {
        echo '### 系统'
        cat /etc/os-release 2>/dev/null
        echo; echo '### glibc / 内核'
        ldd --version 2>&1 | head -1
        uname -r
        echo; echo '### 图形会话'
        echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-<空>}"
        echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-<空>}"
        echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<空>}"
        echo "DISPLAY=${DISPLAY:-<空>}"
        echo "QT_QPA_PLATFORMTHEME=${QT_QPA_PLATFORMTHEME:-<空>}"
        echo; echo '### 相关软件包'
        rpm -q smartmontools polkit polkit-kde systemd kf6-kcrash gdb 2>&1
        rpm -qa 2>/dev/null | grep -Ei 'polkit|smartmontools|kcrash' | sort
        echo; echo '### smartctl'
        command -v smartctl || echo '<未找到>'
        smartctl --version 2>&1 | head -4
        echo "smartctl getcap: $(getcap "$(command -v smartctl)" 2>&1)"
        echo; echo '### pkexec / sudo'
        command -v pkexec || echo '<无 pkexec>'
        pkexec --version 2>&1 | head -2
        command -v sudo || echo '<无 sudo>'
        sudo --version 2>&1 | head -1
        echo; echo '### AppImage'
        echo "路径: $APP"
        ls -l "$APP"
        sha256sum "$APP"
        "$APP" --appimage-version 2>&1 | head -2
        echo; echo '### 最近 20 条 core'
        coredumpctl list --no-pager 2>&1 | tail -20
    } > "$f" 2>&1
    echo "已写入 $f"
}

# -------------------------------------------------------------- prepare
cmd_prepare() {
    local real
    real=$(command -v smartctl) || die "找不到 smartctl"
    cp -f "$real" "$NOCAP" || die "复制 smartctl 失败"
    chmod 0755 "$NOCAP"
    echo "已创建无能力位副本：$NOCAP（getcap 应输出为空）"
    getcap "$NOCAP" || true

    mkdir -p "$CFG_DIR"
    if [ -f "$CFG" ]; then
        cp -f "$CFG" "$OUT/config.toml.bak"
        echo "已备份原配置到 $OUT/config.toml.bak"
    else
        echo "原本没有配置文件（collect 时会删掉这个临时的）"
        : > "$OUT/config-was-absent"
    fi
    cat > "$CFG" <<EOF
# kdd-crash.sh 临时写入，用于制造「权限不足」；取证结束请运行 collect 恢复
[smartctl]
path = "$NOCAP"
EOF
    echo "已写入 $CFG"
    echo
    echo '---------------- 接下来请你手动操作 ----------------'
    echo "1) 打开桌面上的终端，执行（stderr 会留在文件里）："
    echo "   cd \$(dirname \"$APP\") && ./\$(basename \"$APP\") 2>&1 | tee \"$OUT/app-stderr.log\""
    echo '2) 等窗口出来，顶部应显示红色的「读取 SMART 需要一次性授权」横幅'
    echo '3) 点右侧「一键授权」，弹出的密码框里输入密码'
    echo '4) 观察：密码框关闭后程序是否崩溃 / 终端是否出现 Fatal Python error / Assertion failed'
    echo '5) 无论崩没崩，回来执行：bash kdd-crash.sh collect'
    echo '----------------------------------------------------'
}

# -------------------------------------------------------------- collect
cmd_collect() {
    if [ -f "$OUT/config.toml.bak" ]; then
        cp -f "$OUT/config.toml.bak" "$CFG"; echo "已恢复原配置"
    else
        rm -f "$CFG"; echo "已删除临时配置（原本没有配置文件）"
    fi

    local line pid
    line=$(coredumpctl list --no-pager 2>/dev/null | grep -i 'knowdiskdrive' | tail -1)
    if [ -n "$line" ]; then
        pid=$(coredumpctl list --no-pager --json=short 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(1)
rows = [r for r in rows if "knowdiskdrive" in (r.get("exe") or "")]
print(rows[-1]["pid"] if rows else "")
' 2>/dev/null)
        # 退回按列位置取：TIME(Wed 日期 时间 时区) PID UID GID SIG COREFILE EXE
        [ -z "${pid:-}" ] && pid=$(printf '%s\n' "$line" | awk '{print $(NF-5)}')
        echo "最新 core 记录：$line"
        echo "推测 PID=$pid"
        coredumpctl info --no-pager "$pid" > "$OUT/coredump-$pid.txt" 2>&1 \
            || coredumpctl info "$pid" > "$OUT/coredump-$pid.txt" 2>&1 \
            || echo 'coredumpctl info 失败（可能需要 sudo）' > "$OUT/coredump-$pid.txt"
        echo "已写入 $OUT/coredump-$pid.txt"

        if coredumpctl dump --no-pager "$pid" --output="$OUT/core.$pid" >/dev/null 2>&1; then
            echo "已导出 core：$OUT/core.$pid"
            if command -v gdb >/dev/null 2>&1; then
                gdb -batch -nx \
                    -ex 'info sharedlibrary' \
                    -ex 'thread apply all bt' \
                    -c "$OUT/core.$pid" > "$OUT/gdb-$pid.txt" 2>&1
                echo "已写入 gdb 栈：$OUT/gdb-$pid.txt"
            else
                echo '未安装 gdb，跳过符号化栈（coredumpctl info 已含系统栈）'
            fi
        else
            echo '导出 core 失败（可能需要 sudo coredumpctl dump）'
        fi
    else
        echo 'coredumpctl 里没有 knowdiskdrive 的记录' > "$OUT/coredump-none.txt"
    fi

    journalctl --user -b --no-pager -o short-precise > "$OUT/journal-user.log" 2>&1
    echo "已写入 $OUT/journal-user.log"

    if [ -f "$OUT/app-stderr.log" ]; then
        echo '--- app-stderr.log 里与崩溃相关的行 ---'
        grep -nE 'Fatal Python|Assertion|Traceback|Aborted|Segmentation|core dumped|Error|error' \
            "$OUT/app-stderr.log" | tail -40 || echo '（没匹配到）'
    else
        echo '没有找到 app-stderr.log（程序可能不是从终端启动的，stderr 只在 journal 里）'
    fi

    echo
    echo "全部产物在：$OUT"
    ls -l "$OUT"
}

case "${1:-}" in
    env)     cmd_env ;;
    prepare) cmd_prepare ;;
    collect) cmd_collect ;;
    *)       echo "用法: bash $0 env|prepare|collect"; echo "AppImage: $APP"; exit 2 ;;
esac

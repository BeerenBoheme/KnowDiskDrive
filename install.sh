#!/usr/bin/env bash
# KnowDiskDrive 一次性权限配置脚本
#
# 用法：sudo ./install.sh            # 完整安装（需要 root：setcap + 菜单项）
#       ./install.sh --user-only      # 只装菜单项，不需要 root（自测用）
#       ./install.sh --no-nvme        # 只授 SATA 需要的能力，NVMe 将读不到 SMART
#
# 作用（提示词第 6 节，四项）：
#   1. 检查 smartctl 是否存在，不存在则提示安装方式并退出；
#   2. 给 smartctl 授予读取裸盘所需的文件能力（setcap）；
#   3. 为「发起 sudo 的那个普通用户」安装 .desktop 与图标，
#      让应用出现在 KDE / GNOME 的应用菜单里；
#   4. 打印剩余步骤与验证方法。
#
# 本项目只使用 setcap 方案：不使用 polkit / pkexec，不跑 sudo GUI，也不改 udev 规则。
# 装完之后 GUI、CLI、守护进程、SMART 自检全部以普通用户身份运行，
# 不会再出现任何 sudo 提示。
#
# 需要哪些能力（实测于本机 Linux 7.0 / smartctl 7.5）：
#   cap_sys_rawio         —— SATA 盘走 SG_IO 读裸盘所必需；
#   cap_dac_read_search   —— 绕过 /dev/sdX 的读权限检查；
#   cap_sys_admin         —— NVMe 盘必需。内核在 nvme_user_cmd() 里对
#                            NVME_IOCTL_ADMIN_CMD 做了 capable(CAP_SYS_ADMIN)
#                            检查，只给 rawio 会得到
#                            "NVME_IOCTL_ADMIN_CMD: Permission denied"（exit_status=4）。
#                            不想给这个能力时可用 --no-nvme，代价是 NVMe 盘读不到 SMART。
#
# 风险提示（提示词第 6 节要求明示）：
#   cap_sys_rawio / cap_sys_admin 会让本机**任意用户**都能通过 smartctl 读取裸盘，
#   其中 cap_sys_admin 的权限面更大（属于接近 root 的强能力）。这是 setcap 方案的已知代价。
#   若你无法接受，请用 ./install.sh --no-nvme 只授 SATA 需要的能力，
#   或手工撤销：sudo setcap -r "$(command -v smartctl)"

set -euo pipefail

SMARTCTL="$(command -v smartctl || true)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || true)"
APP_ID="knowdiskdrive"

info() { printf '\033[1;34m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# 按发行版给出安装 smartmontools 的命令（ID / ID_LIKE 匹配，都没有就退到通用提示）。
distro_install_hint() {
    local id="" like="" line
    if [ -r /etc/os-release ]; then
        while IFS= read -r line; do
            case "$line" in
                ID=*) id="${line#ID=}"; id="${id//\"/}"; id="${id//\'/}" ;;
                ID_LIKE=*) like="${line#ID_LIKE=}"; like="${like//\"/}"; like="${like//\'/}" ;;
            esac
        done < /etc/os-release
    fi
    for candidate in "$id" $like; do
        case "$candidate" in
            debian|ubuntu|linuxmint|pop|neon) printf 'sudo apt install smartmontools'; return 0 ;;
            arch|manjaro|endeavouros) printf 'sudo pacman -S smartmontools'; return 0 ;;
            fedora|rhel|centos|rocky|almalinux) printf 'sudo dnf install smartmontools'; return 0 ;;
            opensuse*|sles) printf 'sudo zypper install smartmontools'; return 0 ;;
            gentoo) printf 'sudo emerge --ask sys-apps/smartmontools'; return 0 ;;
            alpine) printf 'sudo apk add smartmontools'; return 0 ;;
            void) printf 'sudo xbps-install -S smartmontools'; return 0 ;;
        esac
    done
    printf '请用系统包管理器安装 smartmontools（apt / dnf / pacman / zypper）'
}

# 定位随包发布的资源（.desktop 与图标）。
#
# 两处都找：源码树里的 knowdiskdrive/data/（开发时），以及已安装的 Python 包
# （pip install 之后资源在 site-packages/knowdiskdrive/data/ 下）。
# 旧版本把资源放在源码树顶层的 data/，一并兼容。
locate_resource() {
    local name="$1" candidate out
    for candidate in \
        "$SCRIPT_DIR/knowdiskdrive/data/$name" \
        "$SCRIPT_DIR/data/$name"
    do
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    for candidate in "${PYTHON_BIN:-}" /usr/bin/python3; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        out="$("$candidate" - "$name" <<'PY' 2>/dev/null || true
import sys
from importlib import resources

try:
    print(resources.files("knowdiskdrive.data").joinpath(sys.argv[1]))
except Exception:
    pass
PY
)"
        if [ -n "$out" ] && [ -f "$out" ]; then
            printf '%s\n' "$out"
            return 0
        fi
    done
    return 1
}

USER_ONLY=0
NO_NVME=0
for arg in "$@"; do
    case "$arg" in
        --user-only) USER_ONLY=1 ;;
        --no-nvme) NO_NVME=1 ;;
        -h|--help)
            sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            err "未知参数：$arg（可用：--user-only、--no-nvme、--help）"
            exit 1
            ;;
    esac
done

if [ "$USER_ONLY" -eq 1 ]; then
    step "仅安装菜单项（--user-only，跳过 setcap）"
else
    if [ "$(id -u)" -ne 0 ]; then
        err "请以 root 身份运行：sudo ./install.sh"
        err "只想装应用菜单、不做权限配置时可用：./install.sh --user-only"
        exit 1
    fi

    # -----------------------------------------------------------------------
    # 1. 检查 smartctl
    # -----------------------------------------------------------------------
    step "1/4 检查 smartmontools"
    if [ -z "$SMARTCTL" ]; then
        err "未找到 smartctl，请先安装 smartmontools："
        err "    $(distro_install_hint)"
        exit 2
    fi
    info "smartctl 路径：$SMARTCTL"
    info "smartctl 版本：$("$SMARTCTL" --version | head -n 1)"

    # -----------------------------------------------------------------------
    # 2. setcap
    # -----------------------------------------------------------------------
    step "2/4 授予读取裸盘所需的能力"
    if [ "$NO_NVME" -eq 1 ]; then
        CAPS="cap_sys_rawio,cap_dac_read_search+ep"
        warn "已选择 --no-nvme：NVMe 盘会显示「未完成一次性配置」，只有 SATA 盘可用。"
    else
        CAPS="cap_sys_rawio,cap_dac_read_search,cap_sys_admin+ep"
    fi
    info "授予能力：$CAPS"
    setcap "$CAPS" "$SMARTCTL"

    if ! getcap "$SMARTCTL" | grep -q cap_sys_rawio; then
        err "setcap 执行后未检测到 cap_sys_rawio，请检查文件系统是否支持扩展属性。"
        err "（若 /usr 是单独挂载且带 nosuid 之类的选项，能力会被忽略。）"
        exit 3
    fi
    info "当前能力：$(getcap "$SMARTCTL")"
    warn "cap_sys_rawio / cap_sys_admin 使本机任意用户可读取裸盘，这是 setcap 方案的已知代价。"
fi

# ---------------------------------------------------------------------------
# 3. 安装 .desktop 与图标（装给发起 sudo 的普通用户，不是 root）
# ---------------------------------------------------------------------------
step "3/4 安装应用菜单入口"

# 定位目标用户：--user-only 时就是当前用户；sudo 时是 SUDO_USER；
# 用 root 直接跑时退回 root 的 HOME。
if [ "$USER_ONLY" -eq 1 ]; then
    TARGET_USER="$(id -un)"
    TARGET_HOME="$HOME"
else
    TARGET_USER="${SUDO_USER:-root}"
    if [ "$TARGET_USER" != "root" ]; then
        TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
    else
        TARGET_HOME="$HOME"
    fi
fi
if [ -z "${TARGET_HOME:-}" ] || [ ! -d "$TARGET_HOME" ]; then
    warn "无法确定目标用户的主目录（SUDO_USER='${SUDO_USER:-}'），跳过菜单安装。"
    warn "你可以改用普通用户手工执行：./install.sh --user-only"
else
    DATA_HOME="$TARGET_HOME/.local/share"
    APPS_DIR="$DATA_HOME/applications"
    ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
    DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
    ICON_FILE="$ICON_DIR/$APP_ID.svg"

    DESKTOP_SOURCE="$(locate_resource "$APP_ID.desktop" || true)"
    ICON_SOURCE="$(locate_resource "$APP_ID.svg" || true)"
    if [ -z "$DESKTOP_SOURCE" ] || [ -z "$ICON_SOURCE" ]; then
        warn "找不到包内的 .desktop 或图标（源码树与已安装的 Python 包都查过了），跳过菜单安装。"
        warn "pip 安装的用户可改用：knowdiskdrive install-desktop"
    else
        # 菜单里的启动命令：优先用 PATH 里的 knowdiskdrive-gui，
        # 其次用本目录的虚拟环境，最后退回 python3 -m。
        if [ "$USER_ONLY" -eq 1 ]; then
            FROM_PATH="$(command -v knowdiskdrive-gui || true)"
        else
            FROM_PATH="$(sudo -u "$TARGET_USER" env bash -lc 'command -v knowdiskdrive-gui' 2>/dev/null || true)"
        fi
        GUI_EXEC=""
        for candidate in \
            "$FROM_PATH" \
            "$SCRIPT_DIR/.venv/bin/knowdiskdrive-gui" \
            "$TARGET_HOME/.local/bin/knowdiskdrive-gui"
        do
            if [ -n "$candidate" ] && [ -x "$candidate" ]; then
                GUI_EXEC="$candidate"
                break
            fi
        done
        if [ -z "$GUI_EXEC" ]; then
            GUI_EXEC="python3 -m knowdiskdrive.gui"
            warn "未找到 knowdiskdrive-gui 可执行文件，菜单项改用「$GUI_EXEC」。"
            warn "若 python3 环境里没有装本包，请先进项目目录执行 pip install -e . 再运行本脚本。"
        fi

        mkdir -p "$APPS_DIR" "$ICON_DIR"
        cp -f "$ICON_SOURCE" "$ICON_FILE"
        # .desktop 模板里的 Exec 默认是 knowdiskdrive-gui；
        # 若本机找不到该可执行文件，就改写成探测到的实际命令。
        if [ "$GUI_EXEC" = "knowdiskdrive-gui" ]; then
            cp -f "$DESKTOP_SOURCE" "$DESKTOP_FILE.tmp"
        else
            sed -e "s|^Exec=knowdiskdrive-gui.*|Exec=$GUI_EXEC %F|" \
                -e "s|^TryExec=knowdiskdrive-gui.*|TryExec=$GUI_EXEC|" \
                "$DESKTOP_SOURCE" > "$DESKTOP_FILE.tmp"
        fi
        mv -f "$DESKTOP_FILE.tmp" "$DESKTOP_FILE"
        chmod 644 "$DESKTOP_FILE" "$ICON_FILE"
        if [ "$USER_ONLY" -eq 0 ]; then
            # sudo 场景：文件属主必须是那个普通用户，否则菜单读不到/删不掉。
            chown "$TARGET_USER:$TARGET_USER" "$APPS_DIR" "$ICON_DIR" "$ICON_FILE" "$DESKTOP_FILE"
        fi
        info "已安装菜单项：$DESKTOP_FILE"
        info "已安装图标：  $ICON_FILE"
        info "启动命令：    $GUI_EXEC"

        if command -v update-desktop-database >/dev/null 2>&1; then
            if update-desktop-database "$APPS_DIR" 2>/dev/null; then
                info "已刷新 desktop 数据库。"
            else
                warn "update-desktop-database 失败，菜单可能需要重新登录后才出现。"
            fi
        else
            warn "未找到 update-desktop-database（desktop-file-utils 未安装），已跳过。"
        fi

        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" 2>/dev/null || true
        fi

        if command -v desktop-file-validate >/dev/null 2>&1; then
            desktop-file-validate "$DESKTOP_FILE" && info "desktop 文件校验通过。"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 4. 剩余步骤与验证方法（提示词 8.8）
# ---------------------------------------------------------------------------
step "4/4 完成，验证方式"
cat <<'EOF'
以普通用户身份（不要加 sudo）运行下面任意一条：

  knowdiskdrive list                 # 应列出 sda / nvme0n1，且不含 loop/zram/dm
  knowdiskdrive health               # 全部磁盘的健康概要
  knowdiskdrive show /dev/nvme0n1    # 单盘 SMART 详情
  knowdiskdrive daemon --once        # 采一轮温度写进历史库
  knowdiskdrive-gui                  # 图形界面（也在应用菜单里）

核对数据是否真实（任选一条）：

  smartctl -a /dev/nvme0n1 | head -20
  sqlite3 ~/.local/share/knowdiskdrive/history.db "select count(*) from temp_samples"

确认权限没被降级：

  id -u                              # 必须是 1000 之类的非 0，不是 root
  getcap "$(command -v smartctl)"    # 应显示 cap_sys_rawio,cap_dac_read_search+ep

想撤销全部改动：

  sudo setcap -r "$(command -v smartctl)"    # 撤销全部文件能力
  rm -f ~/.local/share/applications/knowdiskdrive.desktop
  rm -f ~/.local/share/icons/hicolor/scalable/apps/knowdiskdrive.svg
EOF

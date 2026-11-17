# KnowDiskDrive 开发文档

> 仓库根目录的 `README.md` 是简介，这里是完整文档；文中路径均相对仓库根目录。

Linux 桌面磁盘健康检测工具：读取 S.M.A.R.T. 数据，展示硬盘健康状态、温度历史与异常告警。
图形界面用 PySide6，命令行用标准库，界面与文案全中文。

支持 SATA（ATA 属性表）、NVMe（健康信息日志）、USB 桥接盘（读不通时给出明确原因）。

---

## 1. 安装

```bash
cd know_diskdrive
python3 -m venv .venv
.venv/bin/pip install -e .          # 国内网络可加 -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
```

也可以直接装（不需要源码树）：`pip install .`，或从 wheel 装 `pip install knowdiskdrive-0.0.2-py3-none-any.whl`。

要求 Python ≥ 3.10（3.10 会自动带上 `tomli` 兜底标准库的 `tomllib`）。

装完后有这些入口：

| 命令 | 说明 |
| --- | --- |
| `knowdiskdrive` | 命令行（`list` / `show` / `health` / `daemon` / `export` / `install-desktop`） |
| `knowdiskdrive-gui` | 图形界面 |
| `python -m knowdiskdrive.gui` | 同上，未装 console script 时的退路 |
| `knowdiskdrive install-desktop` | 以普通用户身份装应用菜单项与图标（pip 安装后没有源码树时用这个） |

## 2. 一次性权限配置（必须）

smartctl 读裸盘需要权限。本项目**只采用 setcap 方案**，不写 polkit 规则、不常驻 root 进程、不改 udev 规则。
给 `smartctl` 这个可执行文件加一次文件能力即可，**每台机器、每个 smartctl 文件做一次**，之后所有用户都能读。

三种做法效果完全一样，按安装方式选：

| 你的情况 | 做法 |
| --- | --- |
| 图形界面（AppImage、PyInstaller、pip 都算） | 权限不足时界面上有横幅，点 **「一键授权」**，弹出系统自己的管理员密码框；输完密码自动重新读盘 |
| 命令行 | `knowdiskdrive fix-permission`（同样弹密码框；`--print` 只打印命令不执行） |
| 用源码树、还想顺手装菜单项 | `sudo ./install.sh` |

> **AppImage / pip 用户看这里**：这两种安装**没有** `install.sh` 这个文件，
> 所以「一键授权」和 `fix-permission` 才是正式路径，不需要去找任何脚本。
>
> 一键授权按顺序尝试：`pkexec`（KDE / GNOME 桌面的系统密码框）→ `sudo -A` + zenity/kdialog 密码框
> → 终端里的 `sudo`。都不行时横幅会出现「复制命令」按钮，把命令粘到终端执行也可以。
> 取消密码框不会连弹两次，只会显示「已取消授权」。

### 2.1 直接用 install.sh（源码树）

```bash
sudo ./install.sh
```

脚本做四件事（提示词第 6 节）：

1. 检查 `smartctl` 是否存在，缺失时按 `/etc/os-release` 给出对应发行版的安装命令并以退出码 2 结束；
2. `setcap cap_sys_rawio,cap_dac_read_search,cap_sys_admin+ep $(command -v smartctl)`，并用 `getcap` 复验；
3. 给**发起 sudo 的那个普通用户**安装菜单项 `~/.local/share/applications/knowdiskdrive.desktop`
   与图标 `~/.local/share/icons/hicolor/scalable/apps/knowdiskdrive.svg`，然后
   `update-desktop-database` + `gtk-update-icon-cache` + `desktop-file-validate`；
4. 打印验证清单与撤销方法。

不需要 root、只想装菜单项时：

```bash
./install.sh --user-only
```

只想授 SATA 需要的能力、放弃 NVMe 时：

```bash
sudo ./install.sh --no-nvme
```

> **为什么还需要 `cap_sys_admin`**：内核在 `nvme_user_cmd()` 里对 `NVME_IOCTL_ADMIN_CMD` 做了
> `capable(CAP_SYS_ADMIN)` 检查，只给 `cap_sys_rawio` 时 NVMe 盘会返回
> `NVME_IOCTL_ADMIN_CMD: Permission denied`（`exit_status=4`），表现为「未完成一次性配置」。
> SATA 盘只需 `cap_sys_rawio`。
>
> **风险说明**：`cap_sys_rawio` / `cap_sys_admin` 会让本机**任意用户**都能通过 smartctl 读取裸盘，
> 其中 `cap_sys_admin` 权限面更大（接近 root 的强能力）。这是 setcap 方案的已知代价。
> 无法接受时请用 `--no-nvme`，改用 `disk` 组方案，或 `sudo setcap -r "$(command -v smartctl)"` 撤销。

## 3. 命令行

```bash
knowdiskdrive list                  # 列出本机磁盘（已过滤 loop / zram / dm-*）
knowdiskdrive show /dev/nvme0n1     # 单盘 SMART 详情（表格）
knowdiskdrive show /dev/sda --json  # 机器可读输出
knowdiskdrive health                # 全部磁盘健康概要，退出码 0=正常 1=有告警
knowdiskdrive daemon --once         # 采一轮温度写进历史库后退出
knowdiskdrive daemon                # 常驻采样（默认每 300 秒一轮，Ctrl-C 退出）
knowdiskdrive daemon --interval 60 --rounds 10
knowdiskdrive export --days 7 -o temps.csv
knowdiskdrive fix-permission         # 弹管理员密码框，给 smartctl 加读取能力
knowdiskdrive fix-permission --print # 只打印要执行的 setcap 命令
```

`daemon` 的退出码：`0` 全部磁盘读取成功，`2` 至少一块盘读不到（此时输出会提示先跑
`knowdiskdrive fix-permission` 或点界面上的「一键授权」）。

### 数据从哪来

主路径是 `smartctl -j`（JSON），只有它不可用时才降级到 `-a` 文本解析，此时
`DiskInfo.parse_mode == "text"`，界面上会以灰色小字提示「兼容模式解析」并给出原因。
文本降级同样会解析 ATA 属性表与 NVMe 健康日志（温度、通电时间、通电次数、寿命消耗等
都从属性 9/12/190/194 或日志键回填），所以状态徽章在兼容模式下也有依据，不会出现
「显示良好但没有任何数据」的情况。

USB 桥接盘首次探测会按 `sat` → `usbjmicron` → `usbsunplus` 依次尝试；探测成功的
`-d` 值以**序列号**为键写进 `device_prefs` 表，之后每次读取直接复用（盘符会变，
序列号不会），全部失败则明确显示已尝试过的 `-d` 列表。

## 4. 图形界面

```bash
knowdiskdrive-gui
```

- **顶部**：健康徽章（良好 / 注意 / 危险 / 无法判定）+ 型号、序列号、容量、接口、通电次数与时间、当前温度；
- **属性表**：ATA 属性中文化（`knowdiskdrive/attrnames.py`），关键项置顶并高亮，
  临界属性整行标红、状态列写「临界」；NVMe 走健康信息日志；
  属性名只对 smartctl 能叫出标准名的 ID 做翻译，smartctl 报 `Unknown_Attribute` /
  `Unknown_SSD_Attribute` 的 ID 显示成「厂商自定义属性 N」并照抄原始值 —— 这类 ID 存
  什么由厂商自定，编一个具体含义会误导人；
- **温度曲线**：内置图表，含按介质区分的阈值参考线；
- **托盘**：图标颜色随温度等级变化，菜单含「显示主窗口 / 立即刷新 / 暂停监控 / 退出」；
- **热插拔**：`pyudev` 监听 block 子系统，插入或拔出后 400 ms 去抖合并成一次刷新；
- **失败态**：读不到 SMART 时给出明确原因（权限、USB 桥接、休眠、超时、设备消失），
  不显示伪造数据，可一键复制信息；
- **权限不足时**：顶部横幅直接给出 **「一键授权」** 按钮，点下去弹系统自己的管理员密码框，
  输完密码自动重新读盘 —— AppImage / pip 安装没有 install.sh，这条路径就是给它们的
  （见第 2 节）。弹不出密码框时按钮变成「复制命令」，把 setcap 命令交给你手动执行。

无托盘环境（纯 Wayland、offscreen）下不创建托盘，关窗即退出，不会崩溃。

界面固定使用浅色配色（不跟随系统深色主题），因此无论桌面是浅色还是深色都需要一致可读。
这件事要做两层，缺一层都会漏：

1. **样式表**（`knowdiskdrive/gui/theme.py` 的 `STYLESHEET`）负责写了选择器的控件：
   设备列表、属性表、滚动条、按钮、对话框与托盘菜单的底色与字色；
2. **应用调色板**（同文件的 `build_palette()`，由 `apply_light_palette()` 在
   `gui/app.py` 的 `create_application` 里装到 QApplication 上）负责样式表没点名的控件，
   典型就是列表 / 表格的 viewport、滚动区、原生子控件。样式表管不到它们，它们只能靠
   调色板兜底，而在 KDE / GNOME 深色主题下 Qt 取到的 Base 是 Breeze 的 `#26292e`，
   于是同一窗口里会出现「右侧白底、左侧整片深底」的割裂。

Qt 自带的中文翻译在 `gui/app.py` 的 `_install_translations` 里加载，否则消息框按钮会显示成
英文 `Yes / No`。

复现与验证深色主题下的渲染（不碰真实磁盘，两个脚本都灌假数据）：

```bash
# 模拟「深色系统调色板 + 样式表缺 item view 段」的旧状态，左栏应整片发黑
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/repro_dark.py before /tmp/repro_before.png
# 当前代码，左栏应为白底
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/repro_dark.py after /tmp/repro_after.png
# 只注入深色系统调色板，验证调色板兜底后界面仍全白
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshot_dark.py /tmp/dark_ok.png
# 真实桌面上开窗抓图（Wayland 下只能 window.grab()，spectacle 无法非交互截窗口）
.venv/bin/python tools/screenshot_live_real.py /tmp/live.png
```

## 5. 历史与告警

- 历史库：SQLite，默认 `~/.local/share/knowdiskdrive/history.db`，可用环境变量 `KDD_DB` 覆盖；
- 采样：`temp_samples` 表，保留天数由配置项 `retention_days` 控制，`daemon` 每 24 小时清理一次；
- 自检：`启动短自检` 下发 `smartctl -t short` 后，界面每 5 秒用 `-a` 轮询一次进度
  （ATA 看 `ata_smart_data.self_test.status`，NVMe 看 `nvme_self_test_log`），
  每次进度写一条 `test_results` 记录；自检结束后自动停止轮询，换盘或退出也会停；
- 告警：温度跨阈值、属性恶化、SMART 自检失败时发桌面通知，同一告警 `alert_dedup_minutes` 内不重复。

## 6. 配置

配置项集中在 `knowdiskdrive/config.py`，不硬编码在界面或逻辑里。配置文件是
`$XDG_CONFIG_HOME/knowdiskdrive/config.toml`（默认 `~/.config/knowdiskdrive/config.toml`，
可用 `KDD_CONFIG` 指向别处）；文件不存在就用内置默认值，写坏了只在界面/终端提示一句并回退默认：

```toml
[smartctl]
path = ""                       # 留空则依次查 PATH 与 /usr/sbin 等常见目录
timeout_seconds = 15.0
standby_policy = "standby,3"    # 传给 smartctl -n，避免唤醒休眠盘
usb_bridge_d_args = ["sat", "usbjmicron", "usbsunplus"]

[history]
sample_interval_seconds = 300   # 采样间隔，最小 10 秒
retention_days = 90             # 温度样本保留天数，最小 1 天

[alerts]
dedup_minutes = 30              # 同一告警的去重窗口（分钟）

# 温度阈值按介质分档（提示词 4.3）。低于 green_below 为绿色，到 yellow_max 转黄，超过转红。
[temp_thresholds.hdd]
green_below = 40
yellow_max = 50

[temp_thresholds.sata_ssd]
green_below = 50
yellow_max = 60

[temp_thresholds.nvme]
green_below = 65
yellow_max = 80

[ui]
# 属性表里需要置顶显示的 ATA 属性 ID
pinned_attribute_ids = [5, 10, 194, 197, 198, 199]
```

## 7. 项目结构

```
knowdiskdrive/
  pyproject.toml               # 依赖、console_scripts、包内数据文件
  install.sh                   # 一次性 root 配置（第 6 节四项）
  pack/
    knowdiskdrive.spec         # PyInstaller 规格（第 10 节）
    entry.py                   # 打包入口，KDD_SELFTEST=1 时只做自检，--cli 走命令行
    build-appimage.sh          # 构建 AppImage 的外层：准备 22.04 rootfs 并 bwrap 进去（第 10.3 节）
    build-appimage-inner.sh    # 在 rootfs 里真正干活的构建脚本：打包、查 glibc、拼 squashfs
    glibc_check.py             # 扫产物 ELF 的 GLIBC 版本需求，挡住「只能在新系统跑」
    build-source.sh            # 打源码压缩包（第 10.4 节）
    appimage-runtime-head.bin  # 官方 runtime-x86_64（拼接 squashfs 用）
  knowdiskdrive/
    cli.py                     # 命令行入口
    desktop.py                 # 安装菜单项与图标（install-desktop）
    devices.py                 # 块设备枚举与过滤
    smart.py                   # smartctl 调用与 JSON 解析（主路径）
    health.py                  # 健康判定规则（第 4 节唯一真源）
    privilege.py               # 一次性授权：setcap + pkexec/sudo 密码框（第 2 节）
    attrnames.py               # ATA 属性中文名
    history.py                 # SQLite 温度历史
    alerts.py                  # 告警引擎
    daemon.py                  # 后台采样
    gui/
      main_window.py           # 主窗口
      chart.py                 # 温度曲线
      models.py                # 表格模型
      monitor.py               # pyudev 热插拔
      tray.py                  # 托盘与通知
      workers.py               # QThread 后台任务
      icon.py                  # 应用图标
    data/
      knowdiskdrive.desktop  # 应用菜单入口模板（随 wheel 发布）
      knowdiskdrive.svg      # 应用图标（随 wheel 发布）
  tests/                       # pytest，纯内存夹具，无需 root
  tools/
    acceptance.py              # A1–A9 验收自测
    screenshot.py              # 截图：注入 fixture（开发辅助）
    screenshot_live.py         # 截图：读真实磁盘（开发辅助）
    screenshot_live_real.py    # 真实桌面开窗抓图，打印调色板诊断（开发辅助）
    screenshot_dark.py         # 深色系统调色板下的渲染验证（开发辅助）
    repro_dark.py              # 复现/验证「左侧列表发黑」（开发辅助）
```

## 8. 测试与验收

```bash
.venv/bin/python -m pytest -q                 # 全部单元测试（不需要 root）
.venv/bin/python tools/acceptance.py          # A1–A9 验收自测
.venv/bin/python tools/acceptance.py A1 A7    # 只跑指定项
.venv/bin/python tools/acceptance.py --json   # 机器可读
```

验收自测的四种结论：**通过** / **失败** / **受阻**（需要 `sudo ./install.sh`）/
**需人工**（只有确实无法自动化的项才会落到这里）。没有 root 能力时会如实报「受阻」，
不会伪装成通过。

能自动化的项连同真机链路一起验，不靠肉眼：

* **A5** 会在真实图形会话里跑一遍「良好基线 → 恶化样本」，用 `dbus-monitor` 抓
  `org.freedesktop.Notifications` 的 `Notify` 调用，确认通知真的发出去了；
* **A6** 先静态检查 `pyudev.Monitor.from_netlink` 接入与 400ms 去抖，再用
  `udevadm trigger --subsystem-match=block --action=change` 重放真实 netlink 事件，
  量出「事件 → 刷新」的延迟并校验 ≤ 2 秒、多次事件被合并成一次刷新；
* **A8** 依次验证 CLI、GUI 用的读盘函数（`workers.execute_request`）、
  `start_self_test` 三条路径都在普通用户身份下成功且输出里没有 `sudo` 字样。

在完成 `sudo ./install.sh` 的机器上，实测结论为**通过 9 / 失败 0 / 需人工 0**（9 项全部自动判定）
（A6 的实测分支需要一次 sudo 提权来重放 uevent，桌面上会弹出密码框；
sudo 凭据未缓存又拿不到图形提权工具时，该项会说明原因并退回「需人工」）：

| 项 | 结论 | 说明 |
|---|---|---|
| A1 | 通过 | sda 与 nvme0n1 均在列表中，无 loop/zram/dm |
| A2 | 通过 | 两块盘序列号、温度、通电时间均与真机一致 |
| A3 | 通过 | 7 份 fixture 齐全，386 项测试通过，`smart.py` 覆盖率 92% |
| A4 | 通过 | 读盘在 QThread 上执行，超时文案存在 |
| A5 | 通过 | 临界行标红 + 状态列「临界」；dbus 抓到 2 条真实系统通知 |
| A6 | 通过* | 缓存 sudo 凭据时实测：31 个 netlink 事件合并为 1 次刷新，延迟 0.43 秒；凭据失效且无图形提权工具时该项退回「需人工」并说明原因 |
| A7 | 通过 | 两轮 `daemon --once` 均写入新样本，停止后不再增长 |
| A8 | 通过 | CLI / GUI 读盘 / SMART 短自检均以普通用户完成，无 sudo 提示 |
| A9 | 通过 | offscreen（无托盘）下正常启停 |

## 9. 撤销

```bash
sudo setcap -r "$(command -v smartctl)"
rm -f ~/.local/share/applications/knowdiskdrive.desktop
rm -f ~/.local/share/icons/hicolor/scalable/apps/knowdiskdrive.svg
```

## 10. 在其他电脑上安装

两种方式，按目标机有没有 Python 环境来选。

### 10.1 pip 安装（目标机有 Python ≥ 3.10）

```bash
python3 -m venv ~/.local/venvs/kdd && ~/.local/venvs/kdd/bin/pip install knowdiskdrive-0.0.2-py3-none-any.whl
~/.local/venvs/kdd/bin/knowdiskdrive install-desktop   # 装菜单项与图标（普通用户身份，别加 sudo）
~/.local/venvs/kdd/bin/knowdiskdrive fix-permission    # 弹密码框做一次性授权
```

权限仍需在第 2 节点单独处理一次（`setcap` 是每台机器、每个 `smartctl` 文件的一次性动作）。
pip 安装没有源码树，所以用 `fix-permission`，或者干脆打开图形界面点横幅上的「一键授权」。

wheel 里已经带了 `.desktop` 与图标（`knowdiskdrive/data/`），所以 `install-desktop` 不依赖源码树。

### 10.2 打成可执行文件（目标机没有 Python）

```bash
pip install "pyinstaller>=6.3"
pyinstaller --noconfirm --clean pack/knowdiskdrive.spec
KDD_SELFTEST=1 QT_QPA_PLATFORM=offscreen ./dist/knowdiskdrive/knowdiskdrive-gui   # 交付前自检
```

产物是 `dist/knowdiskdrive/` 整个目录（实测约 181 MB，PySide6 占大头），拷到目标机即可。
`KDD_SELFTEST=1` 会打印 `frozen / desktop / icon / smartctl / devices`，正常应当是
`frozen=True`、两个资源路径都非 `None`、`smartctl=/usr/sbin/smartctl`、磁盘数与真机一致。

打包产物的限制（务必先确认）：

* 只能运行在同 CPU 架构 + 同 ABI 的机器上（x86_64 打的包不能给 arm64）；
* **glibc 下限由构建机决定，不是由源码决定**。PyInstaller 会把构建机上的
  *系统库*（`libglib-2.0.so.0`、`liblcms2.so.2`、`libstdc++.so.6` …）一起收进
  产物，它们的版本需求就是构建机发行版的版本。在 Ubuntu 26.04（glibc 2.43）上
  打包，产物拿到 Ubuntu 24.04（2.39）上直接
  `ImportError: version 'GLIBC_2.43' not found`；
* 打完一定要查一次：`python3 pack/glibc_check.py dist/knowdiskdrive --target 2.35`。
  它直接解 ELF 的 `.gnu.version_r`，不用 `objdump`、不看本机架构；
* 仍需目标机自己装 `smartmontools` 并做一次 `setcap`——这两件事都要求 root，不能内置；
* 若目标机是 Wayland + 高 DPI，首次启动可能要在配置里调字号；
* **打包用的 Python 若是 3.10/3.11，PySide6 必须低于 6.12**（见 10.7）。`pyproject.toml`
  里的依赖上界已经这么写了，但直接在本机打包时若已装 6.12，要先
  `pip install "PySide6<6.12"`，否则产物退出时会 SIGABRT。

要在老系统上用，就别在本机打包——用 10.3 的方式，在旧发行版的 rootfs 里构建。

### 10.3 打成 AppImage（单个文件，双击即用）

```bash
./pack/build-appimage.sh            # 版本号从 pyproject.toml 读
ls dist/0.0.2/
# knowdiskdrive-0.0.2-x86_64.AppImage   （实测 45 MB）
./dist/0.0.2/knowdiskdrive-0.0.2-x86_64.AppImage            # 开图形界面
./dist/0.0.2/knowdiskdrive-0.0.2-x86_64.AppImage list       # 走命令行
```

AppImage 的 `AppRun` 按第一个参数分流，所以同一个文件既能双击运行，也能当命令用
——冻结包里没有独立的 `knowdiskdrive` 脚本，两条命令共用 `knowdiskdrive-gui`
这一个二进制（靠 `--cli` 标志分流，见 `pack/entry.py`）：

| 第一个参数 | 走哪 |
|---|---|
| 无参数 | 开图形界面 |
| `--version` / `-h` / `--help` | 命令行（在终端里问版本不该弹窗） |
| 其余以 `-` 开头（如 Qt 的 `-platform offscreen`） | 图形界面，原样透传 |
| 裸词（`list` / `show` / `health` / `daemon` / `export` / `install-desktop` / `fix-permission`） | 命令行 |

裸词那一档故意不设白名单：拼错的子命令交给 `argparse` 报错，
比悄悄弹出一个窗口再去猜哪里写错了好。

构建不依赖 `appimagetool`：脚本取官方 runtime 的 ELF 头
（`pack/appimage-runtime-head.bin`，193728 字节，sha256 `66f5b22f…0772557`，
取自 [AppImageKit continuous 的 runtime-x86_64]
(https://github.com/AppImage/AppImageKit/releases/download/continuous/runtime-x86_64)，
版本串 `Version: 5735cc5`），拼上自己 `mksquashfs -comp xz` 生成的 squashfs，
得到标准 type-2 AppImage。压缩必须是 xz 或 zlib，官方 runtime 只内置这两种
解压器，zstd 会报 `uses (null) compression ... supports only xz, zlib`。
`mksquashfs` 默认开 4 路（`KDD_SQUASH_PROCS` 可调），免得在 16 核机器上把内存吃满。

**别从现成 AppImage 里切头代用**。AppImage 的 ELF 里除了 runtime 代码，还有
`.upd_info`（更新源，形如 `gh-releases-zsync|owner|repo|tag|文件名.zsync`）、
`.digest_md5`、`.sha256_sig`、`.sig_key` 几个元数据段。切头会把对方的更新源
一起带过来，产物就会声称「我的更新源是别人的发布」——用户跑 AppImageUpdate
时会下载别人的文件覆盖本程序。`build-appimage.sh` 第 1 步会把这四个段清零并
打印清了哪些，`tests/test_packaging.py` 也钉住了这一点（并校验 runtime 头的
sha256，防止被就地改过）。

构建脚本最后会自己做冒烟：`--appimage-extract` 解包 → 跑 `KDD_SELFTEST=1`
自检 → 跑 `--version` 确认 CLI 分支生效。三样都过了才算产物可用。

**构建在 Ubuntu 22.04 的 rootfs 里做，不在当前系统上做。** 原因见 10.2：
产物能跑多老的系统，取决于构建机自己的 glibc。`pack/build-appimage.sh` 是这个流程的
外层包装——首次运行会把 [ubuntu-base 22.04.5]
(https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/22.04/release/)
的迷你根文件系统拉到 `~/.cache/knowdiskdrive-build/rootfs-2204`（29 MB，校验 sha256），
在里面 `apt install` 构建依赖、建 `/opt/kdd-venv`（PySide6 6.11.3 + PyInstaller），
然后用 `bwrap` 把源码树挂进 `/work`、以 root 身份跑 `pack/build-appimage-inner.sh`。
rootfs 准备一次就够（留了 `.kdd-ready` 标记），之后重复构建只走打包那几步。

外层脚本做三件事：准备 rootfs、`bwrap` 进入、跑内层。内层脚本才是真正干活的
（清零 runtime 元数据 → PyInstaller → glibc 检查 → 组装 → mksquashfs → 冒烟）。
单独跑内层也支持，但那样用的是当前系统的库——glibc 检查会因此失败并提示
「用 pack/build-appimage.sh 构建」。需要换镜像或换个缓存位置时设
`KDD_BUILD_CACHE`、`KDD_GLIBC_TARGET`、`KDD_SQUASH_PROCS`。

用这套流程构建出来的产物，实测最高需求是 `GLIBC_2.35`（来自 `_internal/libgcc_s.so.1`），
即在 Ubuntu 22.04 及更新的发行版上都能跑。

AppImage 的限制：同 CPU 架构（x86_64）、**glibc ≥ 2.35**（Ubuntu 22.04+ / Debian 12+ /
RHEL 9+，见上）、目标机要有 `smartmontools` 与一次 `setcap`，另外多一条：
**目标机的 `/dev/fuse` 要可用**。

AppImage 里没有 `install.sh`，授权走界面横幅上的「一键授权」，或在终端执行
`./knowdiskdrive-0.0.2-x86_64.AppImage fix-permission`（见第 2 节）。
没有 FUSE 的环境（部分容器、精简系统）用 `--appimage-extract-and-run` 代替直接执行。

### 10.4 源码压缩包

```bash
./pack/build-source.sh              # 产物 dist/<版本>/knowdiskdrive-<版本>-src.tar.gz
tar -xzf dist/0.0.2/knowdiskdrive-0.0.2-src.tar.gz
cd knowdiskdrive-0.0.2 && python3 -m venv .venv && .venv/bin/pip install -e .
```

包里是完整可构建的源码树：`knowdiskdrive/`、`tests/`、`tools/`、`pack/`、
`install.sh`、`pyproject.toml`、`README.md`。排除了 `.venv`、`build`、`dist`、
`.pytest_cache`、`.coverage`、`__pycache__`、`*.egg-info` 与本机虚拟环境路径记录。
实测解包后 `pytest` 仍是 386 passed，不依赖源码树以外的任何东西。

`tools/acceptance.py` 会优先用项目里的 `.venv/bin/python`，没有就退回当前解释器，
CLI 也支持 `python -m knowdiskdrive` 兜底——所以解包到别处、用别的虚拟环境跑验收
同样能出结果，而不是一片 `退出码 127`。

### 10.5 发布产物一览

三个文件都在 `dist/<版本>/` 下，附 `SHA256SUMS` 与 `RELEASE-NOTES.md`：

| 文件 | 大小 | 给谁 |
|---|---|---|
| `knowdiskdrive-0.0.2-x86_64.AppImage` | 45 MB | 不想装 Python 的机器，双击即用（glibc ≥ 2.35） |
| `knowdiskdrive-0.0.2-src.tar.gz` | 215 KB | 要自己构建、改代码、或审阅 |
| `knowdiskdrive-0.0.2-py3-none-any.whl` | 88 KB | 有 Python ≥ 3.10，pip 安装最省事 |

校验：`cd dist/0.0.2 && sha256sum -c SHA256SUMS`。

AppImage 在 `env -i` 清空环境变量后仍能运行，产物里不含构建机的 venv 路径；
解包后 `ldd knowdiskdrive-gui` 无缺失（Qt 全在包内）。源码包能在全新 venv 里
`pip install` 后直接跑起来。

### 10.6 跨发行版要注意的几点

* `smartctl` 不一定在 `PATH` 里（Debian/Ubuntu 装在 `/usr/sbin`）。程序会依次查
  `smartctl_path` 配置、`PATH`、`/usr/sbin:/usr/local/sbin:/sbin:/usr/bin:/usr/local/bin:/bin`，
  所以从应用菜单启动也不会误报「未安装」；
* 未装 `smartctl` 时的提示按 `/etc/os-release` 生成（apt / dnf / pacman / zypper / apk / xbps / emerge）；
* 配置与数据库都走 XDG 路径，没有硬编码 `~/.config`，换用户、换 `HOME` 都能用。

### 10.7 已知问题：PySide6 6.12 + Python < 3.12 会崩

PySide6 **6.12.0** 在 **Python 3.10 / 3.11** 上有引用计数缺陷（Qt 的 PYSIDE-3424、
PYSIDE-3474）：普通的 Qt 调用就会改坏 Qt 单例的引用计数，进程可能在运行时崩，
也可能在**退出时**报

```text
Fatal Python error: none_dealloc: deallocating None
Python runtime state: finalizing
```

然后以 SIGABRT（signal 6）终止、留下 core dump。0.0.1 的 AppImage 是 Python 3.10 +
PySide6 6.12.0 构建的，因此会中招（Fedora 44 上「点完一键授权、关掉窗口」时的崩溃
就是它，界面本身没问题）。这与本项目业务代码无关，用下面 5 行即可复现：

```python
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([])
widget = QWidget()
widget.show()
app.processEvents()
for _ in range(20000):
    widget.repaint()   # 退出时 SIGABRT
```

0.0.2 起的处理：

* 依赖写成 `PySide6>=6.6,<6.12; python_version < "3.12"`（3.12+ 不设上界）；
* AppImage 用 PySide6 **6.11.3** 构建（`pack/build-appimage.sh` 里的 `PYSIDE_VERSION`）；
* 程序启动时还会检查这个组合，命中就在状态栏与 stderr 提示改用 6.11.x 或升级 Python。

自己装的时候遇到同样现象，执行 `pip install "PySide6<6.12"` 即可。

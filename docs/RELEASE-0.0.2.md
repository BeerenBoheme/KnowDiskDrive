# KnowDiskDrive 0.0.2

0.0.2 只做一件事：修掉会让程序异常退出的崩溃。功能与 0.0.1 一致。

## 修了什么：PySide6 6.12 + Python < 3.12 导致的 SIGABRT

现象（Fedora 44 / KDE Wayland 上实测）：双击 AppImage、点「一键授权」、输入密码、
随后关掉窗口，进程以 signal 6 (SIGABRT) 结束，systemd 记一条 core dump，
journal 里留下：

```text
Fatal Python error: none_dealloc: deallocating None
Python runtime state: finalizing (tstate=0x...)
Current thread ... (most recent call first):
  Garbage-collecting
  <no Python frame>
```

根因不在本项目：**PySide6 6.12.0 在 Python 3.10 / 3.11 上有引用计数缺陷**（Qt 的
PYSIDE-3424、PYSIDE-3474），普通的 Qt 调用就会改坏 Qt 单例的引用计数，崩溃可能
发生在使用中，也可能发生在解释器收尾的 GC 里。0.0.1 的 AppImage 是 Python 3.10 +
PySide6 6.12.0 构建的，所以中招。与本项目无关的最小复现（4 行，Python 3.10 +
PySide6 6.12.0，`QT_QPA_PLATFORM=offscreen`）：

```python
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([])
widget = QWidget()
widget.show()
app.processEvents()
for _ in range(20000):
    widget.repaint()   # 退出时 SIGABRT
```

0.0.2 的修法：

- 依赖改成 `PySide6>=6.6,<6.12; python_version < "3.12"`（3.12 及以上不设上界）；
- AppImage 改用 **PySide6 6.11.3** 构建（`pack/build-appimage.sh` 的 `PYSIDE_VERSION`）；
- 程序启动时检查这个组合，命中就在状态栏与 stderr 提示改用 6.11.x 或升级 Python；
- `tests/test_packaging.py` 把依赖上界与打包版本钉住，防止回退。

## 功能

- **命令行**：`list` / `show` / `health` / `daemon` / `export` / `install-desktop` / `fix-permission`，数据类子命令支持 `--json`
- **一键授权**：图形界面在权限不足时给出「一键授权」按钮，弹系统管理员密码框完成 `setcap`；命令行对应 `knowdiskdrive fix-permission`
- **图形界面**：设备列表、属性表、温度曲线、托盘与通知、热插拔监控、自检进度轮询
- **健康判定**：按提示词第 4 节的规则统一口径（严格警告 → 警告 → 良好），SATA 与 NVMe 各一套阈值
- **历史与告警**：SQLite 存温度历史（默认 `~/.local/share/knowdiskdrive/history.db`），告警引擎按降温/回温阈值去抖
- **跨发行版**：`smartctl` 定位走配置 → PATH → `/usr/sbin` 等固定目录；未安装时的提示按 `/etc/os-release` 生成（apt / dnf / pacman / zypper / apk / xbps / emerge）

## 产物

| 文件 | 大小 | 用途 |
|---|---|---|
| `knowdiskdrive-0.0.2-x86_64.AppImage` | 45 MB | 单文件分发，双击即用，不需要 Python |
| `knowdiskdrive-0.0.2-src.tar.gz` | 361 KB | 完整源码树，解包即可构建 |
| `knowdiskdrive-0.0.2-py3-none-any.whl` | 87 KB | pip 安装（目标机有 Python ≥ 3.10） |

从 0.0.1 升上来直接换文件即可：配置（`~/.config/knowdiskdrive/config.toml`）与历史库
（`~/.local/share/knowdiskdrive/history.db`）格式没变。

## 使用前必须知道

1. **读 SMART 需要权限**。目标机要装 `smartmontools` 并做一次
   `sudo setcap cap_dac_read_search,cap_sys_rawio,cap_sys_admin+ep /usr/bin/smartctl`
   （Fedora 在 `/usr/bin`，Debian/Ubuntu 在 `/usr/sbin`），或者把用户加进 `disk` 组。
   不做这一步，所有磁盘都会显示「权限不足」。
   **不需要找 `install.sh`**（AppImage / pip 安装里也没有它）：
   图形界面点横幅上的「一键授权」，或命令行跑 `knowdiskdrive fix-permission`，
   两者都会弹系统自己的管理员密码框。源码树里的 `install.sh` 第 2 节做的是同一件事。
2. **AppImage 需要 `/dev/fuse`**。没有 FUSE 的环境用 `--appimage-extract-and-run`。
3. **平台限制**：x86_64 构建只能跑 x86_64；需要 glibc ≥ 2.35
   （Ubuntu 22.04+ / Debian 12+ / RHEL 9+）。产物在 Ubuntu 22.04 的 rootfs 里构建，
   不再受构建机自身 glibc 版本拖累。
4. **界面固定浅色**，不跟随系统深色主题。

## 同一文件既是 GUI 也是命令

AppImage 的 `AppRun` 按第一个参数分流：无参数开界面，裸词（`list`、`health` 等）
走命令行，`--version`/`-h` 走命令行，其余以 `-` 开头的透传给界面。

## 质量门

- `pytest`：386 passed（纯内存夹具，不需要 root，`QT_QPA_PLATFORM=offscreen`）
- 在 Python 3.10 / PySide6 6.11.3（Ubuntu 22.04 rootfs，非 root 身份）下重跑：386 passed
- `tools/acceptance.py`：A1–A9 九项全部自动通过
- PyInstaller 冻结包与 AppImage 均通过 `KDD_SELFTEST=1` 自检（资源路径、`smartctl` 定位、磁盘枚举）
- 源码包解包后重跑测试：386 passed
- 产物内 `libpyside6.abi3.so.6.11` / `libshiboken6.abi3.so.6.11`（即 PySide6 6.11.3），不是 6.12

## 校验和

```
07b58558702ee1ed0624d1c4ddf76028b86ed1e16569d89e1c525d0ad3d35c83  knowdiskdrive-0.0.2-x86_64.AppImage
03c8e6fb248cdd643d5546a0e41e03f72b097c445a8ec9c39eb66526701c0ae5  knowdiskdrive-0.0.2-src.tar.gz
76fec830a53318cb462bec2733dcf18ff178cced0d75a22066781628d70c4283  knowdiskdrive-0.0.2-py3-none-any.whl
```

也写在同一目录的 `SHA256SUMS` 里，可以 `sha256sum -c SHA256SUMS`。

## 实测过的验证

- **同一份最小复现在两个版本上的对照**：Python 3.10.12 + PySide6 6.12.0 → 退出码 134
  （`Fatal Python error: none_dealloc: deallocating None`）；换成 PySide6 6.11.3 →
  同一段代码正常打印完成、退出码 0。
- AppImage 在 `env -i HOME=/tmp/x PATH=/usr/bin:/bin`（清空环境）下 `--version` 与
  `list` 均正常，产物内不含构建机 venv 路径；
- `--appimage-extract` 后 `ldd knowdiskdrive-gui` 无 "not found"（Qt 全在包内），
  `pack/glibc_check.py` 扫出最高需求 `GLIBC_2.35`（来自 `_internal/libgcc_s.so.1`）；
- 源码包解包后在没有 `.venv` 的目录里跑 `pytest` 与 `tools/acceptance.py` 均通过；
- AppImage 内嵌的 runtime 就是官方 `runtime-x86_64`（`Version: 5735cc5`），产物里
  `strings | grep appimagetool` 为 0，`--appimage-updateinfo` 输出为空——不继承别人的更新源；
- **一键授权链路实测**（本机 KDE/Wayland，先用一个没有能力位的 `smartctl` 副本制造「权限不足」）：
  `privilege.run_elevated(["setcap", ...])` 经 `pkexec` 弹出系统密码框，输入后返回 0，
  `getcap` 看到 `cap_dac_read_search,cap_sys_rawio,cap_sys_admin=ep`；
  GUI 里点「一键授权」后横幅变成「已授予读取权限，正在重新读取…」，窗口随后正常关闭。

## 已知限制

- 厂商自定义（vendor specific）属性不编中文名，显示为「厂商自定义属性 N」——硬套含义会误导；
  仅收录 smartctl 能叫出标准名或语义可交叉验证的 ID。
- USB 桥接盘的 SMART 透传能力取决于桥接芯片，`-d` 参数会按序列号缓存探测结果，
  但探测失败的盘只能显示「不支持」。

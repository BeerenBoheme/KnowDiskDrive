"""GUI 入口（提示词 5 节 + 验收 A9）。

无托盘环境（offscreen / 无 StatusNotifier 的桌面）下：
不创建托盘、关闭窗口即退出，绝不因 QSystemTrayIcon 缺失而崩溃。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ..alerts import AlertEngine
from ..config import Config, load_config
from ..history import open_store
from . import theme
from .icon import app_icon
from .main_window import MainWindow
from .tray import TrayController


# PySide6 6.12.0 在 Python < 3.12 上有已知的引用计数缺陷（Qt 的 PYSIDE-3424 /
# PYSIDE-3474）：普通的 Qt 调用就会把单例的引用计数改坏，轻则运行中崩溃，重则
# 在解释器收尾时抛 "Fatal Python error: none_dealloc: deallocating None" 并以
# SIGABRT 退出。Fedora 上「点完一键授权、关掉窗口」的崩溃就是它，跟业务代码无关
# （用 4 行的最小例子即可复现）。打包与依赖约束都钉在 6.11.x，这里再兜一层提示。
BROKEN_PYSIDE = (6, 12)
SAFE_PYTHON = (3, 12)


def _version_tuple(text: object) -> tuple[int, int, int]:
    """把 "6.12.0" / "6.11.3.post1" 之类的串宽松地转成三元组。"""
    parts: list[int] = []
    for chunk in str(text).split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def pyside_binding_problem(
    py_version: object = None, pyside_version: object = None
) -> str | None:
    """检查「PySide6 >= 6.12 且 Python < 3.12」这个会 abort 的组合。

    返回中文说明；组合安全时返回 None。参数可注入，便于测试。
    """
    if py_version is None:
        py_version = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
    if pyside_version is None:
        try:
            import PySide6

            pyside_version = PySide6.__version__
        except Exception:  # 没有 PySide6 时不用提示
            return None
    if _version_tuple(py_version) >= SAFE_PYTHON:
        return None
    if _version_tuple(pyside_version) < BROKEN_PYSIDE:
        return None
    return (
        f"警告：当前是 Python {py_version} + PySide6 {pyside_version}，"
        "这个组合存在已知的引用计数缺陷（PYSIDE-3424 / PYSIDE-3474），"
        "退出时可能异常终止。请改用 PySide6 6.11.x："
        'pip install "PySide6<6.12"，或升级到 Python 3.12 以上。'
    )


def _apply_font(app: QApplication) -> None:
    font = app.font()
    font.setPointSizeF(max(10.0, font.pointSizeF()))
    app.setFont(font)


def _install_translations(app: QApplication) -> None:
    """加载 Qt 自带的中文翻译。

    不加载时 QMessageBox 的「是/否」、QFileDialog 的按钮都会是英文，
    中文界面里会突然冒出 Yes / No。
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    candidates: list[str] = []
    try:
        candidates.append(QLibraryInfo.path(QLibraryInfo.TranslationsPath))
    except Exception:
        pass
    try:  # PySide6 自带的翻译目录（打包后也可能存在）
        import PySide6

        candidates.append(str(Path(PySide6.__file__).parent / "Qt" / "translations"))
    except Exception:
        pass
    candidates.append("/usr/share/qt6/translations")

    translator = QTranslator(app)
    for directory in candidates:
        if not directory:
            continue
        for name in ("qtbase_zh_CN", "qt_zh_CN"):
            if translator.load(name, directory) and app.installTranslator(translator):
                app._kdd_translator = translator  # 防止被回收
                return


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """创建 QApplication（已存在的实例会被复用，便于测试）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv) if argv is not None else sys.argv)
        app.setApplicationName("KnowDiskDrive")
        app.setApplicationDisplayName("KnowDiskDrive")
        app.setOrganizationName("KnowDiskDrive")
        try:
            app.setWindowIcon(app_icon())
        except Exception:  # 图标问题绝不能让 GUI 起不来
            pass
        platform_name = app.platformName().lower()
        # 托盘可用时关闭最后一个窗口只是隐藏，需要显式退出
        app.setQuitOnLastWindowClosed(not TrayController.is_available())
        if platform_name == "offscreen":
            app.setQuitOnLastWindowClosed(True)
        _apply_font(app)
        _install_translations(app)
        # 系统深色主题下 Qt 会取到深色调色板，而样式表只覆盖写了选择器的
        # 控件，剩下的（列表 viewport、滚动区等）会露出深底，和右侧白底
        # 割裂。这里先把应用调色板钉成与样式表一致的浅色。
        theme.apply_light_palette(app)
        _warn_broken_binding(app)
    return app


def _warn_broken_binding(app: QApplication) -> None:
    """已知会崩溃的 PySide6 组合：打一行提示，并把文案留给状态栏。"""
    problem = pyside_binding_problem()
    if not problem:
        return
    app._kdd_binding_warning = problem  # type: ignore[attr-defined]
    try:
        print(problem, file=sys.stderr)
    except Exception:
        pass


def run_gui(argv: Sequence[str] | None = None, config_path: str | None = None) -> int:
    """启动图形界面，返回退出码。"""
    app = create_application(argv)
    cfg, warning = load_config(config_path)

    store = None
    store_error = None
    try:
        store = open_store()
        store.purge(cfg.retention_days)
    except Exception as exc:  # 打不开历史库也要能看盘
        store_error = str(exc)
        store = None

    tray = TrayController()
    tray_built = False
    try:
        tray_built = tray.build()
    except Exception:
        tray_built = False
    if not tray_built:
        tray = None  # type: ignore[assignment]

    window = MainWindow(cfg=cfg, store=store, tray=tray)
    if store is not None:
        window.alert_engine = AlertEngine(
            store=store, dedup_minutes=cfg.alert_dedup_minutes
        )

    binding_warning = getattr(app, "_kdd_binding_warning", None)
    if binding_warning:
        window.statusBar().showMessage(binding_warning, 15000)
    if warning:
        window.statusBar().showMessage(f"配置提示：{warning}", 8000)
    if store_error:
        window.statusBar().showMessage(f"历史库不可用：{store_error}", 8000)

    window.show()
    try:
        return app.exec()
    finally:
        window.worker.stop()
        if store is not None:
            store.close()
        if tray is not None:
            tray.hide()


def main(argv: Sequence[str] | None = None) -> int:
    return run_gui(argv)

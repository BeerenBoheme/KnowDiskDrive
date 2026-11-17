"""托盘图标与通知（提示词 5.3）。

图标用温度配色（绿 / 黄 / 红），tooltip 写「型号 / 健康 / 温度」，
菜单：显示主窗口、立即刷新、暂停监控、退出。

A9：无托盘环境（offscreen、纯 Wayland 无 StatusNotifier）时
QSystemTrayIcon.isSystemTrayAvailable() 为 False —— 此时不创建托盘，
窗口关闭直接退出，绝不能因为托盘缺失而崩溃。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..health import TEMP_COLORS, TEMP_GREEN, TEMP_RED, TEMP_YELLOW, HEALTH_TEXT
from . import theme

ICON_SIZE = 64
NOTIFY_TIMEOUT_MS = 8000


def temp_icon(temp_level: str = TEMP_GREEN, letter: str = "D") -> QIcon:
    """用温度配色画一个圆角方块图标（不依赖外部图片文件）。"""
    color = TEMP_COLORS.get(temp_level, TEMP_COLORS[TEMP_GREEN])
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, ICON_SIZE - 8, ICON_SIZE - 8, 14, 14)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setPointSizeF(26.0)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, letter)
    painter.end()
    return QIcon(pixmap)


def health_icon(health_state: str) -> QIcon:
    mapping = {
        "good": TEMP_GREEN,
        "warning": TEMP_YELLOW,
        "danger": TEMP_RED,
    }
    return temp_icon(mapping.get(health_state, TEMP_GREEN))


class TrayController(QObject):
    """封装托盘的存在性判断与菜单动作。"""

    show_window_requested = Signal()
    refresh_requested = Signal()
    pause_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray: QSystemTrayIcon | None = None
        self._paused = False

    # -- 可用性 -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def tray(self) -> QSystemTrayIcon | None:
        return self._tray

    @property
    def active(self) -> bool:
        return self._tray is not None

    # -- 构建 -------------------------------------------------------------
    def build(self) -> bool:
        if self._tray is not None:
            return True
        if not self.is_available():
            return False
        tray = QSystemTrayIcon(health_icon("good"), self)
        menu = QMenu()
        self._show_action = QAction("显示主窗口", menu)
        self._refresh_action = QAction("立即刷新", menu)
        self._pause_action = QAction("暂停监控", menu)
        self._pause_action.setCheckable(True)
        self._quit_action = QAction("退出", menu)

        self._show_action.triggered.connect(self.show_window_requested.emit)
        self._refresh_action.triggered.connect(self.refresh_requested.emit)
        self._pause_action.toggled.connect(self._on_pause_toggled)
        self._quit_action.triggered.connect(self.quit_requested.emit)

        for action in (self._show_action, self._refresh_action, self._pause_action):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self._quit_action)
        self._menu = menu
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self._tray = tray
        return True

    def hide(self) -> None:
        if self._tray is not None:
            self._tray.hide()

    def _on_activated(self, reason: Any) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window_requested.emit()

    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = checked
        self._pause_action.setText("恢复监控" if checked else "暂停监控")
        self.pause_toggled.emit(checked)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if self._tray is not None:
            self._pause_action.setChecked(paused)
            self._pause_action.setText("恢复监控" if paused else "暂停监控")

    # -- 更新 -------------------------------------------------------------
    def update_tooltip(self, model: str, health_state: str, temp_c: int | None) -> None:
        if self._tray is None:
            return
        temp_text = f"{temp_c}°C" if temp_c is not None else "—"
        health_text = HEALTH_TEXT.get(health_state, health_state)
        self._tray.setToolTip(f"{model}\n健康：{health_text}\n温度：{temp_text}")
        self._tray.setIcon(health_icon(health_state))

    def notify(self, title: str, body: str) -> bool:
        """发系统通知；托盘不存在时返回 False（调用方应改用窗口内提示）。"""
        if self._tray is None:
            return False
        try:
            self._tray.showMessage(title, body, health_icon("warning"), NOTIFY_TIMEOUT_MS)
        except Exception:
            return False
        return True

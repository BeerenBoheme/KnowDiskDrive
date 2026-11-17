"""应用图标（窗口 / 托盘 / 关于框共用）。

查找顺序：包内资源 `knowdiskdrive/data/knowdiskdrive.svg`（随 wheel 发布，
PyInstaller 打包后同样可用）→ 用户目录 → 系统目录。全都找不到时用 QPainter
现场画一个同构图的图标。**任何情况下都不抛异常**——图标缺失不该让程序起不来。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

from .. import data as data_mod

APP_ICON_NAME = "knowdiskdrive"
FALLBACK_SIZE = 128


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    packaged = data_mod.icon_path()
    if packaged is not None:
        paths.append(packaged)
    # PyInstaller 单文件模式：资源被解到 sys._MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "knowdiskdrive" / "data" / f"{APP_ICON_NAME}.svg")
    # install.sh / 发行版包装装到用户目录或系统目录后的位置
    paths.extend(
        [
            Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")))
            / "icons"
            / "hicolor"
            / "scalable"
            / "apps"
            / f"{APP_ICON_NAME}.svg",
            Path("/usr/share/icons/hicolor/scalable/apps") / f"{APP_ICON_NAME}.svg",
            Path("/usr/local/share/icons/hicolor/scalable/apps") / f"{APP_ICON_NAME}.svg",
        ]
    )
    return paths


def _draw_fallback(size: int = FALLBACK_SIZE) -> QPixmap:
    """照着色 SVG 的构图重画一张，作为资源缺失时的兜底。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    scale = size / 128.0
    body = QRectF(10 * scale, 18 * scale, 108 * scale, 92 * scale)
    gradient = QLinearGradient(0, body.top(), 0, body.bottom())
    gradient.setColorAt(0.0, QColor("#3d4551"))
    gradient.setColorAt(1.0, QColor("#232a33"))
    painter.setBrush(gradient)
    painter.setPen(QColor("#12171d"))
    painter.drawRoundedRect(body, 12 * scale, 12 * scale)

    centre = body.center()
    painter.setBrush(QColor("#2f3843"))
    painter.setPen(QColor("#1b2129"))
    painter.drawEllipse(centre, 34 * scale, 34 * scale)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QColor("#3f4a58"))
    painter.drawEllipse(centre, 22 * scale, 22 * scale)
    painter.setBrush(QColor("#131920"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(centre, 11 * scale, 11 * scale)
    painter.setBrush(QColor("#8fa1b7"))
    painter.drawEllipse(centre, 4 * scale, 4 * scale)

    # 健康读数折线
    points = [
        (22, 96), (36, 96), (44, 82), (52, 96), (62, 96),
        (70, 88), (78, 96), (92, 96), (100, 90), (106, 96),
    ]
    path = QPainterPath()
    for index, (x, y) in enumerate(points):
        point = (x * scale, y * scale)
        if index == 0:
            path.moveTo(*point)
        else:
            path.lineTo(*point)
    pen = painter.pen()
    pen.setColor(QColor("#22c55e"))
    pen.setWidthF(max(2.0, 5 * scale))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#22c55e"))
    painter.drawEllipse(QRectF(99 * scale, 23 * scale, 14 * scale, 14 * scale))

    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """返回应用图标；资源缺失时回退到现场绘制，永不返回空图标。"""
    for path in _candidate_paths():
        try:
            if not path.is_file():
                continue
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
        except Exception:
            continue
    return QIcon(_draw_fallback())

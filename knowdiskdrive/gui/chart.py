"""温度曲线（提示词 7：只画温度曲线，X 轴为时间）。

用 QPainter 手绘而不用 QtCharts：QtCharts 是 extra 模块，PySide6 基础包不一定带，
而且本项目只需要一条折线，手绘更轻、更可控（也避免长时间运行掉帧）。
"""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..health import TEMP_COLORS, TEMP_GREEN, TEMP_RED, TEMP_YELLOW
from ..history import Sample, parse_iso
from . import theme

MARGIN_LEFT = 46
MARGIN_RIGHT = 12
MARGIN_TOP = 12
MARGIN_BOTTOM = 26


class TempChart(QWidget):
    """把 history.downsample() 的采样画成折线。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: list[Sample] = []
        self._thresholds: tuple[int, int] = (50, 60)
        self._placeholder = "暂无温度历史数据"
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    # -- 数据 -------------------------------------------------------------
    def set_samples(self, samples: list[Sample], thresholds: tuple[int, int] = (50, 60)) -> None:
        self._samples = list(samples)
        self._thresholds = thresholds
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def samples(self) -> list[Sample]:
        return list(self._samples)

    # -- 绘制 -------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802, D401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        area = QRectF(
            MARGIN_LEFT,
            MARGIN_TOP,
            max(10.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(10.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )
        points = [(parse_iso(s.ts_utc), s.temp_c) for s in self._samples if s.temp_c is not None]

        if not points:
            painter.setPen(QPen(QColor(theme.COLOR_MUTED)))
            painter.drawText(area, Qt.AlignCenter, self._placeholder)
            painter.end()
            return

        temps = [t for _, t in points]
        low = min(temps + [self._thresholds[0]]) - 5
        high = max(temps + [self._thresholds[1]]) + 5
        if high - low < 10:
            high = low + 10
        self._draw_grid(painter, area, low, high)
        self._draw_series(painter, area, points, low, high)
        self._draw_time_axis(painter, area, points)
        painter.end()

    def _y_for(self, area: QRectF, value: float, low: float, high: float) -> float:
        if high <= low:
            return area.bottom()
        ratio = (value - low) / (high - low)
        return area.bottom() - ratio * area.height()

    def _draw_grid(self, painter: QPainter, area: QRectF, low: float, high: float) -> None:
        painter.setPen(QPen(QColor(theme.COLOR_BORDER), 1))
        painter.drawRect(area)
        font = QFont()
        font.setPointSizeF(8.0)
        painter.setFont(font)
        steps = 4
        for i in range(steps + 1):
            value = low + (high - low) * i / steps
            y = self._y_for(area, value, low, high)
            painter.setPen(QPen(QColor(theme.COLOR_BORDER), 1, Qt.DashLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QPen(QColor(theme.COLOR_MUTED)))
            painter.drawText(QRectF(0, y - 8, MARGIN_LEFT - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{value:.0f}°")
        # 阈值参考线（4.3 的黄色上限与红色起始）
        for threshold, color in ((self._thresholds[0], TEMP_COLORS[TEMP_YELLOW]), (self._thresholds[1], TEMP_COLORS[TEMP_RED])):
            y = self._y_for(area, threshold, low, high)
            painter.setPen(QPen(QColor(color), 1, Qt.DotLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

    def _draw_series(
        self,
        painter: QPainter,
        area: QRectF,
        points: list[tuple[datetime, int]],
        low: float,
        high: float,
    ) -> None:
        first_ts = points[0][0].timestamp()
        last_ts = points[-1][0].timestamp()
        span = last_ts - first_ts

        path = QPainterPath()
        coords: list[QPointF] = []
        for index, (moment, temp) in enumerate(points):
            x = area.left() if span <= 0 else area.left() + (moment.timestamp() - first_ts) / span * area.width()
            if span <= 0:
                x = area.left() + area.width() * index / max(1, len(points) - 1)
            y = self._y_for(area, temp, low, high)
            coords.append(QPointF(x, y))

        path.moveTo(coords[0])
        for point in coords[1:]:
            path.lineTo(point)

        # 折线下方半透明填充，让趋势更醒目
        fill = QPainterPath(path)
        fill.lineTo(coords[-1].x(), area.bottom())
        fill.lineTo(coords[0].x(), area.bottom())
        fill.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TEMP_COLORS[TEMP_GREEN] + "33"))
        painter.drawPath(fill)
        painter.setBrush(Qt.NoBrush)

        painter.setPen(QPen(QColor(TEMP_COLORS[TEMP_GREEN]), 2))
        painter.drawPath(path)

        last = coords[-1]
        painter.setBrush(QColor(TEMP_COLORS[TEMP_GREEN]))
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawEllipse(last, 3.2, 3.2)
        painter.setBrush(Qt.NoBrush)

    def _draw_time_axis(self, painter: QPainter, area: QRectF, points: list[tuple[datetime, int]]) -> None:
        font = QFont()
        font.setPointSizeF(8.0)
        painter.setFont(font)
        painter.setPen(QPen(QColor(theme.COLOR_MUTED)))
        labels = [points[0], points[len(points) // 2], points[-1]] if len(points) > 2 else points
        seen: set[str] = set()
        for index, (moment, _temp) in enumerate(labels):
            text = moment.astimezone().strftime("%m-%d %H:%M")
            if text in seen:
                continue
            seen.add(text)
            x = area.left() if index == 0 else (area.right() if index == len(labels) - 1 else area.center().x())
            rect = QRectF(x - 55, area.bottom() + 4, 110, 18)
            rect.moveLeft(min(max(rect.left(), area.left()), area.right() - rect.width()))
            painter.drawText(rect, Qt.AlignCenter, text)

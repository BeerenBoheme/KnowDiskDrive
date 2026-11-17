"""属性表模型（提示词 5.2）。

列固定为 `ID | 属性名 | 当前值 | 最差值 | 阈值 | 原始值 | 状态`，
异常行加浅红/浅黄底色，**同时**在状态列写中文文字，保证色觉障碍用户也能看出异常。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QApplication, QStyledItemDelegate

from ..health import (
    ATTR_CAUTION,
    ATTR_CRITICAL,
    ATTR_STATUS_TEXT,
    HEALTH_TEXT,
    attribute_status,
    sort_attributes,
)
from ..attrnames import attribute_display_name
from ..smart import PLACEHOLDER, AtaAttribute, DiskInfo, collect_nvme_rows
from . import theme

ATTR_HEADERS = ("ID", "属性名", "当前值", "最差值", "阈值", "原始值", "状态")
NVME_HEADERS = ("项目", "数值")


class AttributeTableModel(QAbstractTableModel):
    """ATA 属性表。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[AtaAttribute] = []
        self._statuses: list[str] = []
        self._is_sata = True

    # -- 数据装载 ---------------------------------------------------------
    def set_attributes(
        self,
        attributes: list[AtaAttribute],
        is_sata: bool = True,
        pin_first: bool = True,
        pinned_ids: tuple[int, ...] = (5, 10, 194, 197, 198, 199),
    ) -> None:
        self.beginResetModel()
        self._is_sata = is_sata
        ordered = sort_attributes(attributes, pin_ids=pinned_ids, pin_first=pin_first)
        self._rows = ordered
        self._statuses = [attribute_status(attr, is_sata) for attr in ordered]
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._statuses = []
        self.endResetModel()

    # -- Qt 接口 ----------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(ATTR_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal:
            return str(section + 1) if role == Qt.DisplayRole else None
        if role == Qt.TextAlignmentRole:
            # 表头与单元格同向对齐，否则「当前值/最差值/阈值」的数字会与表头错位
            if section in (2, 3, 4):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role != Qt.DisplayRole:
            return None
        return ATTR_HEADERS[section] if 0 <= section < len(ATTR_HEADERS) else None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        attr = self._rows[index.row()]
        status = self._statuses[index.row()]
        status_text = ATTR_STATUS_TEXT.get(status, status)

        if role == Qt.DisplayRole:
            return self._text(attr, index.column(), status)
        if role == Qt.TextAlignmentRole:
            # ID 是标识符按左对齐，数值列右对齐便于按位数比较
            if index.column() in (2, 3, 4):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.BackgroundRole:
            if status == ATTR_CRITICAL:
                return QColor(theme.COLOR_ROW_CRITICAL)
            if status == ATTR_CAUTION:
                return QColor(theme.COLOR_ROW_CAUTION)
            return None
        if role == Qt.ForegroundRole:
            if status == ATTR_CRITICAL:
                return QColor(theme.COLOR_DANGER)
            return None
        if role == Qt.ToolTipRole:
            return (
                f"{attr.id} {attribute_display_name(attr.id, attr.name)}"
                f"（{attr.name}）｜原始值 {attr.raw_display()}｜状态 {status_text}"
            )
        return None

    def _text(self, attr: AtaAttribute, column: int, status: str) -> str:
        if column == 0:
            return str(attr.id)
        if column == 1:
            return attribute_display_name(attr.id, attr.name)
        if column == 2:
            return _int_text(attr.value)
        if column == 3:
            return _int_text(attr.worst)
        if column == 4:
            return _int_text(attr.thresh)
        if column == 5:
            return attr.raw_display()
        if column == 6:
            return ATTR_STATUS_TEXT.get(status, status)
        return ""

    # -- 供复制信息使用 ---------------------------------------------------
    def rows_text(self) -> list[str]:
        return [
            "\t".join(self._text(attr, col, self._statuses[i]) for col in range(len(ATTR_HEADERS)))
            for i, attr in enumerate(self._rows)
        ]


class NvmeLogModel(QAbstractTableModel):
    """NVMe 盘没有 ATA 属性表，改用键值表（提示词 5.2）。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str]] = []

    def set_info(self, info: DiskInfo | None) -> None:
        self.beginResetModel()
        self._rows = collect_nvme_rows(info.nvme_log if info else None)
        self.endResetModel()

    def set_rows(self, rows: list[tuple[str, str]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(NVME_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return NVME_HEADERS[section] if 0 <= section < len(NVME_HEADERS) else None
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        key, value = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return key if index.column() == 0 else value
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def rows_text(self) -> list[str]:
        return [f"{key}\t{value}" for key, value in self._rows]


def _int_text(value: int | None) -> str:
    return PLACEHOLDER if value is None else str(value)


def health_badge_text(report: Any) -> str:
    """顶部信息条的健康徽章文字：「● 良好」。"""
    return f"● {HEALTH_TEXT.get(report.state, report.state)}"


class MultiLineItemDelegate(QStyledItemDelegate):
    """按文本行数计算行高的委托。

    Qt 默认按单行文字算 sizeHint，含换行的 item（设备列表是「设备名 / 型号 /
    序列号」三行）会被压成一行高度，三行字挤在一起。绕开的办法不是在 item 上
    setSizeHint：宽度给 -1 会被 Qt 当成无效尺寸直接忽略，而写死宽度在窗口拉伸
    后又不对，所以放在委托里按字体实际行高算。
    """

    def __init__(self, extra_padding: int = 16, parent: Any = None) -> None:
        super().__init__(parent)
        self._extra_padding = extra_padding

    def sizeHint(self, option: Any, index: QModelIndex) -> Any:  # noqa: N802
        size = super().sizeHint(option, index)
        text = str(index.data(Qt.DisplayRole) or "")
        lines = max(1, text.count("\n") + 1)
        metrics = option.fontMetrics
        if metrics.lineSpacing() <= 0:
            metrics = QFontMetrics(QApplication.font())
        size.setHeight(metrics.lineSpacing() * lines + self._extra_padding)
        return size

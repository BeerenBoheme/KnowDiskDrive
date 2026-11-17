"""界面配色与样式表。

健康色直接复用 health.py（提示词 5.1：良好 #22c55e / 警告 #f59e0b / 危险 #ef4444），
保证 CLI、GUI、托盘三处对同一状态用同一颜色。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from ..health import HEALTH_COLORS, TEMP_COLORS, HEALTH_TEXT

COLOR_GOOD = HEALTH_COLORS["good"]
COLOR_WARNING = HEALTH_COLORS["warning"]
COLOR_DANGER = HEALTH_COLORS["danger"]
COLOR_UNKNOWN = HEALTH_COLORS["unknown"]

#: Qt 样式表里用的中性色
COLOR_TEXT = "#111827"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#e5e7eb"
COLOR_BG = "#f9fafb"
COLOR_BANNER_WARN = "#fef3c7"
COLOR_BANNER_ERROR = "#fee2e2"
COLOR_ROW_CRITICAL = "#fee2e2"
COLOR_ROW_CAUTION = "#fef9c3"

#: 中性色的 QColor 版本（QPalette 用）
COLOR_BASE = "#ffffff"
COLOR_ALTERNATE_BASE = "#fafafa"
COLOR_SELECTED = "#dbeafe"
COLOR_LINK = "#2563eb"

#: 中文字体优先级（Linux 常见 CJK 字体）
FONT_FAMILIES = (
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Sarasa Gothic SC",
    "sans-serif",
)

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {COLOR_BG}; }}
QWidget {{ color: {COLOR_TEXT}; }}
QLabel#Headline {{ font-size: 20px; font-weight: 600; }}
QLabel#Subline {{ color: {COLOR_MUTED}; }}
QLabel#Badge {{ font-size: 15px; font-weight: 600; padding: 2px 10px; border-radius: 9px; }}
/* 兜底：任何 item view（列表 / 表格 / 树）都显式给白底与浅色选中。样式表对
   viewport 的绘制不如对调色板可靠，深色主题下多写这一句能少踩坑。 */
QAbstractItemView {{
    background: #ffffff;
    alternate-background-color: #fafafa;
    color: {COLOR_TEXT};
    selection-background-color: #dbeafe;
    selection-color: {COLOR_TEXT};
}}
QTableView {{
    background: #ffffff;
    alternate-background-color: #fafafa;
    gridline-color: {COLOR_BORDER};
    selection-background-color: #dbeafe;
    selection-color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
}}
/* 滚动条不占边框：轨道透明、与边框留出内缩，避免压住最后一行的边线 */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 3px 2px 3px 0;
}}
QScrollBar::handle:vertical {{
    background: #c7cbd1;
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #9ca3af; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0 3px 2px 3px;
}}
QScrollBar::handle:horizontal {{
    background: #c7cbd1;
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: #9ca3af; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
QListWidget {{
    background: #ffffff;
    alternate-background-color: #fafafa;
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    outline: 0;
}}
QListWidget::item {{
    color: {COLOR_TEXT};
    padding: 5px 8px;
    border-radius: 4px;
}}
QListWidget::item:hover {{ background: #f3f4f6; }}
QListWidget::item:selected {{ background: #dbeafe; color: {COLOR_TEXT}; }}
QHeaderView::section {{
    background: #f3f4f6;
    padding: 4px 6px;
    border: 0;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    font-weight: 600;
}}
QPushButton {{
    padding: 5px 14px;
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    background: #ffffff;
}}
QPushButton:hover {{ background: #f3f4f6; }}
QPushButton:disabled {{ color: {COLOR_MUTED}; background: #f3f4f6; }}
QComboBox {{ padding: 4px 8px; border: 1px solid {COLOR_BORDER}; border-radius: 6px; background: #ffffff; }}
QComboBox QAbstractItemView {{
    background: #ffffff;
    color: {COLOR_TEXT};
    selection-background-color: #dbeafe;
    selection-color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
}}
QFrame#Banner {{ border: 1px solid #fbbf24; border-radius: 6px; padding: 8px; }}
QStatusBar {{ color: {COLOR_MUTED}; }}

/* 下面这些控件只在对话框 / 托盘菜单里出现。系统是深色主题时它们会落到深色
   调色板上，与这里固定的浅色样式表混在一起会出现「深底深字」，所以统一显式
   给出浅色底与深色字。 */
QCheckBox {{ color: {COLOR_TEXT}; }}
QMessageBox {{ background: {COLOR_BG}; }}
QMessageBox QLabel {{ color: {COLOR_TEXT}; }}
QFileDialog {{ background: {COLOR_BG}; }}
QFileDialog QListView, QFileDialog QTreeView, QFileDialog QTableView {{
    background: #ffffff;
    alternate-background-color: #fafafa;
    color: {COLOR_TEXT};
    selection-background-color: #dbeafe;
    selection-color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
}}
QFileDialog QLineEdit, QFileDialog QComboBox {{
    background: #ffffff;
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 3px 6px;
}}
QLineEdit {{ background: #ffffff; color: {COLOR_TEXT}; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 3px 6px; }}
QMenu {{ background: #ffffff; color: {COLOR_TEXT}; border: 1px solid {COLOR_BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: #dbeafe; color: {COLOR_TEXT}; }}
QMenu::separator {{ height: 1px; background: {COLOR_BORDER}; margin: 4px 8px; }}
QToolTip {{ background: #ffffff; color: {COLOR_TEXT}; border: 1px solid {COLOR_BORDER}; padding: 4px 6px; }}
"""

def build_palette() -> QPalette:
    """构造与上面浅色样式表一致的调色板。

    为什么需要它：样式表只覆盖它写了选择器的控件，其余控件（列表/树的
    viewport、滚动区、右键菜单里的原生子控件等）仍然按应用调色板绘制。
    在 KDE / GNOME 的深色主题下，Qt 会从系统里取到深色的 Base/Window，
    于是同一窗口里会出现「右侧白底、左侧深底浅字」的割裂——实测深色值
    就是 Breeze 的 (38, 41, 47)。把应用调色板显式设成浅色，等于给所有
    没有被样式表点名的控件兜一层底，不再依赖用户当前的主题。
    """
    palette = QPalette()
    base = QColor(COLOR_BASE)
    text = QColor(COLOR_TEXT)
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG))
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_ALTERNATE_BASE))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_MUTED))
    palette.setColor(QPalette.ColorRole.Button, base)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_SELECTED))
    palette.setColor(QPalette.ColorRole.HighlightedText, text)
    palette.setColor(QPalette.ColorRole.Link, QColor(COLOR_LINK))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.Mid, QColor(COLOR_BORDER))
    palette.setColor(QPalette.ColorRole.Midlight, QColor("#f3f4f6"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#9ca3af"))
    palette.setColor(QPalette.ColorRole.Light, base)
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#d1d5db"))
    # 失效态用灰字，避免深色主题下失效按钮又变回浅字
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLOR_MUTED))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, base)
    return palette


def apply_light_palette(app: object) -> None:
    """把浅色调色板装到 QApplication 上（已在测试里单独验证，故允许 stub）。"""
    try:
        setter = getattr(app, "setPalette", None)
        if callable(setter):
            setter(build_palette())
    except Exception:  # 调色板失败也不能让界面起不来
        pass


__all__ = [
    "COLOR_GOOD",
    "COLOR_WARNING",
    "COLOR_DANGER",
    "COLOR_UNKNOWN",
    "COLOR_TEXT",
    "COLOR_MUTED",
    "COLOR_BORDER",
    "COLOR_BG",
    "COLOR_BANNER_WARN",
    "COLOR_BANNER_ERROR",
    "COLOR_ROW_CRITICAL",
    "COLOR_ROW_CAUTION",
    "COLOR_BASE",
    "COLOR_ALTERNATE_BASE",
    "COLOR_SELECTED",
    "FONT_FAMILIES",
    "STYLESHEET",
    "build_palette",
    "apply_light_palette",
    "HEALTH_TEXT",
    "TEMP_COLORS",
]

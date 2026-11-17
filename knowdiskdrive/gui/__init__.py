"""PySide6 图形界面（提示词第 5 节）。

模块划分：

* `theme`      —— 颜色与样式表；
* `workers`    —— QThread 读盘工作线程（主线程绝不直接调用 smartctl）；
* `models`     —— 属性表 / NVMe 键值表的 Qt 模型；
* `chart`      —— QPainter 手绘温度曲线；
* `monitor`    —— pyudev 热插拔监听；
* `tray`       —— 托盘图标与通知；
* `main_window`—— 主窗口；
* `app`        —— 应用入口。
"""

from __future__ import annotations

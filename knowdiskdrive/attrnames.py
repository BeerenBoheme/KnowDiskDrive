"""ATA SMART 属性的中文名（提示词 5.2 的「属性名」列）。

smartctl 给出的原始名是英文下划线风格（`Reallocated_Sector_Ct`），
中文界面里直接展示对普通用户不友好，因此这里维护一份常用属性的中文名。
未收录的属性原样显示英文名，保证「不知道就照抄厂家给的」而不是乱翻。

收录原则（重要）：只收 smartctl 自己能叫出标准名的 ID，或语义可交叉验证的
ID（如 231 的归一化值与原始值互补）。smartctl 报 `Unknown_Attribute` /
`Unknown_SSD_Attribute` 的 ID 一律不编中文名 —— 厂商在这些 ID 上存什么是
厂商自由，硬套一个含义会误导用户（实测某国产 SATA SSD 的 234 号属性原始值
是 4000 亿量级的计数器，按「平均擦除次数」解释完全说不通）。
"""

from __future__ import annotations

#: 常用 ATA/SATA 属性 ID → 中文名
ATA_ATTRIBUTE_NAMES: dict[int, str] = {
    1: "底层读取错误率",
    2: "吞吐性能",
    3: "主轴起转时间",
    4: "启停次数",
    5: "重映射扇区数",
    7: "寻道错误率",
    8: "寻道时间性能",
    9: "通电时间",
    10: "起转重试次数",
    11: "校准重试次数",
    12: "通电次数",
    13: "软读取错误率",
    22: "氦气密封检测",
    175: "芯片级程序失败计数",
    177: "磨损均衡计数",
    178: "退役块计数",
    179: "已用保留块计数",
    181: "程序失败计数",
    182: "擦除失败计数",
    183: "运行期坏块数",
    184: "端到端错误检测",
    187: "不可纠正错误数",
    188: "已超时命令数",
    189: "高频写入/读取错误",
    190: "气流温度",
    191: "掉电加速度",
    192: "断电回收计数",
    193: "负载循环计数",
    194: "温度",
    195: "硬件 ECC 已恢复错误",
    196: "重分配事件计数",
    197: "待映射扇区数",
    198: "离线不可纠正扇区数",
    199: "UDMA CRC 错误数",
    200: "多区域错误率",
    201: "软读取错误率",
    202: "数据地址标记错误",
    203: "运行期 ECC 错误",
    204: "软 ECC 纠正",
    205: "热敏电阻异常",
    212: "震动传感器",
    213: "写入错误率",
    220: "磁盘偏移量",
    221: "晃动高度",
    222: "加载摩擦",
    223: "加载/卸载重试",
    224: "加载摩擦（原始）",
    225: "负载/卸载循环",
    226: "负载摩擦",
    # 231 在 smartctl 里报 Unknown_SSD_Attribute，但各厂商一致用来表示
    # 「剩余寿命百分比」：归一化值 70 与原始值 30 互补为 100，可交叉验证。
    231: "SSD 剩余寿命",
    232: "可用保留空间",
    233: "介质磨损指示",
    235: "上电异常计数",
    240: "磁头飞行时间",
    241: "写入总量",
    242: "读取总量",
    250: "读取错误重试率",
}


def _is_unknown_name(name: str) -> bool:
    """smartctl 用 Unknown_Attribute / Unknown_SSD_Attribute 这类名字表示「我也不认识」。"""
    lowered = (name or "").strip().lower()
    return lowered.startswith("unknown")


def attribute_display_name(attr_id: int, fallback: str | None = None) -> str:
    """中文名优先；未收录时回退到 smartctl 给的英文名，仍未知则标为厂商自定义。"""
    chinese = ATA_ATTRIBUTE_NAMES.get(attr_id)
    if chinese:
        return chinese
    original = (fallback or "").strip()
    if original and not _is_unknown_name(original):
        return original
    return f"厂商自定义属性 {attr_id}"


def attribute_display_with_english(attr_id: int, name: str | None) -> str:
    """表格里用的名字：中文（英文原名），没有中文名时只用英文。"""
    chinese = ATA_ATTRIBUTE_NAMES.get(attr_id)
    original = (name or "").strip()
    if _is_unknown_name(original):
        original = ""
    if chinese and original:
        return f"{chinese}（{original}）"
    if chinese:
        return chinese
    if original:
        return original
    return f"厂商自定义属性 {attr_id}"

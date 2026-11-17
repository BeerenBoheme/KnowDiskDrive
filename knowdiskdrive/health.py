"""健康判定规则（提示词第 4 节，唯一真源）。

**不要自己发明规则。** 本模块严格按提示词 4.1 / 4.2 / 4.3 实现，另有单测覆盖。

* 4.1 单条属性状态：按顺序判定，命中即停；
* 4.2 整盘健康：取最差一级；
* 4.3 温度阈值：按介质（HDD / SATA SSD / NVMe），边界值归黄色。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .attrnames import attribute_display_name
from .config import DEFAULT_CONFIG, Config, TempThreshold
from .smart import (
    AtaAttribute,
    DiskInfo,
    STATUS_NO_SMART,
    STATUS_OK,
    STATUS_STANDBY,
)

# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------
ATTR_NORMAL = "normal"  # 正常
ATTR_CAUTION = "caution"  # 注意
ATTR_CRITICAL = "critical"  # 临界

ATTR_STATUS_TEXT: dict[str, str] = {
    ATTR_NORMAL: "正常",
    ATTR_CAUTION: "注意",
    ATTR_CRITICAL: "临界",
}

#: 整盘健康级别（取最差一级）
HEALTH_GOOD = "good"  # 良好
HEALTH_WARNING = "warning"  # 警告
HEALTH_DANGER = "danger"  # 危险
HEALTH_UNKNOWN = "unknown"  # 无法判定（读取失败 / 休眠 / 无 SMART）

HEALTH_TEXT: dict[str, str] = {
    HEALTH_GOOD: "良好",
    HEALTH_WARNING: "警告",
    HEALTH_DANGER: "危险",
    HEALTH_UNKNOWN: "无法判定",
}

#: 健康状态配色（提示词 5.1）
HEALTH_COLORS: dict[str, str] = {
    HEALTH_GOOD: "#22c55e",
    HEALTH_WARNING: "#f59e0b",
    HEALTH_DANGER: "#ef4444",
    HEALTH_UNKNOWN: "#6b7280",
}

#: 温度等级
TEMP_GREEN = "green"
TEMP_YELLOW = "yellow"
TEMP_RED = "red"

TEMP_COLORS: dict[str, str] = {
    TEMP_GREEN: "#22c55e",
    TEMP_YELLOW: "#f59e0b",
    TEMP_RED: "#ef4444",
}

TEMP_TEXT: dict[str, str] = {
    TEMP_GREEN: "正常",
    TEMP_YELLOW: "偏高",
    TEMP_RED: "过高",
}

#: 4.1 中「原始值 > 0 即为注意」的属性 ID
RAW_GT_ZERO_IDS: frozenset[int] = frozenset({5, 10, 197, 198})
#: 4.1 中仅 SATA 适用的属性 ID（199 / 200）
RAW_GT_ZERO_SATA_IDS: frozenset[int] = frozenset({199, 200})

#: 4.1 第三个条件：value <= thresh + 5 判为注意
CAUTION_MARGIN = 5

#: 4.2：注意属性数量达到该值即整盘警告
CAUTION_COUNT_FOR_WARNING = 3

#: 4.2：NVMe 寿命与备用空间阈值
NVME_PERCENTAGE_USED_WARNING = 90
NVME_AVAILABLE_SPARE_WARNING = 10

#: 「临界」属性会触发告警通知（5.3 / A5）
_SEVERITY_ORDER = {ATTR_NORMAL: 0, ATTR_CAUTION: 1, ATTR_CRITICAL: 2}
_HEALTH_ORDER = {HEALTH_GOOD: 0, HEALTH_WARNING: 1, HEALTH_DANGER: 2}
_TEMP_ORDER = {TEMP_GREEN: 0, TEMP_YELLOW: 1, TEMP_RED: 2}


class HealthState(str, Enum):
    """整盘健康级别（便于比较与序列化）。"""

    GOOD = HEALTH_GOOD
    WARNING = HEALTH_WARNING
    DANGER = HEALTH_DANGER

    @property
    def text(self) -> str:
        return HEALTH_TEXT[self.value]

    @property
    def color(self) -> str:
        return HEALTH_COLORS[self.value]


def worst_health(*states: str) -> str:
    """取最差一级健康状态，忽略未知值。"""
    best = HEALTH_GOOD
    for state in states:
        if state in _HEALTH_ORDER and _HEALTH_ORDER[state] > _HEALTH_ORDER[best]:
            best = state
    return best


def worse_health(a: str, b: str) -> str:
    return worst_health(a, b)


# ---------------------------------------------------------------------------
# 4.1 单条属性状态
# ---------------------------------------------------------------------------
def attribute_status(attr: AtaAttribute, is_sata: bool = True) -> str:
    """按提示词 4.1 判定单条属性状态，命中即停。

    顺序：
    1. when_failed 为 failing_now / failing_past -> 临界
    2. thresh > 0 且 value <= thresh -> 临界
    3. thresh > 0 且 value <= thresh + 5 -> 注意
    4. ID ∈ {5, 10, 197, 198} 且 raw.value > 0 -> 注意
    5. ID ∈ {199, 200} 且 raw.value > 0（仅 SATA）-> 注意
    6. 其他 -> 正常
    """
    when_failed = (attr.when_failed or "").strip().lower()
    if when_failed in {"failing_now", "failing_past"}:
        return ATTR_CRITICAL

    thresh = attr.thresh
    value = attr.value
    if thresh is not None and value is not None and thresh > 0:
        if value <= thresh:
            return ATTR_CRITICAL
        if value <= thresh + CAUTION_MARGIN:
            return ATTR_CAUTION

    raw = attr.raw
    if raw is not None and raw > 0:
        if attr.id in RAW_GT_ZERO_IDS:
            return ATTR_CAUTION
        if is_sata and attr.id in RAW_GT_ZERO_SATA_IDS:
            return ATTR_CAUTION

    return ATTR_NORMAL


def attribute_status_text(attr: AtaAttribute, is_sata: bool = True) -> str:
    return ATTR_STATUS_TEXT[attribute_status(attr, is_sata)]


# ---------------------------------------------------------------------------
# 4.2 整盘健康
# ---------------------------------------------------------------------------
@dataclass
class HealthReport:
    """一次健康判定的完整结果。"""

    state: str = HEALTH_GOOD
    reasons: list[str] = field(default_factory=list)
    critical_attrs: list[AtaAttribute] = field(default_factory=list)
    caution_attrs: list[AtaAttribute] = field(default_factory=list)
    temp_level: str = TEMP_GREEN
    temp_state: str = "unknown"  # "unknown" 表示无温度数据
    notes: list[str] = field(default_factory=list)

    @property
    def state_text(self) -> str:
        return HEALTH_TEXT.get(self.state, HEALTH_TEXT[HEALTH_GOOD])

    @property
    def color(self) -> str:
        return HEALTH_COLORS.get(self.state, HEALTH_COLORS[HEALTH_GOOD])

    @property
    def is_healthy(self) -> bool:
        return self.state == HEALTH_GOOD

    def reason_text(self) -> str:
        """把触发原因拼成一行中文，供 tooltip / 复制信息使用。"""
        return "；".join(self.reasons) if self.reasons else "未发现异常"


def nvme_critical_warning(info: DiskInfo) -> int | None:
    """读取 NVMe critical_warning，缺失返回 None。"""
    log = info.nvme_log or {}
    value = log.get("critical_warning")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def evaluate(info: DiskInfo, cfg: Config = DEFAULT_CONFIG) -> HealthReport:
    """按提示词第 4 节判定整盘健康（取最差一级）。"""
    report = HealthReport()
    reasons: list[str] = []

    # 数据本身不可用时不要猜健康等级（提示词 8：缺失不补 0，也不补“良好”）
    if info.status not in {STATUS_OK, STATUS_STANDBY}:
        report.state = HEALTH_UNKNOWN
        report.reasons = [f"数据不可用：{info.status_text}"]
        report.temp_level, report.temp_state = classify_temperature(
            None, _temp_threshold_for(info, cfg)
        )
        return report
    if info.status == STATUS_STANDBY:
        report.state = HEALTH_UNKNOWN
        report.reasons = ["磁盘休眠中，本轮未唤醒（-n standby 策略）"]
        report.temp_level, report.temp_state = classify_temperature(
            None, _temp_threshold_for(info, cfg)
        )
        return report

    # ---------- 4.2 危险条件 ----------
    if info.smart_passed is False:
        reasons.append("SMART 整体健康自评为失败（smart_status.passed = false）")

    is_sata = not info.is_nvme
    critical_attrs: list[AtaAttribute] = []
    caution_attrs: list[AtaAttribute] = []
    for attr in info.ata_attributes:
        status = attribute_status(attr, is_sata=is_sata)
        if status == ATTR_CRITICAL:
            critical_attrs.append(attr)
            if (attr.when_failed or "").strip().lower() == "failing_now":
                reasons.append(f"属性 {attr.id}（{attr.name}）当前失败（failing_now）")
        elif status == ATTR_CAUTION:
            caution_attrs.append(attr)

    critical_warning = nvme_critical_warning(info)
    if critical_warning:
        reasons.append(f"NVMe 严重警告标志非零（critical_warning = {critical_warning}）")

    # ---------- 4.2 警告条件 ----------
    if critical_attrs:
        names = "、".join(f"{a.id} {a.name}" for a in critical_attrs[:5])
        reasons.append(f"存在临界属性：{names}")

    log = info.nvme_log or {}
    media_errors = _safe_int(log.get("media_errors"))
    if media_errors is not None and media_errors > 0:
        reasons.append(f"NVMe 介质错误数 {media_errors}")

    percentage_used = _safe_int(log.get("percentage_used"))
    if percentage_used is not None and percentage_used >= NVME_PERCENTAGE_USED_WARNING:
        reasons.append(
            f"NVMe 寿命消耗 {percentage_used}%，达到 {NVME_PERCENTAGE_USED_WARNING}% 阈值"
        )

    available_spare = _safe_int(log.get("available_spare"))
    if available_spare is not None and available_spare < NVME_AVAILABLE_SPARE_WARNING:
        reasons.append(f"NVMe 可用备用空间仅 {available_spare}%，低于 {NVME_AVAILABLE_SPARE_WARNING}% 阈值")

    if len(caution_attrs) >= CAUTION_COUNT_FOR_WARNING:
        reasons.append(f"注意级属性共 {len(caution_attrs)} 条，达到 {CAUTION_COUNT_FOR_WARNING} 条阈值")

    # ---------- 汇总 ----------
    if info.smart_passed is False or critical_warning or any(
        (a.when_failed or "").strip().lower() == "failing_now" for a in info.ata_attributes
    ):
        report.state = HEALTH_DANGER
    elif (
        critical_attrs
        or (media_errors is not None and media_errors > 0)
        or (percentage_used is not None and percentage_used >= NVME_PERCENTAGE_USED_WARNING)
        or (available_spare is not None and available_spare < NVME_AVAILABLE_SPARE_WARNING)
        or len(caution_attrs) >= CAUTION_COUNT_FOR_WARNING
    ):
        report.state = HEALTH_WARNING
    else:
        report.state = HEALTH_GOOD

    # ---------- 4.3 温度 ----------
    report.temp_level, report.temp_state = classify_temperature(
        info.temperature_c, _temp_threshold_for(info, cfg)
    )
    if report.temp_level == TEMP_RED and info.temperature_c is not None:
        reasons.append(f"温度 {info.temperature_c}°C 已进入红色区间")

    if info.status == STATUS_NO_SMART:
        report.notes.append("该设备不支持 SMART")
    if info.parse_mode == "text":
        report.notes.append("兼容模式解析")

    report.reasons = reasons
    report.critical_attrs = critical_attrs
    report.caution_attrs = caution_attrs
    return report


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _temp_threshold_for(info: DiskInfo, cfg: Config) -> TempThreshold:
    return cfg.temp_threshold(info.media)


# ---------------------------------------------------------------------------
# 4.3 温度阈值
# ---------------------------------------------------------------------------
def classify_temperature(
    temp_c: int | None, threshold: TempThreshold
) -> tuple[str, str]:
    """按提示词 4.3 判定温度等级。

    区间写法必须明确：`green_below <= t <= yellow_max` 为黄，即边界值归黄色。

    返回 `(等级, 状态)`；无温度数据时返回 `(TEMP_GREEN, "unknown")`，不猜温度。
    """
    if temp_c is None:
        return TEMP_GREEN, "unknown"
    if temp_c < threshold.green_below:
        return TEMP_GREEN, TEMP_GREEN
    if temp_c <= threshold.yellow_max:
        return TEMP_YELLOW, TEMP_YELLOW
    return TEMP_RED, TEMP_RED


def temperature_level(temp_c: int | None, media: str, cfg: Config = DEFAULT_CONFIG) -> str:
    """按介质判定温度等级（便捷封装）。"""
    level, _ = classify_temperature(temp_c, cfg.temp_threshold(media))
    return level


def temperature_color(temp_c: int | None, media: str, cfg: Config = DEFAULT_CONFIG) -> str | None:
    """温度数字的颜色；无数据返回 None（UI 用默认色）。"""
    if temp_c is None:
        return None
    return TEMP_COLORS[temperature_level(temp_c, media, cfg)]


# ---------------------------------------------------------------------------
# 汇总视图（CLI / GUI 共用）
# ---------------------------------------------------------------------------
@dataclass
class DiskSummary:
    """一块磁盘的健康概要，CLI 与 GUI 都消费这个结构。"""

    info: DiskInfo
    report: HealthReport

    @property
    def health(self) -> str:
        return self.report.state

    @property
    def health_text(self) -> str:
        return self.report.state_text

    @property
    def attributes(self) -> list[AtaAttribute]:
        return self.info.ata_attributes


def summarize(info: DiskInfo, cfg: Config = DEFAULT_CONFIG) -> DiskSummary:
    """生成健康概要。"""
    return DiskSummary(info=info, report=evaluate(info, cfg))


def sort_attributes(
    attributes: Iterable[AtaAttribute],
    pin_ids: tuple[int, ...] = (),
    pin_first: bool = True,
) -> list[AtaAttribute]:
    """按 ID 升序排列；pin_first 时把 pin_ids 里的属性置顶（提示词 5.2）。"""
    pinned_set = set(pin_ids) if pin_first else set()
    pinned = [a for a in attributes if a.id in pinned_set]
    rest = [a for a in attributes if a.id not in pinned_set]
    pinned.sort(key=lambda a: a.id)
    rest.sort(key=lambda a: a.id)
    return pinned + rest


def copy_text(summary: DiskSummary) -> str:
    """「复制信息」按钮的内容（提示词 5.4）：纯文本，不渲染富文本。"""
    info = summary.info
    report = summary.report
    lines = [
        f"型号：{info.display_model}",
        f"序列号：{info.display_serial}",
        f"设备：{info.device}",
        f"健康：{report.state_text}（{report.reason_text()}）",
    ]
    temp_text = f"{info.temperature_c}°C" if info.temperature_c is not None else "—"
    lines.append(f"温度：{temp_text}")
    if info.power_on_hours is not None:
        lines.append(f"通电时间：{info.power_on_hours} 小时")
    if info.power_cycle_count is not None:
        lines.append(f"通电次数：{info.power_cycle_count}")
    abnormal = [
        f"{a.id} {attribute_display_name(a.id, a.name)}"
        f"（{ATTR_STATUS_TEXT[attribute_status(a, not info.is_nvme)]}）"
        for a in info.ata_attributes
        if attribute_status(a, not info.is_nvme) != ATTR_NORMAL
    ]
    lines.append("异常属性：" + ("、".join(abnormal) if abnormal else "无"))
    return "\n".join(lines)

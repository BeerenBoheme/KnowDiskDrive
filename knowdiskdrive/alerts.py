"""告警判定与去重（提示词 2.3 状态机 + 5.3 通知时机）。

通知只在三种「变化」发生时报，不做轮询式刷屏：

1. 健康状态降级（良好→警告、警告→危险、良好→危险）；
2. 温度进入红区（4.3 阈值）；
3. 某个属性从正常/注意变成「临界」（新出现的临界属性）。

同一事件 30 分钟内不重复（可配置）。去重记录优先写进 history.db 的 events 表，
这样应用重启后不会立刻把同一件事再通知一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .health import (
    HEALTH_DANGER,
    HEALTH_GOOD,
    HEALTH_TEXT,
    HEALTH_WARNING,
    TEMP_RED,
    HealthReport,
)
from .history import to_iso, utc_now
from .smart import DiskInfo

#: 健康等级排序，用于判定「降级」。unknown 不参与比较。
HEALTH_RANK = {HEALTH_GOOD: 0, HEALTH_WARNING: 1, HEALTH_DANGER: 2}

KIND_HEALTH_DOWN = "health_downgrade"
KIND_TEMP_RED = "temperature_red"
KIND_ATTR_CRITICAL = "attribute_critical"


@dataclass(frozen=True)
class Alert:
    serial: str
    kind: str
    title: str
    body: str
    dedup_key: str


class AlertEngine:
    """有状态：记住上一次的健康等级与临界属性集合。"""

    def __init__(
        self,
        store: object | None = None,
        dedup_minutes: int = 30,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.dedup_window = timedelta(minutes=max(0, dedup_minutes))
        self._now = now_fn or utc_now
        self._prev_health: dict[str, str] = {}
        self._prev_critical: dict[str, frozenset[int]] = {}
        self._last_fired: dict[tuple[str, str], datetime] = {}

    # -- 对外接口 ---------------------------------------------------------
    def evaluate(self, serial: str, info: DiskInfo, report: HealthReport) -> list[Alert]:
        now = self._now()
        model = info.display_model or serial
        alerts: list[Alert] = []

        prev_health = self._prev_health.get(serial)
        if prev_health is not None and prev_health in HEALTH_RANK and report.state in HEALTH_RANK:
            if HEALTH_RANK[report.state] > HEALTH_RANK[prev_health]:
                key = f"{KIND_HEALTH_DOWN}:{prev_health}->{report.state}"
                if self._fire(serial, KIND_HEALTH_DOWN, key, now, report.reason_text()):
                    alerts.append(
                        Alert(
                            serial=serial,
                            kind=KIND_HEALTH_DOWN,
                            title=f"{model} 健康状态下降",
                            body=f"{HEALTH_TEXT.get(prev_health, prev_health)} → "
                            f"{HEALTH_TEXT.get(report.state, report.state)}\n{report.reason_text()}",
                            dedup_key=key,
                        )
                    )
        self._prev_health[serial] = report.state

        if report.temp_level == TEMP_RED and info.temperature_c is not None:
            key = f"{KIND_TEMP_RED}:{info.temperature_c}"
            if self._fire(serial, KIND_TEMP_RED, KIND_TEMP_RED, now, f"温度 {info.temperature_c}°C"):
                alerts.append(
                    Alert(
                        serial=serial,
                        kind=KIND_TEMP_RED,
                        title=f"{model} 温度过高",
                        body=f"当前温度 {info.temperature_c}°C 已进入红区，建议检查散热或降低负载。",
                        dedup_key=key,
                    )
                )

        current_critical = frozenset(attr.id for attr in report.critical_attrs)
        previous_critical = self._prev_critical.get(serial)
        if previous_critical is not None:
            for attr_id in sorted(current_critical - previous_critical):
                key = f"{KIND_ATTR_CRITICAL}:{attr_id}"
                detail = f"属性 {attr_id} 变为临界"
                if self._fire(serial, KIND_ATTR_CRITICAL, key, now, detail):
                    alerts.append(
                        Alert(
                            serial=serial,
                            kind=KIND_ATTR_CRITICAL,
                            title=f"{model} 出现临界属性",
                            body=f"SMART 属性 {attr_id} 变为临界值，请尽快备份数据并考虑更换磁盘。",
                            dedup_key=key,
                        )
                    )
        self._prev_critical[serial] = current_critical

        return alerts

    def forget(self, serial: str) -> None:
        """设备拔出时清掉它的历史记忆，重插后重新建立基线。"""
        self._prev_health.pop(serial, None)
        self._prev_critical.pop(serial, None)

    # -- 内部 -------------------------------------------------------------
    def _fire(self, serial: str, kind: str, dedup_key: str, now: datetime, detail: str | None) -> bool:
        """返回 True 表示该事件此刻应该发出通知。"""
        last = self._last_fired.get((serial, dedup_key))
        if last is None and self.store is not None:
            stored = self.store.last_event_at(serial, kind)
            if stored is not None:
                last = stored
        if last is not None and now - last < self.dedup_window:
            return False
        self._last_fired[(serial, dedup_key)] = now
        if self.store is not None:
            try:
                self.store.record_event(serial, kind, detail, ts=now)
            except Exception:  # 存储异常不能影响通知本身
                pass
        return True

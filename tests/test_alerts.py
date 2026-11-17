"""alerts.py 单元测试（提示词 5.3 通知时机与 30 分钟去重）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from knowdiskdrive import alerts as alerts_mod
from knowdiskdrive.alerts import (
    KIND_ATTR_CRITICAL,
    KIND_HEALTH_DOWN,
    KIND_TEMP_RED,
    AlertEngine,
)
from knowdiskdrive.health import (
    HEALTH_DANGER,
    HEALTH_GOOD,
    HEALTH_UNKNOWN,
    HEALTH_WARNING,
    TEMP_GREEN,
    TEMP_RED,
    TEMP_YELLOW,
    HealthReport,
)
from knowdiskdrive.history import HistoryStore
from knowdiskdrive.smart import AtaAttribute


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


def report(
    state: str = HEALTH_GOOD,
    temp_level: str = TEMP_GREEN,
    critical: list[AtaAttribute] | None = None,
    reasons: list[str] | None = None,
) -> HealthReport:
    return HealthReport(
        state=state,
        reasons=reasons or [],
        critical_attrs=critical or [],
        temp_level=temp_level,
    )


def attr(attr_id: int) -> AtaAttribute:
    return AtaAttribute(
        id=attr_id,
        name=f"属性{attr_id}",
        value=10,
        worst=10,
        thresh=20,
        raw=5,
        raw_text="5",
        when_failed="failing_now",
    )


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def info(ata_healthy_payload):
    from knowdiskdrive.smart import SmartctlRun, parse_json_payload

    return parse_json_payload(ata_healthy_payload, "/dev/sda", SmartctlRun(rc=0))


def test_first_evaluation_does_not_alert_downgrade(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    assert engine.evaluate("SN123", info, report(HEALTH_WARNING)) == []


def test_health_downgrade_alerts(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    found = engine.evaluate("SN123", info, report(HEALTH_DANGER, reasons=["媒体错误 5 条"]))
    assert len(found) == 1
    assert found[0].kind == KIND_HEALTH_DOWN
    assert "良好" in found[0].body and "危险" in found[0].body
    assert "媒体错误 5 条" in found[0].body


def test_health_upgrade_is_silent(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_DANGER))
    assert engine.evaluate("SN123", info, report(HEALTH_GOOD)) == []


def test_unknown_state_never_triggers_downgrade(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    assert engine.evaluate("SN123", info, report(HEALTH_UNKNOWN)) == []
    assert engine.evaluate("SN123", info, report(HEALTH_GOOD)) == []


def test_same_downgrade_deduplicated_within_window(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    assert len(engine.evaluate("SN123", info, report(HEALTH_WARNING))) == 1
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    clock.advance(10)
    assert engine.evaluate("SN123", info, report(HEALTH_WARNING)) == []
    clock.advance(30)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    assert len(engine.evaluate("SN123", info, report(HEALTH_WARNING))) == 1


def test_temperature_red_alert(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    found = engine.evaluate("SN123", info, report(temp_level=TEMP_RED))
    assert [a.kind for a in found] == [KIND_TEMP_RED]
    assert "温度" in found[0].body


def test_temperature_yellow_is_silent(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    assert engine.evaluate("SN123", info, report(temp_level=TEMP_YELLOW)) == []


def test_temperature_red_deduplicated(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    assert len(engine.evaluate("SN123", info, report(temp_level=TEMP_RED))) == 1
    assert engine.evaluate("SN123", info, report(temp_level=TEMP_RED)) == []


def test_new_critical_attribute_alerts(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report())
    found = engine.evaluate("SN123", info, report(critical=[attr(5)]))
    assert [a.kind for a in found] == [KIND_ATTR_CRITICAL]
    assert "5" in found[0].body


def test_first_run_with_critical_attr_is_silent(clock, info):
    """首次读取就存在临界属性时不通知（避免启动即刷屏），基线照常建立。"""
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    assert engine.evaluate("SN123", info, report(critical=[attr(5)])) == []
    assert engine.evaluate("SN123", info, report(critical=[attr(5)])) == []


def test_critical_attribute_dedup_and_new_id(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report())
    engine.evaluate("SN123", info, report(critical=[attr(5)]))
    clock.advance(5)
    found = engine.evaluate("SN123", info, report(critical=[attr(5), attr(197)]))
    assert [a.kind for a in found] == [KIND_ATTR_CRITICAL]
    assert "197" in found[0].body
    engine.evaluate("SN123", info, report())
    clock.advance(40)
    found = engine.evaluate("SN123", info, report(critical=[attr(197)]))
    assert len(found) == 1
    assert "197" in found[0].body


def test_resolved_attribute_is_removed_from_baseline(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(critical=[attr(5)]))
    engine.evaluate("SN123", info, report())
    found = engine.evaluate("SN123", info, report(critical=[attr(5)]))
    assert [a.kind for a in found] == [KIND_ATTR_CRITICAL]


def test_engine_isolated_per_serial(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("A", info, report(HEALTH_GOOD))
    assert engine.evaluate("B", info, report(HEALTH_DANGER)) == []


def test_forget_resets_baseline(clock, info):
    engine = AlertEngine(dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    engine.forget("SN123")
    assert engine.evaluate("SN123", info, report(HEALTH_DANGER)) == []


def test_dedup_persists_through_store(tmp_path, clock, info):
    store = HistoryStore(tmp_path / "h.db")
    try:
        first = AlertEngine(store=store, dedup_minutes=30, now_fn=clock)
        found = first.evaluate("SN123", info, report(HEALTH_GOOD))
        found = first.evaluate("SN123", info, report(HEALTH_WARNING))
        assert len(found) == 1
        assert store.count("events") == 1

        second = AlertEngine(store=store, dedup_minutes=30, now_fn=clock)
        second.evaluate("SN123", info, report(HEALTH_GOOD))
        clock.advance(5)
        assert second.evaluate("SN123", info, report(HEALTH_WARNING)) == []
        clock.advance(60)
        second.evaluate("SN123", info, report(HEALTH_GOOD))
        assert len(second.evaluate("SN123", info, report(HEALTH_WARNING))) == 1
    finally:
        store.close()


def test_store_failure_does_not_break_alert(clock, info):
    class BrokenStore:
        def last_event_at(self, serial, kind):
            return None

        def record_event(self, *args, **kwargs):
            raise RuntimeError("disk full")

    engine = AlertEngine(store=BrokenStore(), dedup_minutes=30, now_fn=clock)
    engine.evaluate("SN123", info, report(HEALTH_GOOD))
    assert len(engine.evaluate("SN123", info, report(HEALTH_DANGER))) == 1


def test_alert_kind_constants():
    assert KIND_HEALTH_DOWN == "health_downgrade"
    assert KIND_TEMP_RED == "temperature_red"
    assert KIND_ATTR_CRITICAL == "attribute_critical"
    assert alerts_mod.HEALTH_RANK[HEALTH_GOOD] < alerts_mod.HEALTH_RANK[HEALTH_WARNING]

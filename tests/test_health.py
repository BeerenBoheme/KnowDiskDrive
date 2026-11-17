"""健康判定规则（提示词第 4 节）——单测必须覆盖每个分支与边界。"""

from __future__ import annotations

import pytest

from knowdiskdrive import health as health_mod
from knowdiskdrive.config import DEFAULT_CONFIG, Config, TempThreshold
from knowdiskdrive.smart import AtaAttribute, DiskInfo

from conftest import make_info


def _attr(attr_id, value=None, worst=None, thresh=None, raw=None, when_failed=""):
    return AtaAttribute(
        id=attr_id,
        name=f"属性{attr_id}",
        value=value,
        worst=worst,
        thresh=thresh,
        raw=raw,
        raw_text=None,
        when_failed=when_failed,
    )


# --------------------------------------------------------------------------
# 4.1 单条属性
# --------------------------------------------------------------------------
def test_when_failed_now_is_critical():
    assert health_mod.attribute_status(_attr(5, 100, 100, 10, 0, "failing_now")) == health_mod.ATTR_CRITICAL


def test_when_failed_past_is_critical():
    assert health_mod.attribute_status(_attr(5, 100, 100, 10, 0, "failing_past")) == health_mod.ATTR_CRITICAL


def test_value_at_or_below_threshold_is_critical():
    assert health_mod.attribute_status(_attr(5, 10, 10, 10, 0)) == health_mod.ATTR_CRITICAL
    assert health_mod.attribute_status(_attr(5, 9, 9, 10, 0)) == health_mod.ATTR_CRITICAL


def test_value_within_five_of_threshold_is_caution():
    """4.1：value <= thresh + 5 判为注意（边界含 5）。"""
    assert health_mod.attribute_status(_attr(5, 15, 15, 10, 0)) == health_mod.ATTR_CAUTION
    assert health_mod.attribute_status(_attr(5, 14, 14, 10, 0)) == health_mod.ATTR_CAUTION


def test_value_above_threshold_plus_five_is_normal():
    assert health_mod.attribute_status(_attr(5, 16, 16, 10, 0)) == health_mod.ATTR_NORMAL


def test_threshold_zero_does_not_trigger_margin_rule():
    """阈值 0 表示未设阈值，不应把 value=3 判成注意。"""
    assert health_mod.attribute_status(_attr(1, 3, 3, 0, 0)) == health_mod.ATTR_NORMAL


@pytest.mark.parametrize("attr_id", [5, 10, 197, 198])
def test_raw_gt_zero_ids_are_caution(attr_id):
    assert health_mod.attribute_status(_attr(attr_id, 100, 100, 0, 3)) == health_mod.ATTR_CAUTION


@pytest.mark.parametrize("attr_id", [199, 200])
def test_sata_only_ids(attr_id):
    assert health_mod.attribute_status(_attr(attr_id, 100, 100, 0, 3), is_sata=True) == health_mod.ATTR_CAUTION
    assert health_mod.attribute_status(_attr(attr_id, 100, 100, 0, 3), is_sata=False) == health_mod.ATTR_NORMAL


def test_raw_zero_is_normal():
    assert health_mod.attribute_status(_attr(5, 100, 100, 0, 0)) == health_mod.ATTR_NORMAL


def test_missing_values_do_not_crash():
    assert health_mod.attribute_status(AtaAttribute(id=5, name="x")) == health_mod.ATTR_NORMAL


# --------------------------------------------------------------------------
# 4.2 整盘健康
# --------------------------------------------------------------------------
def test_healthy_ata_is_good(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_GOOD
    assert report.reasons == []


def test_healthy_nvme_is_good(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_GOOD


def test_smart_passed_false_is_danger(ata_failing_payload):
    info = make_info(ata_failing_payload, "/dev/sda")
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_DANGER
    assert any("smart_status.passed" in r for r in report.reasons)


def test_failing_now_is_danger(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    info.ata_attributes[0].when_failed = "failing_now"
    info.smart_passed = True
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_DANGER


def test_critical_attribute_without_failing_now_is_warning(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    info.smart_passed = True
    info.ata_attributes[0].value = 5
    info.ata_attributes[0].thresh = 10
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_WARNING


def test_three_caution_attributes_is_warning(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    info.smart_passed = True
    # 造 3 条 raw>0 的注意属性（ID 5/10/197）
    for index, attr_id in enumerate((5, 10, 197)):
        info.ata_attributes[index].id = attr_id
        info.ata_attributes[index].raw = 7
        info.ata_attributes[index].thresh = 0
    report = health_mod.evaluate(info)
    assert len(report.caution_attrs) == 3
    assert report.state == health_mod.HEALTH_WARNING


def test_two_caution_attributes_stay_good(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    info.smart_passed = True
    for index, attr_id in enumerate((5, 10)):
        info.ata_attributes[index].id = attr_id
        info.ata_attributes[index].raw = 7
        info.ata_attributes[index].thresh = 0
    assert health_mod.evaluate(info).state == health_mod.HEALTH_GOOD


def test_nvme_critical_warning_is_danger(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    info.nvme_log["critical_warning"] = 2
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_DANGER
    assert any("critical_warning" in r for r in report.reasons)


def test_nvme_media_errors_is_warning(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    info.nvme_log["media_errors"] = 3
    assert health_mod.evaluate(info).state == health_mod.HEALTH_WARNING


def test_nvme_percentage_used_threshold(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    info.nvme_log["percentage_used"] = 89
    assert health_mod.evaluate(info).state == health_mod.HEALTH_GOOD
    info.nvme_log["percentage_used"] = 90
    assert health_mod.evaluate(info).state == health_mod.HEALTH_WARNING


def test_nvme_available_spare_threshold(nvme_healthy_payload):
    info = make_info(nvme_healthy_payload, "/dev/nvme0n1")
    info.nvme_log["available_spare"] = 10
    assert health_mod.evaluate(info).state == health_mod.HEALTH_GOOD
    info.nvme_log["available_spare"] = 9
    assert health_mod.evaluate(info).state == health_mod.HEALTH_WARNING


def test_nvme_critical_fixture(nvme_critical_payload):
    info = make_info(nvme_critical_payload, "/dev/nvme0n1")
    report = health_mod.evaluate(info)
    assert report.state == health_mod.HEALTH_DANGER
    reasons = " ".join(report.reasons)
    assert "critical_warning" in reasons
    assert "介质错误" in reasons or "寿命消耗" in reasons


def test_read_failure_yields_unknown(ata_healthy_payload):
    info = DiskInfo(device="/dev/sda", status="permission")
    assert health_mod.evaluate(info).state == health_mod.HEALTH_UNKNOWN


def test_standby_yields_unknown():
    info = DiskInfo(device="/dev/sda", status="standby")
    assert health_mod.evaluate(info).state == health_mod.HEALTH_UNKNOWN


def test_worst_health_ordering():
    assert health_mod.worst_health("good", "warning") == "warning"
    assert health_mod.worst_health("warning", "danger") == "danger"
    assert health_mod.worst_health("good", "unknown") == "good"
    assert health_mod.worst_health() == "good"


# --------------------------------------------------------------------------
# 4.3 温度阈值（边界值必须归黄色）
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "media,value,expected",
    [
        ("hdd", 39, health_mod.TEMP_GREEN),
        ("hdd", 40, health_mod.TEMP_YELLOW),
        ("hdd", 50, health_mod.TEMP_YELLOW),
        ("hdd", 51, health_mod.TEMP_RED),
        ("sata_ssd", 49, health_mod.TEMP_GREEN),
        ("sata_ssd", 50, health_mod.TEMP_YELLOW),
        ("sata_ssd", 60, health_mod.TEMP_YELLOW),
        ("sata_ssd", 61, health_mod.TEMP_RED),
        ("nvme", 64, health_mod.TEMP_GREEN),
        ("nvme", 65, health_mod.TEMP_YELLOW),
        ("nvme", 80, health_mod.TEMP_YELLOW),
        ("nvme", 81, health_mod.TEMP_RED),
    ],
)
def test_temperature_boundaries(media, value, expected):
    assert health_mod.temperature_level(value, media) == expected


def test_temperature_none_is_unknown_state():
    level, state = health_mod.classify_temperature(None, TempThreshold(40, 50))
    assert level == health_mod.TEMP_GREEN
    assert state == "unknown"
    assert health_mod.temperature_color(None, "hdd") is None


def test_temperature_thresholds_configurable():
    cfg = Config(temp_thresholds={"hdd": TempThreshold(30, 35), "sata_ssd": TempThreshold(50, 60), "nvme": TempThreshold(65, 80)})
    assert health_mod.temperature_level(36, "hdd", cfg) == health_mod.TEMP_RED
    assert health_mod.temperature_level(29, "hdd", cfg) == health_mod.TEMP_GREEN


def test_media_detection_from_info(ata_healthy_payload, nvme_healthy_payload, ata_failing_payload):
    assert make_info(ata_healthy_payload, "/dev/sda").media == "sata_ssd"
    assert make_info(nvme_healthy_payload, "/dev/nvme0n1").media == "nvme"
    assert make_info(ata_failing_payload, "/dev/sda").media == "hdd"


def test_sort_attributes_pins(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    ordered = health_mod.sort_attributes(info.ata_attributes, (199, 5, 194), pin_first=True)
    assert [a.id for a in ordered[:2]] == [5, 199]


def test_sort_attributes_no_pin(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    ordered = health_mod.sort_attributes(info.ata_attributes, (199, 5), pin_first=False)
    assert [a.id for a in ordered] == sorted(a.id for a in ordered)


def test_copy_text_contains_key_fields(ata_healthy_payload):
    info = make_info(ata_healthy_payload, "/dev/sda")
    summary = health_mod.summarize(info)
    text = health_mod.copy_text(summary)
    assert "ZHITAI" in text
    assert info.serial in text
    assert "健康：良好" in text
    assert "异常属性：无" in text

"""属性中文名映射的测试（提示词 5.2 的「属性名」列）。"""

from __future__ import annotations

from knowdiskdrive.attrnames import (
    ATA_ATTRIBUTE_NAMES,
    _is_unknown_name,
    attribute_display_name,
    attribute_display_with_english,
)


def test_known_attribute_uses_chinese_name():
    assert attribute_display_name(5, "Reallocated_Sector_Ct") == "重映射扇区数"
    assert attribute_display_name(9, "Power_On_Hours") == "通电时间"


def test_unknown_attribute_falls_back_to_english_name():
    assert attribute_display_name(999, "Weird") == "Weird"


def test_vendor_specific_ids_are_not_guessed():
    """smartctl 报 Unknown_* 的 ID 不许编中文名，否则会误导用户。

    实测某国产 SATA SSD 的 206~211、234、243、245 落在 Unknown_* 上，
    原始值是几百亿量级的计数器，按 ATA 老定义的「飞行高度 / VCM 电流」解释
    完全说不通。这类 ID 必须显示成「厂商自定义属性 N」。
    """
    for attr_id in (206, 207, 208, 209, 210, 211, 234, 243, 245):
        assert attr_id not in ATA_ATTRIBUTE_NAMES, f"{attr_id} 不应该有中文名"
        assert attribute_display_name(attr_id, "Unknown_SSD_Attribute") == (
            f"厂商自定义属性 {attr_id}"
        )


def test_unknown_ssd_attribute_name_is_treated_as_unknown():
    assert _is_unknown_name("Unknown_Attribute")
    assert _is_unknown_name("Unknown_SSD_Attribute")
    assert not _is_unknown_name("Power_On_Hours")
    assert not _is_unknown_name("")


def test_231_ssd_life_left_is_kept():
    """231 虽然也是 Unknown_SSD_Attribute，但归一化值与原始值互补可验证。"""
    assert attribute_display_name(231, "Unknown_SSD_Attribute") == "SSD 剩余寿命"


def test_display_with_english():
    assert attribute_display_with_english(5, "Reallocated_Sector_Ct") == (
        "重映射扇区数（Reallocated_Sector_Ct）"
    )
    assert attribute_display_with_english(206, "Unknown_SSD_Attribute") == (
        "厂商自定义属性 206"
    )
    assert attribute_display_with_english(999, "Some_Name") == "Some_Name"

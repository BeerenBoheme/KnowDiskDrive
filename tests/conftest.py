"""pytest 共享夹具。

要求（用户附加约束）：测试要快、内存占用小 —— 所有夹具都是小体积 JSON，
不做真实磁盘 IO，不启动 GUI 主循环。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# GUI 测试必须在无显示环境下跑（A9）。必须在导入 PySide6 之前设置。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from knowdiskdrive import devices as devices_mod
from knowdiskdrive import smart as smart_mod

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """读取 tests/fixtures 下的 JSON 样本。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def ata_healthy_payload() -> dict:
    return load_fixture("ata_zhitai_healthy.json")


@pytest.fixture(scope="session")
def nvme_healthy_payload() -> dict:
    return load_fixture("nvme_kioxia_healthy.json")


@pytest.fixture(scope="session")
def ata_failing_payload() -> dict:
    return load_fixture("ata_failing.json")


@pytest.fixture(scope="session")
def nvme_critical_payload() -> dict:
    return load_fixture("nvme_critical.json")


def make_info(payload: dict, device: str, rc: int = 0) -> smart_mod.DiskInfo:
    """把 JSON 样本解析成 DiskInfo（测试里最常用的入口）。"""
    run = smart_mod.SmartctlRun(rc=rc)
    return smart_mod.parse_json_payload(payload, device, run)


@pytest.fixture
def fake_devices() -> list[devices_mod.Device]:
    """两台与真机一致的设备（不访问 /sys，纯内存对象）。"""
    return [
        devices_mod.Device(
            name="sda",
            model="ZHITAI SC001 Active 512GB SSD",
            serial="ZTB1EXAMPLE0000001",
            tran="sata",
            size="476.9G",
            size_bytes=512110190592,
            rota=False,
        ),
        devices_mod.Device(
            name="nvme0n1",
            model="KBG40ZNV512G KIOXIA",
            serial="41OPHEXAMPLE1",
            tran="nvme",
            size="476.9G",
            size_bytes=512110190592,
            rota=False,
        ),
    ]

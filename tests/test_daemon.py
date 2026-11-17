"""daemon.py 单元测试（提示词第 7 节：后台采样闭环）。

全部用注入的假枚举器 / 假读取器，不碰真实磁盘，也不 sleep。
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from knowdiskdrive import daemon as daemon_mod
from knowdiskdrive import smart
from knowdiskdrive.config import DEFAULT_CONFIG, Config
from knowdiskdrive.devices import Device, DeviceEnumerationError
from knowdiskdrive.history import HistoryStore


def make_device(name: str = "sda", serial: str = "SN1", model: str = "ZHITAI SC001") -> Device:
    return Device(
        name=name,
        model=model,
        serial=serial,
        tran="sata",
        size="476.9G",
        size_bytes=512110190592,
        rota=False,
    )


def make_info(device: str = "/dev/sda", temp: int | None = 33, status: str = smart.STATUS_OK) -> smart.DiskInfo:
    info = smart.DiskInfo(device=device, serial="SN1", model="ZHITAI SC001", status=status)
    info.temperature_c = temp
    return info


class FakeEnv:
    """可编程的枚举器 / 读取器。"""

    def __init__(self, devices: list[Device] | None = None) -> None:
        self.devices = devices if devices is not None else [make_device()]
        self.infos: list[smart.DiskInfo] = []
        self.reads = 0
        self.error: Exception | None = None

    def enumerate(self) -> list[Device]:
        if self.error is not None:
            raise self.error
        return list(self.devices)

    def read(self, path: str, cfg: Config, **kwargs: object) -> smart.DiskInfo:
        self.reads += 1
        if self.infos:
            index = min(self.reads - 1, len(self.infos) - 1)
            return self.infos[index]
        return make_info(device=path)


@pytest.fixture
def env() -> FakeEnv:
    return FakeEnv()


@pytest.fixture
def store() -> HistoryStore:
    s = HistoryStore(":memory:")
    yield s
    s.close()


def build(env: FakeEnv, store: HistoryStore, stream: io.StringIO | None = None, **kwargs: object) -> daemon_mod.Monitor:
    return daemon_mod.Monitor(
        cfg=DEFAULT_CONFIG,
        store=store,
        stream=stream if stream is not None else io.StringIO(),
        enumerator=env.enumerate,
        reader=env.read,
        **kwargs,
    )


# ---------------------------------------------------------------- 单轮采样
def test_collect_records_sample(env, store):
    monitor = build(env, store)
    outcomes = monitor.collect()
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.device == "sda"
    assert outcome.health == "good"
    assert outcome.temp_c == 33
    assert outcome.readable is True
    assert store.count("temp_samples") == 1


def test_collect_skips_unreadable_device(env, store):
    env.infos = [make_info(status=smart.STATUS_PERMISSION)]
    monitor = build(env, store)
    outcomes = monitor.collect()
    assert outcomes[0].readable is False
    assert "一次性授权" in outcomes[0].status_text
    assert store.count("temp_samples") == 0


def test_collect_handles_reader_exception(env, store):
    def boom(*args: object, **kwargs: object) -> smart.DiskInfo:
        raise RuntimeError("设备消失")

    monitor = build(env, store)
    monitor._read = boom  # type: ignore[assignment]
    outcomes = monitor.collect()
    assert outcomes[0].status == smart.STATUS_ERROR
    assert "设备消失" in outcomes[0].status_text
    assert store.count("temp_samples") == 0


def test_collect_handles_enumeration_error(env, store):
    env.error = DeviceEnumerationError("lsblk 不可用")
    stream = io.StringIO()
    monitor = build(env, store, stream=stream)
    assert monitor.collect() == []
    assert "lsblk 不可用" in stream.getvalue()


def test_collect_releases_each_device(env, store):
    """多盘一轮：每块盘都写一条样本。"""
    env.devices = [make_device("sda", "SN1"), make_device("sdb", "SN2")]
    monitor = build(env, store)
    outcomes = monitor.collect()
    assert [o.device for o in outcomes] == ["sda", "sdb"]
    assert store.count("temp_samples") == 2


def test_temp_none_is_rendered_as_dash(env, store):
    env.infos = [make_info(temp=None)]
    monitor = build(env, store)
    outcome = monitor.collect()[0]
    assert outcome.temp_c is None
    assert "—" in outcome.line()


# ---------------------------------------------------------------- 告警
def test_alert_emitted_on_temp_red(env, store):
    env.infos = [make_info(temp=70)]
    stream = io.StringIO()
    monitor = build(env, store, stream=stream)
    outcomes = monitor.collect()
    assert outcomes[0].alerts, "红区温度应产生告警"
    assert store.count("events") == 1


def test_alert_dedup_within_window(env, store):
    env.infos = [make_info(temp=70)]
    monitor = build(env, store)
    monitor.collect()
    monitor.collect()
    assert store.count("events") == 1


# ---------------------------------------------------------------- purge
def test_purge_called_once_per_day(env, store):
    clock = {"now": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)}

    def now() -> datetime:
        return clock["now"]

    calls: list[int] = []
    real_purge = store.purge

    def spy(days: int, **kwargs: object) -> int:
        calls.append(days)
        return 0

    store.purge = spy  # type: ignore[assignment]
    monitor = build(env, store, clock=now)
    monitor.collect()
    clock["now"] += timedelta(hours=1)
    monitor.collect()
    assert len(calls) == 1
    clock["now"] += timedelta(hours=25)
    monitor.collect()
    assert len(calls) == 2
    store.purge = real_purge  # type: ignore[assignment]


def test_purge_failure_does_not_break_sampling(env, store):
    def boom(days: int, **kwargs: object) -> int:
        raise RuntimeError("数据库损坏")

    store.purge = boom  # type: ignore[assignment]
    monitor = build(env, store)
    assert len(monitor.collect()) == 1


# ---------------------------------------------------------------- 常驻循环
class FakeStop:
    def __init__(self, stop_after: int = 10**9) -> None:
        self.waits = 0
        self.stop_after = stop_after

    def is_set(self) -> bool:
        return self.waits >= self.stop_after

    def wait(self, timeout: float) -> bool:
        self.waits += 1
        return self.is_set()


def test_run_respects_iterations(env, store):
    stream = io.StringIO()
    monitor = build(env, store, stream=stream)
    stop = FakeStop()
    rc = monitor.run(interval=10, iterations=2, stop_event=stop)
    assert rc == 0
    assert monitor.rounds == 2
    assert store.count("temp_samples") == 2
    assert "开始监控" in stream.getvalue()


def test_run_stops_when_event_set(env, store):
    monitor = build(env, store)
    stop = FakeStop(stop_after=0)
    rc = monitor.run(interval=10, stop_event=stop)
    assert rc == 0
    assert monitor.rounds == 0


def test_run_prints_alert_line(env, store):
    env.infos = [make_info(temp=70)]
    stream = io.StringIO()
    monitor = build(env, store, stream=stream)
    monitor.run(interval=10, iterations=1, stop_event=FakeStop())
    assert "告警" in stream.getvalue()


def test_run_reports_no_devices(env, store):
    env.devices = []
    stream = io.StringIO()
    monitor = build(env, store, stream=stream)
    monitor.run(interval=10, iterations=1, stop_event=FakeStop())
    assert "没有检测到磁盘" in stream.getvalue()


def test_interval_is_clamped(env, store):
    monitor = build(env, store)
    stop = FakeStop(stop_after=1)
    monitor.run(interval=1, stop_event=stop)
    assert stop.waits == 1  # 至少跑过一轮即停


def test_collect_persists_probe_d_arg(env, store):
    """提示词 3.3：探测出的 -d 值要缓存，否则每轮都要重试四个候选。"""
    env.infos = [make_info()]
    env.infos[0].usb_d_arg = "usbjmicron"
    monitor = build(env, store)
    monitor.collect()
    assert store.get_pref("SN1") == "usbjmicron"


def test_collect_passes_cached_d_arg_to_reader(env, store):
    store.set_pref("SN1", "sat")
    seen: dict[str, object] = {}

    def reader(path: str, cfg: Config, **kwargs: object) -> smart.DiskInfo:
        seen.update(kwargs)
        return make_info(device=path)

    monitor = daemon_mod.Monitor(
        cfg=DEFAULT_CONFIG,
        store=store,
        stream=io.StringIO(),
        enumerator=env.enumerate,
        reader=reader,
    )
    monitor.collect()
    assert seen.get("cached_d_arg") == "sat"


def test_collect_without_serial_does_not_write_pref(env, store):
    env.devices = [make_device(serial="")]
    env.infos = [make_info()]
    env.infos[0].usb_d_arg = "sat"
    monitor = build(env, store)
    monitor.collect()
    assert store.get_pref("") is None


# ---------------------------------------------------------------- run_once
def test_run_once_returns_zero(env, store, monkeypatch):
    monkeypatch.setattr(daemon_mod.devices_mod, "enumerate_devices", env.enumerate)
    monkeypatch.setattr(smart, "read_smart_auto", env.read)
    rc = daemon_mod.run_once(cfg=DEFAULT_CONFIG, store=store, stream=io.StringIO())
    assert rc == 0
    assert store.count("temp_samples") == 1


def test_run_once_returns_two_when_all_unreadable(env, store, monkeypatch):
    env.infos = [make_info(status=smart.STATUS_PERMISSION)]
    monkeypatch.setattr(daemon_mod.devices_mod, "enumerate_devices", env.enumerate)
    monkeypatch.setattr(smart, "read_smart_auto", env.read)
    rc = daemon_mod.run_once(cfg=DEFAULT_CONFIG, store=store, stream=io.StringIO())
    assert rc == 2

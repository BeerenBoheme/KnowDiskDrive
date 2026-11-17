"""后台采样守护（提示词第 7 节的监控闭环，无 GUI 版本）。

```
knowdiskdrive daemon                # 常驻，默认每 300 秒采样一次
knowdiskdrive daemon --once         # 只采一轮就退出（自测 / cron 用）
knowdiskdrive daemon --interval 60  # 自定义间隔
```

职责很窄：按间隔枚举磁盘 → 读 SMART → 写入历史库 → 交给 AlertEngine 判定
→ 把告警打到标准输出。**不安装 systemd 服务**（提示词明确要求），需要常驻
时要么用终端跑着，要么自己套一层 systemd --user。

内存约定：每轮结束即丢弃 DiskInfo，只保留一行概要字符串，长时间运行也不会
累积（提示词「不要触发 oom killer」）。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, TextIO

from . import devices as devices_mod
from . import health as health_mod
from . import smart as smart_mod
from .alerts import Alert, AlertEngine
from .config import DEFAULT_CONFIG, Config
from .history import HistoryStore, open_store

#: 默认采样间隔（秒），与配置里的 sample_interval_seconds 保持一致
DEFAULT_INTERVAL_SECONDS = 300

#: 清理过期历史的最小间隔（秒）
PURGE_INTERVAL_SECONDS = 24 * 3600


@dataclass
class SampleOutcome:
    """一轮采样的单盘结果，只留概要，不留原始数据。"""

    device: str
    serial: str
    model: str
    health: str
    health_text: str
    temp_c: int | None
    status: str
    status_text: str
    alerts: list[Alert] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        return self.status == smart_mod.STATUS_OK

    def line(self) -> str:
        if not self.readable:
            return f"{self.device:<10} {self.model}  —  —  {self.status_text}"
        temp = f"{self.temp_c}°C" if self.temp_c is not None else "—"
        return f"{self.device:<10} {self.model}  {self.health_text}  {temp}"


def _local_stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime("%H:%M:%S")


class Monitor:
    """采样循环本体，可注入 store / engine / 输出，方便测试。"""

    def __init__(
        self,
        cfg: Config = DEFAULT_CONFIG,
        store: HistoryStore | None = None,
        engine: AlertEngine | None = None,
        stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
        enumerator: Callable[[], list] | None = None,
        reader: Callable[..., object] | None = None,
    ) -> None:
        self.cfg = cfg
        self._owned_store = store is None
        self.store = store if store is not None else open_store()
        self.engine = engine if engine is not None else AlertEngine(
            store=self.store, dedup_minutes=cfg.alert_dedup_minutes
        )
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock or datetime.now
        self._enumerate = enumerator or devices_mod.enumerate_devices
        self._read = reader or smart_mod.read_smart_auto
        self._last_purge: datetime | None = None
        self.rounds = 0

    # -- 生命周期 ---------------------------------------------------------
    def close(self) -> None:
        if self._owned_store:
            try:
                self.store.close()
            except Exception:
                pass
            self._owned_store = False
        else:
            try:
                self.store.conn.commit()
            except Exception:
                pass

    def __enter__(self) -> "Monitor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 单轮采样 ---------------------------------------------------------
    def collect(self) -> list[SampleOutcome]:
        """枚举并读取全部磁盘，写入历史与告警，返回概要列表。"""
        try:
            found = list(self._enumerate())
        except devices_mod.DeviceEnumerationError as exc:
            self.emit(f"枚举磁盘失败：{exc}")
            return []

        outcomes: list[SampleOutcome] = []
        for device in found:
            info = None
            try:
                info = self._read(
                    device.path,
                    self.cfg,
                    cached_d_arg=self._cached_d_arg(device),
                    allowed_devices=found,
                    is_usb=device.tran == "usb",
                )
                report = health_mod.evaluate(info, self.cfg)
                serial = device.serial or device.name
                outcome = SampleOutcome(
                    device=device.name,
                    serial=serial,
                    model=device.display_model or device.name,
                    health=report.state,
                    health_text=report.state_text,
                    temp_c=info.temperature_c,
                    status=info.status,
                    status_text=info.status_text,
                )
                if info.usb_d_arg and device.serial:
                    self._remember_d_arg(device.serial, info.usb_d_arg)
                if info.status == smart_mod.STATUS_OK and serial:
                    self._record(serial, info, report)
                    outcome.alerts = self.engine.evaluate(serial, info, report)
                else:
                    self.engine.forget(serial)
            except Exception as exc:  # 单盘异常不能拖垮整轮
                outcome = SampleOutcome(
                    device=device.name,
                    serial=device.serial or device.name,
                    model=device.display_model or device.name,
                    health=health_mod.HEALTH_UNKNOWN,
                    health_text=health_mod.HEALTH_TEXT[health_mod.HEALTH_UNKNOWN],
                    temp_c=None,
                    status=smart_mod.STATUS_ERROR,
                    status_text=f"采样异常：{exc}",
                )
            finally:
                info = None  # 及时释放，避免长跑累积
            outcomes.append(outcome)

        self.rounds += 1
        self._maybe_purge()
        return outcomes

    def _record(self, serial: str, info: object, report: object) -> None:
        try:
            self.store.record_sample(
                serial,
                getattr(info, "temperature_c", None),
                getattr(report, "state", None),
                getattr(info, "status", None),
            )
        except Exception:
            pass

    def _cached_d_arg(self, device: object) -> str | None:
        """提示词 3.3：按序列号取上次探测成功的 -d 值。"""
        serial = getattr(device, "serial", None)
        if not serial:
            return None
        try:
            return self.store.get_pref(serial)
        except Exception:
            return None

    def _remember_d_arg(self, serial: str, d_arg: str) -> None:
        try:
            self.store.set_pref(serial, d_arg)
        except Exception:
            pass

    def _maybe_purge(self) -> None:
        now = self.clock()
        if self._last_purge is not None and now - self._last_purge < timedelta(
            seconds=PURGE_INTERVAL_SECONDS
        ):
            return
        self._last_purge = now
        try:
            removed = self.store.purge(self.cfg.retention_days)
        except Exception:
            return
        if removed:
            self.emit(f"已清理 {removed} 条超过 {self.cfg.retention_days} 天的历史记录")

    # -- 常驻循环 ---------------------------------------------------------
    def run(
        self,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        iterations: int | None = None,
        stop_event: object | None = None,
    ) -> int:
        """常驻采样。iterations=None 表示一直跑；stop_event 支持外部停止。"""
        interval = max(10, int(interval))
        stop = stop_event if stop_event is not None else _NullEvent()
        self.emit(
            f"开始监控：间隔 {interval} 秒，历史保留 {self.cfg.retention_days} 天，按 Ctrl-C 退出"
        )
        rounds = 0
        try:
            while not stop.is_set():
                outcomes = self.collect()
                stamp = _local_stamp(self.clock())
                if not outcomes:
                    self.emit(f"[{stamp}] 没有检测到磁盘")
                for outcome in outcomes:
                    self.emit(f"[{stamp}] {outcome.line()}")
                    for alert in outcome.alerts:
                        self.emit(f"[{stamp}] 告警：{alert.title} —— {alert.body}")
                rounds += 1
                if iterations is not None and rounds >= iterations:
                    break
                if stop.wait(interval):
                    break
        except KeyboardInterrupt:
            self.emit("收到中断，已停止监控")
            return 130
        self.emit(f"监控结束，共采样 {rounds} 轮")
        return 0

    # -- 内部 -------------------------------------------------------------
    def emit(self, text: str) -> None:
        print(text, file=self.stream, flush=True)


class _NullEvent:
    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        time.sleep(timeout)
        return False


def run_once(
    cfg: Config = DEFAULT_CONFIG,
    store: HistoryStore | None = None,
    stream: TextIO | None = None,
) -> int:
    """采一轮就退出，返回码与 CLI 一致：0 正常，2 有盘读不到。"""
    with Monitor(cfg=cfg, store=store, stream=stream) as monitor:
        outcomes = monitor.collect()
        stamp = _local_stamp()
        for outcome in outcomes:
            monitor.emit(f"[{stamp}] {outcome.line()}")
            for alert in outcome.alerts:
                monitor.emit(f"[{stamp}] 告警：{alert.title} —— {alert.body}")
        if all(outcome.readable for outcome in outcomes):
            return 0
        return 2

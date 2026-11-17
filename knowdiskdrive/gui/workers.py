"""后台工作线程：所有 smartctl / lsblk 调用都在这里发生。

提示词第 8 节硬性要求：**主线程绝不允许同步调用 smartctl**。
因此本模块只暴露：

* `WorkRequest` / `WorkResult` —— 跨线程传递的纯数据；
* `execute_request()` —— 可在任意线程执行的纯函数（便于单测，不依赖 Qt）；
* `SmartWorker` —— 常驻 QThread，串行消费请求队列，用信号把结果送回 GUI 主线程。

串行而非每盘一个线程，是为了避免 USB 桥接探测时同时向同一块盘发多条命令
（某些桥接器会因此假死）。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..config import Config
from ..devices import Device, DeviceEnumerationError, enumerate_devices
from ..health import HealthReport, evaluate
from ..smart import DiskInfo, read_smart_auto, start_self_test

KIND_SCAN = "scan"
KIND_READ = "read"
KIND_TEST = "test"


@dataclass(frozen=True)
class WorkRequest:
    """一次后台任务。device 为 None 表示扫描类任务。"""

    kind: str
    cfg: Config
    device: Device | None = None
    request_id: int = 0
    cached_d_arg: str | None = None
    test_mode: str = "short"


@dataclass
class WorkResult:
    kind: str
    request_id: int = 0
    device: Device | None = None
    info: DiskInfo | None = None
    report: HealthReport | None = None
    devices: list[Device] = field(default_factory=list)
    error: str | None = None
    message: str = ""
    duration_ms: int = 0


def execute_request(request: WorkRequest) -> WorkResult:
    """执行一次任务。任何异常都转成 WorkResult.error，不向上抛。"""
    started = time.monotonic()
    try:
        if request.kind == KIND_SCAN:
            try:
                found = enumerate_devices()
            except DeviceEnumerationError as exc:
                return WorkResult(kind=request.kind, request_id=request.request_id, error=str(exc))
            return WorkResult(
                kind=request.kind,
                request_id=request.request_id,
                devices=found,
                duration_ms=_elapsed_ms(started),
            )

        device = request.device
        if device is None:
            return WorkResult(
                kind=request.kind,
                request_id=request.request_id,
                error="缺少目标设备",
            )

        if request.kind == KIND_TEST:
            run = start_self_test(
                device.path,
                request.test_mode,
                request.cfg,
                allowed_devices=[device],
            )
            ok = run.rc == 0 and not run.timed_out
            message = "已启动自检" if ok else _run_message(run)
            return WorkResult(
                kind=request.kind,
                request_id=request.request_id,
                device=device,
                error=None if ok else message,
                message=message,
                duration_ms=_elapsed_ms(started),
            )

        info = read_smart_auto(
            device.path,
            request.cfg,
            cached_d_arg=request.cached_d_arg,
            allowed_devices=[device],
            is_usb=device.tran == "usb",
        )
        report = evaluate(info, request.cfg)
        return WorkResult(
            kind=request.kind,
            request_id=request.request_id,
            device=device,
            info=info,
            report=report,
            message=info.status_text,
            duration_ms=_elapsed_ms(started),
        )
    except Exception as exc:  # 防御：后台线程异常不能让 GUI 崩溃
        return WorkResult(
            kind=request.kind,
            request_id=request.request_id,
            device=request.device,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _run_message(run: Any) -> str:
    if run.timed_out:
        return "读取超时，可能是 USB 设备无响应（可重试）"
    detail = (run.stderr or "").strip() or (run.stdout or "").strip()
    return detail.splitlines()[-1] if detail else f"smartctl 退出码 {run.rc}"


class SmartWorker(QThread):
    """常驻工作线程：串行处理读盘 / 扫描 / 自检请求。"""

    result_ready = Signal(object)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._queue: "queue.Queue[WorkRequest]" = queue.Queue(maxsize=16)
        self._stopping = threading.Event()
        self._inflight: WorkRequest | None = None

    # -- 主线程调用 -------------------------------------------------------
    def submit(self, request: WorkRequest) -> bool:
        """排队一个任务；队列满时返回 False（GUI 显示「正在读取，请稍候」）。"""
        if self._stopping.is_set():
            return False
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            return False
        return True

    def busy(self) -> bool:
        return self._inflight is not None

    def pending(self) -> int:
        return self._queue.qsize() + (1 if self._inflight is not None else 0)

    def stop(self) -> None:
        self._stopping.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self.wait(3000)

    # -- 线程体 -----------------------------------------------------------
    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                request = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._inflight = request
            try:
                result = execute_request(request)
            except Exception as exc:  # execute_request 已兜底，这里再兜一层
                result = WorkResult(
                    kind=request.kind,
                    request_id=request.request_id,
                    device=request.device,
                    error=f"{type(exc).__name__}: {exc}",
                )
            self._inflight = None
            if self._stopping.is_set():
                break
            try:
                self.result_ready.emit(result)
            except RuntimeError:
                break

# ---------------------------------------------------------------------------
# 提权线程：pkexec / sudo 会阻塞到用户在对话框里输完密码，绝不能放主线程
# ---------------------------------------------------------------------------
class ElevationWorker(QThread):
    """在后台跑一次提权 setcap，结果用信号送回主线程。

    与 SmartWorker 分开是因为它只跑一次、且不需要排队：用户点「一键授权」
    期间界面要照常响应，但不能同时开两个密码框。
    """

    done = Signal(object)  # ElevationResult

    def __init__(
        self,
        smartctl: str,
        no_nvme: bool = False,
        prefer: str = "gui",
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._smartctl = smartctl
        self._no_nvme = no_nvme
        self._prefer = prefer

    def run(self) -> None:  # pragma: no cover - 线程体，由 GUI 测试单独驱动
        from .. import privilege

        try:
            result = privilege.apply_capabilities(
                self._smartctl, no_nvme=self._no_nvme, prefer=self._prefer
            )
        except Exception as exc:  # 提权失败不能让线程带着异常退出
            result = privilege.ElevationResult(
                ok=False,
                backend="none",
                message=f"{type(exc).__name__}: {exc}",
            )
        self.done.emit(result)

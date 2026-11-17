"""全局配置（提示词第 11 节：不要硬编码设备路径、温度阈值、采样间隔）。

所有可调参数集中在 DEFAULT_CONFIG，用户可用 TOML 覆盖：

    $XDG_CONFIG_HOME/knowdiskdrive/config.toml

也可以用环境变量 `KDD_CONFIG` 指向别的配置文件。缺省/缺键时回退默认值，
配置写错不应导致程序崩溃（只提示并回退默认）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:  # Python 3.11+ 自带
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 只在 3.10 上走到
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        # 两个都没有：配置文件读不了，但程序仍要能按默认值启动。
        tomllib = None  # type: ignore[assignment]

APP_DIR_NAME = "knowdiskdrive"

#: 配置文件路径（$XDG_CONFIG_HOME/knowdiskdrive/config.toml）
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
) / APP_DIR_NAME / "config.toml"

#: 历史数据库默认路径（7 节）
DEFAULT_DB_PATH = Path(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
) / APP_DIR_NAME / "history.db"


def default_config_path() -> Path:
    """返回当前生效的配置文件路径（环境变量可改写）。"""
    override = os.environ.get("KDD_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def default_db_path() -> Path:
    """返回历史数据库路径，环境变量 `KDD_DB` 可改写。"""
    override = os.environ.get("KDD_DB")
    return Path(override) if override else DEFAULT_DB_PATH


@dataclass(frozen=True)
class TempThreshold:
    """温度分级阈值（提示词 4.3）。

    语义：`t < green_below` 绿色；`green_below <= t <= yellow_max` 黄色（边界归黄）；
    `t > yellow_max` 红色。
    """

    green_below: int
    yellow_max: int


#: 介质 -> 默认温度阈值（HDD / SATA SSD / NVMe）
DEFAULT_TEMP_THRESHOLDS: dict[str, TempThreshold] = {
    "hdd": TempThreshold(green_below=40, yellow_max=50),
    "sata_ssd": TempThreshold(green_below=50, yellow_max=60),
    "nvme": TempThreshold(green_below=65, yellow_max=80),
}


@dataclass(frozen=True)
class Config:
    """运行期配置。字段含义见各注释，全部可被 TOML 覆盖。"""

    # --- smartctl ---
    #: smartctl 可执行文件路径，空字符串表示用 PATH 查找
    smartctl_path: str = ""
    #: 单次调用超时（秒），提示词第 8 节要求 15 秒
    timeout_seconds: float = 15.0
    #: 不唤醒休眠盘的 `-n` 参数取值（提示词 3.4）
    standby_policy: str = "standby,3"
    #: USB 桥接探测用的 `-d` 候选值（提示词 3.3，顺序即探测顺序）
    usb_bridge_d_args: tuple[str, ...] = ("sat", "usbjmicron", "usbsunplus")

    # --- 采样与保留（第 7 节）---
    #: 后台采样间隔（秒），默认 5 分钟
    sample_interval_seconds: int = 300
    #: 温度样本保留天数
    retention_days: int = 90

    # --- 告警（5.3）---
    #: 同一事件去重窗口（分钟）
    alert_dedup_minutes: int = 30

    # --- 温度阈值（4.3）---
    temp_thresholds: dict[str, TempThreshold] = field(
        default_factory=lambda: dict(DEFAULT_TEMP_THRESHOLDS)
    )

    # --- 界面（5.2）---
    #: 需要置顶显示的 ATA 属性 ID
    pinned_attribute_ids: tuple[int, ...] = (5, 10, 194, 197, 198, 199)

    def temp_threshold(self, media: str) -> TempThreshold:
        """按介质取温度阈值，未知介质按最保守的 SATA SSD 处理。"""
        return self.temp_thresholds.get(media) or self.temp_thresholds["sata_ssd"]


DEFAULT_CONFIG = Config()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def config_from_dict(data: dict[str, Any], base: Config | None = None) -> Config:
    """把 TOML/字典覆盖项合并到默认配置上。无法解析的键直接忽略。"""
    cfg = base or DEFAULT_CONFIG
    changed: dict[str, Any] = {}

    smartctl = data.get("smartctl") or {}
    if isinstance(smartctl, dict):
        if isinstance(smartctl.get("path"), str):
            changed["smartctl_path"] = smartctl["path"].strip()
        if "timeout_seconds" in smartctl:
            changed["timeout_seconds"] = _as_float(
                smartctl["timeout_seconds"], cfg.timeout_seconds
            )
        if isinstance(smartctl.get("standby_policy"), str):
            policy = smartctl["standby_policy"].strip()
            if policy:
                changed["standby_policy"] = policy
        if isinstance(smartctl.get("usb_bridge_d_args"), list):
            args = tuple(str(a).strip() for a in smartctl["usb_bridge_d_args"] if str(a).strip())
            if args:
                changed["usb_bridge_d_args"] = args

    history = data.get("history") or {}
    if isinstance(history, dict):
        if "sample_interval_seconds" in history:
            changed["sample_interval_seconds"] = max(
                10, _as_int(history["sample_interval_seconds"], cfg.sample_interval_seconds)
            )
        if "retention_days" in history:
            changed["retention_days"] = max(
                1, _as_int(history["retention_days"], cfg.retention_days)
            )

    alerts = data.get("alerts") or {}
    if isinstance(alerts, dict) and "dedup_minutes" in alerts:
        changed["alert_dedup_minutes"] = max(
            0, _as_int(alerts["dedup_minutes"], cfg.alert_dedup_minutes)
        )

    thresholds = data.get("temp_thresholds") or {}
    if isinstance(thresholds, dict):
        merged = dict(cfg.temp_thresholds)
        for media, spec in thresholds.items():
            if not isinstance(spec, dict):
                continue
            if media not in merged:
                continue
            old = merged[media]
            merged[media] = TempThreshold(
                green_below=_as_int(spec.get("green_below"), old.green_below),
                yellow_max=_as_int(spec.get("yellow_max"), old.yellow_max),
            )
        changed["temp_thresholds"] = merged

    ui = data.get("ui") or {}
    if isinstance(ui, dict) and isinstance(ui.get("pinned_attribute_ids"), list):
        ids = tuple(
            _as_int(v, -1) for v in ui["pinned_attribute_ids"] if _as_int(v, -1) > 0
        )
        if ids:
            changed["pinned_attribute_ids"] = ids

    return replace(cfg, **changed) if changed else cfg


def load_config(path: Path | str | None = None) -> tuple[Config, str | None]:
    """读取配置。

    返回 `(配置对象, 警告信息)`。文件不存在不算错误；文件损坏时回退默认配置
    并给出中文警告（提示词第 11 节：不要吞掉异常不提示）。
    """
    target = Path(path) if path is not None else default_config_path()
    if not target.exists():
        return DEFAULT_CONFIG, None
    if tomllib is None:  # pragma: no cover - 3.10 且没装 tomli
        return DEFAULT_CONFIG, (
            f"当前 Python 缺少 TOML 解析器，无法读取 {target}，"
            "已回退默认配置（可执行 pip install tomli 后再试）。"
        )
    try:
        with target.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return DEFAULT_CONFIG, f"配置文件 {target} 读取失败，已回退默认配置：{exc}"
    if not isinstance(data, dict):
        return DEFAULT_CONFIG, f"配置文件 {target} 内容不是键值表，已回退默认配置。"
    return config_from_dict(data), None

"""历史记录存储（提示词第 7 节）。

设计要点：

* SQLite（不是 CSV 主存），路径 `$XDG_DATA_HOME/knowdiskdrive/history.db`，权限 0600；
* 开启 WAL，避免 GUI 线程与守护线程写库互相锁死；
* 所有写操作短事务、立即提交，绝不在事务里做 smartctl 调用；
* 时间统一存 UTC ISO8601 文本（`2026-11-17T15:04:05Z`），排序与区间查询都靠字符串比较。

性能约束（用户要求：不要触发 OOM）：查询接口一律带 `limit`，图表只取抽样点。
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import default_db_path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS temp_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    serial       TEXT    NOT NULL,
    ts_utc       TEXT    NOT NULL,
    temp_c       INTEGER,
    health_state TEXT,
    device_state TEXT
);
CREATE INDEX IF NOT EXISTS idx_temp_samples_serial_ts
    ON temp_samples (serial, ts_utc);

CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT    NOT NULL,
    ts_utc TEXT    NOT NULL,
    kind   TEXT    NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_serial_kind_ts
    ON events (serial, kind, ts_utc);

CREATE TABLE IF NOT EXISTS test_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    serial       TEXT    NOT NULL,
    ts_utc       TEXT    NOT NULL,
    kind         TEXT,
    status       TEXT,
    progress_pct INTEGER
);
CREATE INDEX IF NOT EXISTS idx_test_results_serial_ts
    ON test_results (serial, ts_utc);

CREATE TABLE IF NOT EXISTS device_prefs (
    serial           TEXT PRIMARY KEY,
    smartctl_d_arg   TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: 一次导出/绘图最多取回的采样行数（防止内存爆掉）
MAX_ROWS = 20000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime | float | int | None = None) -> str:
    """统一的时间戳格式（UTC，秒精度，Z 结尾）。"""
    if moment is None:
        moment = utc_now()
    if isinstance(moment, (int, float)):
        moment = datetime.fromtimestamp(float(moment), tz=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    """把 to_iso() 产物解析回 datetime（坏数据回退到当前时间）。"""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return utc_now()


def to_local_text(text: str) -> str:
    """把 UTC 时间戳转成本地时区 "YYYY-MM-DD HH:MM" 供界面显示。"""
    return parse_iso(text).astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class Sample:
    ts_utc: str
    temp_c: int | None
    health_state: str | None
    device_state: str | None


@dataclass(frozen=True)
class Event:
    serial: str
    ts_utc: str
    kind: str
    detail: str | None


@dataclass(frozen=True)
class TestResult:
    serial: str
    ts_utc: str
    kind: str | None
    status: str | None
    progress_pct: int | None


class HistoryStore:
    """history.db 的唯一访问层。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._conn: sqlite3.Connection | None = None

    # -- 连接管理 ---------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        except sqlite3.DatabaseError:
            conn.close()
            raise
        if created:
            self._restrict_permissions()
        else:
            self._restrict_permissions()
        return conn

    def _restrict_permissions(self) -> None:
        """提示词 7：文件权限 0600（同盘的其他人不能读到磁盘序列号与历史）。"""
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "HistoryStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 写入 -------------------------------------------------------------
    def record_sample(
        self,
        serial: str,
        temp_c: int | None,
        health_state: str | None,
        device_state: str | None = None,
        ts: datetime | float | int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO temp_samples (serial, ts_utc, temp_c, health_state, device_state)"
            " VALUES (?, ?, ?, ?, ?)",
            (serial, to_iso(ts), temp_c, health_state, device_state),
        )
        self.conn.commit()

    def record_event(
        self,
        serial: str,
        kind: str,
        detail: str | None = None,
        ts: datetime | float | int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO events (serial, ts_utc, kind, detail) VALUES (?, ?, ?, ?)",
            (serial, to_iso(ts), kind, detail),
        )
        self.conn.commit()

    def record_test_result(
        self,
        serial: str,
        kind: str | None,
        status: str | None,
        progress_pct: int | None = None,
        ts: datetime | float | int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO test_results (serial, ts_utc, kind, status, progress_pct)"
            " VALUES (?, ?, ?, ?, ?)",
            (serial, to_iso(ts), kind, status, progress_pct),
        )
        self.conn.commit()

    # -- 读取 -------------------------------------------------------------
    def latest_sample(self, serial: str) -> Sample | None:
        row = self.conn.execute(
            "SELECT ts_utc, temp_c, health_state, device_state FROM temp_samples"
            " WHERE serial = ? ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (serial,),
        ).fetchone()
        return _sample(row) if row else None

    def samples(
        self,
        serial: str,
        since: datetime | str | None = None,
        limit: int = MAX_ROWS,
    ) -> list[Sample]:
        sql = "SELECT ts_utc, temp_c, health_state, device_state FROM temp_samples WHERE serial = ?"
        params: list[Any] = [serial]
        if since is not None:
            sql += " AND ts_utc >= ?"
            params.append(since if isinstance(since, str) else to_iso(since))
        sql += " ORDER BY ts_utc ASC, id ASC LIMIT ?"
        params.append(int(limit))
        return [_sample(row) for row in self.conn.execute(sql, params)]

    def events(self, serial: str | None = None, limit: int = 200) -> list[Event]:
        sql = "SELECT serial, ts_utc, kind, detail FROM events"
        params: list[Any] = []
        if serial:
            sql += " WHERE serial = ?"
            params.append(serial)
        sql += " ORDER BY ts_utc DESC, id DESC LIMIT ?"
        params.append(int(limit))
        return [
            Event(row["serial"], row["ts_utc"], row["kind"], row["detail"])
            for row in self.conn.execute(sql, params)
        ]

    def last_event_at(self, serial: str, kind: str) -> datetime | None:
        """同一事件最近一次发生时间（告警去重用，跨进程重启仍然有效）。"""
        row = self.conn.execute(
            "SELECT ts_utc FROM events WHERE serial = ? AND kind = ?"
            " ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (serial, kind),
        ).fetchone()
        return parse_iso(row["ts_utc"]) if row else None

    def test_results(self, serial: str, limit: int = 50) -> list[TestResult]:
        rows = self.conn.execute(
            "SELECT serial, ts_utc, kind, status, progress_pct FROM test_results"
            " WHERE serial = ? ORDER BY ts_utc DESC, id DESC LIMIT ?",
            (serial, int(limit)),
        )
        return [
            TestResult(r["serial"], r["ts_utc"], r["kind"], r["status"], r["progress_pct"])
            for r in rows
        ]

    def count(self, table: str) -> int:
        if table not in {"temp_samples", "events", "test_results", "device_prefs"}:
            raise ValueError(f"未知表名：{table}")
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    # -- 设备偏好（USB -d 缓存，提示词 3.3 / 7）----------------------------
    def get_pref(self, serial: str) -> str | None:
        row = self.conn.execute(
            "SELECT smartctl_d_arg FROM device_prefs WHERE serial = ?", (serial,)
        ).fetchone()
        return row["smartctl_d_arg"] if row else None

    def set_pref(self, serial: str, d_arg: str | None) -> None:
        self.conn.execute(
            "INSERT INTO device_prefs (serial, smartctl_d_arg) VALUES (?, ?)"
            " ON CONFLICT(serial) DO UPDATE SET smartctl_d_arg = excluded.smartctl_d_arg",
            (serial, d_arg),
        )
        self.conn.commit()

    # -- 维护 -------------------------------------------------------------
    def purge(self, retention_days: int, now: datetime | None = None) -> int:
        """删除超过保留期的采样/事件，返回删除行数（提示词 7：启动时清理）。"""
        if retention_days <= 0:
            return 0
        cutoff = to_iso((now or utc_now()) - timedelta(days=retention_days))
        removed = 0
        for table in ("temp_samples", "events", "test_results"):
            cur = self.conn.execute(f"DELETE FROM {table} WHERE ts_utc < ?", (cutoff,))
            removed += cur.rowcount or 0
        self.conn.commit()
        return removed

    def export_samples_csv(self, serial: str, path: Path | str, since: datetime | None = None) -> int:
        """把温度序列导出为 CSV（UTF-8 BOM，Excel 友好）。返回写入行数。"""
        import csv

        target = Path(path)
        rows = self.samples(serial, since=since)
        with target.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            writer.writerows(export_rows(rows))
        return len(rows)

    def downsample(self, serial: str, max_points: int = 600, since: datetime | None = None) -> list[Sample]:
        """按时间等距抽样，保证图表点数上界（防止长时间运行后 OOM）。"""
        rows = self.samples(serial, since=since)
        if max_points <= 0 or len(rows) <= max_points:
            return rows
        if max_points == 1:
            return [rows[-1]]
        step = (len(rows) - 1) / (max_points - 1)
        picked = [rows[round(i * step)] for i in range(max_points)]
        picked[-1] = rows[-1]
        return picked


#: 导出 CSV 的表头（提示词 7：导出当前盘温度序列）
CSV_HEADER = ["时间(本地)", "时间(UTC)", "温度(°C)", "健康", "设备状态"]


def export_rows(samples: Iterable[Sample]) -> list[list[object]]:
    """把采样行转成 CSV 行（不含表头），方便 GUI 与 CLI 复用同一格式。"""
    return [
        [
            to_local_text(sample.ts_utc),
            sample.ts_utc,
            "" if sample.temp_c is None else sample.temp_c,
            sample.health_state or "",
            sample.device_state or "",
        ]
        for sample in samples
    ]


def _sample(row: sqlite3.Row) -> Sample:
    return Sample(
        ts_utc=row["ts_utc"],
        temp_c=row["temp_c"],
        health_state=row["health_state"],
        device_state=row["device_state"],
    )


def open_store(path: Path | str | None = None) -> HistoryStore:
    """打开（必要时创建）历史库。"""
    return HistoryStore(path)


def wait_until_flushed(store: HistoryStore) -> None:
    """测试辅助：确保 WAL 内容落盘。"""
    store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

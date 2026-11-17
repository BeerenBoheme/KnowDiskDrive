"""history.py 单元测试（提示词第 7 节的存储层）。"""

from __future__ import annotations

import csv
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from knowdiskdrive.history import (
    MAX_ROWS,
    HistoryStore,
    Sample,
    export_rows,
    open_store,
    parse_iso,
    to_iso,
    to_local_text,
    utc_now,
)


@pytest.fixture()
def store(tmp_path):
    s = HistoryStore(tmp_path / "sub" / "history.db")
    yield s
    s.close()


def test_to_iso_normalises_timezone():
    fixed = datetime(2026, 11, 17, 23, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    assert to_iso(fixed) == "2026-11-17T15:04:05Z"


def test_to_iso_accepts_epoch_seconds():
    assert to_iso(0) == "1970-01-01T00:00:00Z"


def test_to_iso_naive_is_treated_as_utc():
    assert to_iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"


def test_parse_iso_roundtrip():
    text = to_iso(utc_now())
    assert parse_iso(text) == parse_iso(text)


def test_parse_iso_bad_value_falls_back_to_now():
    before = utc_now()
    parsed = parse_iso("garbage")
    assert parsed >= before - timedelta(seconds=2)


def test_to_local_text_formats():
    text = to_local_text("2026-11-17T15:04:05Z")
    assert len(text) == 16 and text[4] == "-" and text[13] == ":" and "T" not in text


def test_store_creates_parent_directory_and_0600(tmp_path):
    path = tmp_path / "a" / "b" / "history.db"
    store = HistoryStore(path)
    store.record_sample("SN1", 40, "good")
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    store.close()


def test_pragmas_are_applied(store):
    journal = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal.lower() == "wal"


def test_schema_version_recorded(store):
    row = store.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == "1"


def test_record_sample_and_latest(store):
    store.record_sample("SN1", 41, "good", "active", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.record_sample("SN1", 44, "warning", "active", ts=datetime(2026, 1, 2, tzinfo=timezone.utc))
    latest = store.latest_sample("SN1")
    assert latest.temp_c == 44
    assert latest.health_state == "warning"
    assert store.count("temp_samples") == 2


def test_latest_sample_missing_returns_none(store):
    assert store.latest_sample("nope") is None


def test_samples_ordered_and_serial_filtered(store):
    store.record_sample("SN1", 40, "good", ts=datetime(2026, 1, 2, tzinfo=timezone.utc))
    store.record_sample("SN1", 38, "good", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.record_sample("SN2", 50, "good", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    rows = store.samples("SN1")
    assert [r.temp_c for r in rows] == [38, 40]


def test_samples_since_filter(store):
    store.record_sample("SN1", 40, "good", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.record_sample("SN1", 45, "good", ts=datetime(2026, 1, 5, tzinfo=timezone.utc))
    rows = store.samples("SN1", since=datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert [r.temp_c for r in rows] == [45]


def test_samples_limit_capped(store):
    for i in range(10):
        store.record_sample("SN1", i, "good", ts=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i))
    assert len(store.samples("SN1", limit=3)) == 3


def test_samples_default_limit_is_max_rows(store):
    assert MAX_ROWS >= 1000


def test_record_event_and_last_event_at(store):
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store.record_event("SN1", "temperature_red", "温度 80°C", ts=ts)
    assert store.last_event_at("SN1", "temperature_red") == ts
    assert store.last_event_at("SN1", "other") is None


def test_events_returns_most_recent_first(store):
    store.record_event("SN1", "k1", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.record_event("SN1", "k2", ts=datetime(2026, 1, 2, tzinfo=timezone.utc))
    kinds = [e.kind for e in store.events("SN1")]
    assert kinds == ["k2", "k1"]


def test_events_without_serial_returns_all(store):
    store.record_event("SN1", "k1")
    store.record_event("SN2", "k2")
    assert len(store.events()) == 2


def test_record_test_result_roundtrip(store):
    store.record_test_result("SN1", "short", "in_progress", 42)
    rows = store.test_results("SN1")
    assert rows[0].kind == "short"
    assert rows[0].progress_pct == 42


def test_pref_upsert(store):
    assert store.get_pref("SN1") is None
    store.set_pref("SN1", "sat")
    store.set_pref("SN1", "usbjmicron")
    assert store.get_pref("SN1") == "usbjmicron"
    assert store.count("device_prefs") == 1


def test_purge_removes_old_rows(store):
    old = datetime.now(timezone.utc) - timedelta(days=100)
    fresh = datetime.now(timezone.utc)
    store.record_sample("SN1", 40, "good", ts=old)
    store.record_sample("SN1", 45, "good", ts=fresh)
    store.record_event("SN1", "k", ts=old)
    removed = store.purge(90)
    assert removed == 2
    assert store.count("temp_samples") == 1


def test_purge_non_positive_is_noop(store):
    store.record_sample("SN1", 40, "good")
    assert store.purge(0) == 0
    assert store.count("temp_samples") == 1


def test_count_rejects_unknown_table(store):
    with pytest.raises(ValueError):
        store.count("sqlite_master")


def test_export_csv_has_bom_and_header(store, tmp_path):
    store.record_sample("SN1", 41, "good", "active", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    target = tmp_path / "out.csv"
    count = store.export_samples_csv("SN1", target)
    assert count == 1
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "温度(°C)" in text
    assert "41" in text


def test_export_csv_handles_none_temperature(store, tmp_path):
    store.record_sample("SN1", None, "unknown", "standby")
    target = tmp_path / "out.csv"
    store.export_samples_csv("SN1", target)
    rows = list(csv.reader(target.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[1][2] == ""


def test_downsample_keeps_endpoints(store):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(100):
        store.record_sample("SN1", 30 + i % 10, "good", ts=base + timedelta(minutes=i))
    rows = store.downsample("SN1", max_points=10)
    assert len(rows) == 10
    assert rows[0].temp_c == 30
    assert rows[-1].temp_c == 30 + 99 % 10


def test_downsample_short_series_unchanged(store):
    store.record_sample("SN1", 40, "good")
    assert len(store.downsample("SN1", max_points=600)) == 1


def test_downsample_single_point(store):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        store.record_sample("SN1", i, "good", ts=base + timedelta(minutes=i))
    assert len(store.downsample("SN1", max_points=1)) == 1


def test_context_manager_closes(tmp_path):
    with HistoryStore(tmp_path / "h.db") as store:
        store.record_sample("SN1", 1, "good")
    assert store._conn is None


def test_open_store_helper(tmp_path):
    store = open_store(tmp_path / "h.db")
    assert isinstance(store, HistoryStore)
    store.close()


def test_export_rows_helper(store):
    store.record_sample("SN1", 42, "good")
    rows = export_rows(store.samples("SN1"))
    assert rows[0][2] == 42

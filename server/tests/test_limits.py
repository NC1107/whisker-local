from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.bridge import MAX_MESSAGE_BYTES, RobotBridge
from whisker_local.config import Config
from whisker_local.limits import TokenBucket
from whisker_local.store import Store

SERIAL = "LR4C000001"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def activity(code: str = "0x560010"):
    return parse_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": [code]}))


def make_bridge(tmp_path: Path, store: Store, **options) -> RobotBridge:
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    return RobotBridge(config, store, **options)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "h.db")


def count(store: Store, table: str) -> int:
    return store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_bucket_allows_a_burst_then_the_refill_rate():
    now = [0.0]
    bucket = TokenBucket(rate=2, burst=3, clock=lambda: now[0])
    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]
    now[0] = 1.0
    assert [bucket.allow() for _ in range(3)] == [True, True, False]


def test_bucket_never_saves_up_more_than_its_burst():
    now = [0.0]
    bucket = TokenBucket(rate=1, burst=2, clock=lambda: now[0])
    now[0] = 1000.0
    assert [bucket.allow() for _ in range(3)] == [True, True, False]


def test_a_flood_stops_filling_the_raw_message_table(tmp_path: Path, store: Store, caplog):
    now = [0.0]
    bridge = make_bridge(tmp_path, store, store_limiter=TokenBucket(1, 5, lambda: now[0]))
    with caplog.at_level(logging.WARNING):
        for _ in range(100):
            bridge.ingest(activity(), NOW)
    assert count(store, "messages") == 5
    assert "rate limited" in caplog.text
    assert caplog.text.count("rate limited") == 1


def test_a_message_over_the_size_cap_is_dropped_before_it_is_parsed(
    tmp_path: Path, store: Store, caplog
):
    bridge = make_bridge(tmp_path, store)
    junk = json.dumps({"data": ["0x560010"], "padding": "x" * MAX_MESSAGE_BYTES})
    message = SimpleNamespace(
        topic=f"prod/LR4/{SERIAL}/activity", retain=False, payload=junk.encode()
    )
    with caplog.at_level(logging.WARNING):
        bridge._handle(message, link=None)
    assert count(store, "messages") == 0
    assert bridge.last_seen is None
    assert "over the" in caplog.text


def test_a_normal_message_under_the_cap_is_kept(tmp_path: Path, store: Store):
    bridge = make_bridge(tmp_path, store)
    body = json.dumps({"robotStatus": 4}).encode()
    message = SimpleNamespace(topic=f"prod/LR4/{SERIAL}/state", retain=False, payload=body)
    bridge._handle(message, link=None)
    assert count(store, "messages") == 1


def fill(store: Store, *, old: int, recent: int) -> None:
    for i in range(old):
        store.add_message(NOW - timedelta(days=200, seconds=i), "state", {"n": i})
    for i in range(recent):
        store.add_message(NOW - timedelta(days=1, seconds=i), "state", {"n": i})


def test_pruning_by_age_removes_only_old_raw_messages(store: Store):
    fill(store, old=4, recent=3)
    visit = store.add_event(NOW - timedelta(days=300), "visit", {"weight_lb": 9.0})
    store.add_event(NOW - timedelta(days=250), "alert", {"key": "offline"})

    removed = store.prune_messages(older_than=NOW - timedelta(days=90), max_bytes=10**9)

    assert removed == 4
    assert count(store, "messages") == 3
    assert count(store, "events") == 2
    assert store.visit(visit) is not None


def test_pruning_by_size_keeps_the_newest_messages_within_the_budget(store: Store):
    for i in range(10):
        store.add_message(NOW - timedelta(minutes=10 - i), "state", {"n": i})
    size = store._db.execute("SELECT LENGTH(body) FROM messages LIMIT 1").fetchone()[0]

    store.prune_messages(older_than=NOW - timedelta(days=90), max_bytes=size * 4)

    kept = [row[0] for row in store._db.execute("SELECT body FROM messages ORDER BY id")]
    assert [json.loads(body)["n"] for body in kept] == [6, 7, 8, 9]


def test_pruning_an_untouched_table_removes_nothing(store: Store):
    fill(store, old=0, recent=5)
    assert store.prune_messages(older_than=NOW - timedelta(days=90), max_bytes=10**9) == 0
    assert count(store, "messages") == 5


def test_raw_messages_are_found_by_time_without_a_scan(store: Store):
    plan = store._db.execute(
        "EXPLAIN QUERY PLAN DELETE FROM messages WHERE at < ?", ("2026-01-01",)
    ).fetchall()
    assert any("messages_by_time" in row["detail"] for row in plan)


def test_a_stored_detection_is_found_without_a_scan(store: Store):
    plan = store._db.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM events WHERE kind = 'visit' "
        "AND json_extract(detail, '$.detected_at') = ?",
        ("2026-01-01",),
    ).fetchall()
    assert any("events_by_detection" in row["detail"] for row in plan)

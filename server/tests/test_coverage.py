"""Which days have data: online records, gaps in imported history, and unreliable ranges."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.bridge import OFFLINE_AFTER, RobotBridge
from whisker_local.config import Config
from whisker_local.coverage import Coverage, InvalidRange, RangeNotFound
from whisker_local.store import Store

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer right-key"}
SERIAL = "LR4C000001"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "h.db")


@pytest.fixture
def coverage(store: Store) -> Coverage:
    return Coverage(store, NEW_YORK, clock=lambda: NOW)


def local(day: str, hour: int = 12, minute: int = 0) -> datetime:
    year, month, number = map(int, day.split("-"))
    return datetime(year, month, number, hour, minute, tzinfo=NEW_YORK).astimezone(UTC)


def states(coverage: Coverage, start: str, end: str) -> dict[str, str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return {day.date.isoformat(): day.state for day in coverage.days(first, last)}


def test_a_day_the_robot_was_online_all_day_is_full(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-05", 0), True)

    assert states(coverage, "2026-11-06", "2026-11-07") == {
        "2026-11-06": "full",
        "2026-11-07": "full",
    }


def test_a_day_with_the_robot_offline_for_half_of_it_is_partial(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-05", 0), True)
    store.add_presence(local("2026-11-06", 12), False)
    store.add_presence(local("2026-11-07", 12), True)

    assert states(coverage, "2026-11-05", "2026-11-07") == {
        "2026-11-05": "full",
        "2026-11-06": "partial",
        "2026-11-07": "partial",
    }


def test_a_day_entirely_offline_is_none_not_a_quiet_day(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-04", 0), True)
    store.add_presence(local("2026-11-05", 20), False)
    store.add_presence(local("2026-11-08", 7), True)

    found = states(coverage, "2026-11-05", "2026-11-08")

    assert found["2026-11-06"] == found["2026-11-07"] == "none"
    assert found["2026-11-05"] == "partial"


def test_the_day_in_progress_is_never_full(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-01", 0), True)

    assert states(coverage, "2026-11-08", "2026-11-08") == {"2026-11-08": "partial"}


def test_before_any_record_there_is_no_data(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-05", 0), True)

    assert states(coverage, "2026-11-01", "2026-11-04") == dict.fromkeys(
        ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"), "none"
    )


def test_a_dst_day_is_judged_over_its_real_length(store: Store, coverage: Coverage):
    store.add_presence(local("2026-10-31", 0), True)

    assert states(coverage, "2026-11-01", "2026-11-01") == {"2026-11-01": "full"}


def history_with_a_long_silence(store: Store) -> None:
    """Imported-style events with a 60 hour hole, like the real export."""
    for day, hour in [("2026-11-03", 9), ("2026-11-03", 20), ("2026-11-04", 8), ("2026-11-04", 21)]:
        store.add_event(local(day, hour), "visit", {"imported": True})
    store.add_event(local("2026-11-05", 19, 12), "clean_cycle", {"imported": True})
    store.add_event(local("2026-11-08", 7, 28), "clean_cycle", {"imported": True})
    store.add_event(local("2026-11-08", 7, 36), "visit", {"imported": True})
    store.add_event(local("2026-11-08", 18, 0), "visit", {"imported": True})


def test_a_long_silence_in_imported_history_is_a_gap_in_the_days_it_covers(
    store: Store, coverage: Coverage
):
    history_with_a_long_silence(store)

    found = states(coverage, "2026-11-02", "2026-11-08")

    assert found["2026-11-06"] == found["2026-11-07"] == "none"
    assert found["2026-11-05"] == "partial"
    assert found["2026-11-08"] == "partial"
    assert found["2026-11-04"] == "full"


def test_the_first_day_of_imported_history_is_partial_not_full(store: Store, coverage: Coverage):
    history_with_a_long_silence(store)

    found = states(coverage, "2026-11-02", "2026-11-04")

    assert found["2026-11-02"] == "none"
    assert found["2026-11-03"] == "partial"


def test_an_ordinary_overnight_silence_is_not_a_gap(store: Store, coverage: Coverage):
    for hour in (7, 12, 18):
        store.add_event(local("2026-11-03", hour), "visit", {})
    for hour in (7, 20):
        store.add_event(local("2026-11-04", hour), "visit", {})
    store.add_event(local("2026-11-05", 23), "visit", {})

    assert states(coverage, "2026-11-04", "2026-11-04") == {"2026-11-04": "full"}


def test_a_day_with_events_is_never_none_even_if_the_record_says_offline(
    store: Store, coverage: Coverage
):
    store.add_presence(local("2026-11-04", 0), True)
    store.add_presence(local("2026-11-05", 0), False)
    store.add_event(local("2026-11-06", 9), "visit", {})

    assert states(coverage, "2026-11-06", "2026-11-06") == {"2026-11-06": "partial"}


def test_the_online_record_wins_over_inference_once_it_starts(store: Store, coverage: Coverage):
    history_with_a_long_silence(store)
    store.add_presence(local("2026-11-08", 7, 28), True)
    store.add_presence(local("2026-11-08", 20, 0), False)

    day = coverage.days(date(2026, 11, 8), date(2026, 11, 8))[0]

    assert day.source == "online_record"


def test_a_range_can_be_marked_unreliable_and_stays_visible(store: Store, coverage: Coverage):
    store.add_presence(local("2026-11-01", 0), True)
    marked = coverage.mark_unreliable(date(2026, 11, 2), date(2026, 11, 4), " moved into cabinet ")

    found = {
        day.date.isoformat(): day for day in coverage.days(date(2026, 11, 1), date(2026, 11, 5))
    }

    assert marked["note"] == "moved into cabinet"
    assert found["2026-11-01"].unreliable is None
    assert found["2026-11-03"].unreliable == {"id": marked["id"], "note": "moved into cabinet"}
    assert found["2026-11-03"].state == "full"
    assert found["2026-11-03"].counts is False
    assert found["2026-11-01"].counts is True


def test_a_backwards_range_is_refused_and_a_missing_one_cannot_be_removed(coverage: Coverage):
    with pytest.raises(InvalidRange):
        coverage.mark_unreliable(date(2026, 11, 4), date(2026, 11, 2), "x")
    with pytest.raises(RangeNotFound):
        coverage.unmark_unreliable(99)


def test_api_marks_lists_and_removes_unreliable_ranges(client: TestClient):
    created = client.post(
        "/api/coverage/unreliable",
        json={"start": "2026-11-02", "end": "2026-11-08", "note": "box in a cabinet"},
        headers=AUTH,
    )
    assert created.status_code == 201
    range_id = created.json()["id"]

    view = client.get(
        "/api/coverage", params={"start": "2026-11-01", "end": "2026-11-03"}, headers=AUTH
    ).json()
    assert [d["date"] for d in view["days"]] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert view["days"][1]["unreliable"] == {"id": range_id, "note": "box in a cabinet"}
    assert view["unreliable_ranges"][0]["note"] == "box in a cabinet"

    listed = client.get("/api/coverage/unreliable", headers=AUTH).json()
    assert [(r["id"], r["start_date"], r["end_date"]) for r in listed] == [
        (range_id, "2026-11-02", "2026-11-08")
    ]

    assert client.delete(f"/api/coverage/unreliable/{range_id}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/coverage/unreliable/{range_id}", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"start": "2026-11-08", "end": "2026-11-02", "note": "x"},
        {"start": "2026-11-02", "end": "2026-11-08", "note": ""},
        {"start": "2026-11-02", "end": "2026-11-08"},
    ],
)
def test_api_refuses_unusable_ranges(client: TestClient, body: dict):
    response = client.post("/api/coverage/unreliable", json=body, headers=AUTH)
    assert response.status_code == 422


def test_api_coverage_routes_need_the_key(client: TestClient):
    assert client.get("/api/coverage").status_code == 401
    assert client.post("/api/coverage/unreliable", json={}).status_code == 401


def doc():
    return parse_message(f"prod/LR4/{SERIAL}/state", json.dumps({"robotStatus": 4, "catDetect": 0}))


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    return RobotBridge(config, store)


def test_the_first_message_records_the_robot_online(bridge: RobotBridge, store: Store):
    bridge.ingest(doc(), NOW)
    bridge.ingest(doc(), NOW + timedelta(minutes=2))

    assert store.presence() == [(NOW, True)]


def test_a_silence_is_recorded_when_the_next_message_ends_it(bridge: RobotBridge, store: Store):
    bridge.ingest(doc(), NOW)
    bridge.ingest(doc(), NOW + timedelta(minutes=2))
    bridge.ingest(doc(), NOW + timedelta(hours=5))

    assert store.presence() == [
        (NOW, True),
        (NOW + timedelta(minutes=2), False),
        (NOW + timedelta(hours=5), True),
    ]


def test_a_silence_is_recorded_while_it_lasts(bridge: RobotBridge, store: Store):
    bridge.ingest(doc(), NOW)

    bridge._settle_visits(NOW + OFFLINE_AFTER - timedelta(seconds=1))
    assert store.presence() == [(NOW, True)]
    bridge._settle_visits(NOW + OFFLINE_AFTER + timedelta(seconds=1))
    assert store.presence() == [(NOW, True), (NOW, False)]
    bridge._settle_visits(NOW + timedelta(hours=1))
    assert len(store.presence()) == 2


def test_a_restart_closes_the_online_period_at_the_last_message(
    tmp_path: Path, store: Store, bridge: RobotBridge
):
    bridge.ingest(doc(), NOW)
    store.add_message(NOW + timedelta(minutes=1), "state", {})

    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    restarted = RobotBridge(config, store)
    restarted.ingest(doc(), NOW + timedelta(hours=1))

    assert store.presence() == [
        (NOW, True),
        (NOW + timedelta(minutes=1), False),
        (NOW + timedelta(hours=1), True),
    ]


def old_database(path: Path) -> None:
    """A database as the first release made it, with history in it."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE messages (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
            body TEXT NOT NULL);
        CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
            detail TEXT NOT NULL);
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE cats (id INTEGER PRIMARY KEY, name TEXT NOT NULL, color_index INTEGER NOT NULL,
            estimated_weight_lb REAL, created_at TEXT NOT NULL, archived_at TEXT);
        CREATE TABLE visit_labels (event_id INTEGER PRIMARY KEY, label TEXT NOT NULL,
            cat_id INTEGER, source TEXT NOT NULL);
        CREATE TABLE maintenance_log (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
            done_on TEXT NOT NULL, recorded_at TEXT NOT NULL, UNIQUE (kind, done_on));
        INSERT INTO events (at, kind, detail) VALUES
            ('2026-11-01T12:00:00.000+00:00', 'visit', '{"weight_lb": 9.0}');
        """
    )
    db.commit()
    db.close()


def test_an_old_database_is_backed_up_once_then_gains_the_new_tables(tmp_path: Path):
    path = tmp_path / "old.db"
    old_database(path)

    upgraded = Store(path)

    backups = list((tmp_path / "backups").glob("*before-upgrade*.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "robot_presence" not in tables
    assert old.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert upgraded.presence() == []
    assert len(upgraded.events(limit=5)) == 1

    upgraded.close()
    Store(path)
    assert len(list((tmp_path / "backups").glob("*before-upgrade*.db"))) == 1


def test_a_new_database_is_not_backed_up(tmp_path: Path):
    Store(tmp_path / "fresh.db")
    assert not (tmp_path / "backups").exists()

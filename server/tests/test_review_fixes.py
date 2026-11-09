"""Defects found in review: lost hand labels, half-written backups, slow replays, start-up."""

from __future__ import annotations

import os
import random
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local import cats as cats_module
from whisker_local import store as store_module
from whisker_local.api import IDENTITY_VERSION, IDENTITY_VERSION_KEY, create_app
from whisker_local.bridge import RobotBridge
from whisker_local.cats import CatTracker
from whisker_local.config import Config
from whisker_local.store import Store
from whisker_local.whisker_import import Importer, parse

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
REFERENCE = date(2026, 11, 8)
TWO_READINGS = (
    "Activity,Timestamp,Value\n"
    "Weight recorded,11/8 at 9:12 AM,8.4 lbs\n"
    "Weight recorded,11/8 at 9:03 AM,8.9 lbs\n"
    "Cat detected,11/8 at 9:01 AM,-\n"
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def cats(store: Store) -> CatTracker:
    return CatTracker(store, NEW_YORK, clock=lambda: NOW)


def legacy_pair(store: Store, cats: CatTracker) -> tuple[int, int, int]:
    alpha = cats.create_cat("Alpha", 10.2)["id"]
    beta = cats.create_cat("Beta", 8.7)["id"]
    mark = {"imported": True, "import_batch": "aaaaaaaaaaaa", "imported_at": "x"}
    for at, weight in parse(TWO_READINGS, NEW_YORK, REFERENCE).weights:
        store.add_event(at, "visit", {"weight_lb": weight, **mark})
    return alpha, beta, len(store.visits())


def test_reprocess_never_drops_one_of_two_different_hand_labels(store, cats):
    alpha, beta, _ = legacy_pair(store, cats)
    first, second = store.visits()
    cats.label_visit(first["id"], "cat", beta)
    cats.label_visit(second["id"], "cat", alpha)

    summary = Importer(store, cats, NEW_YORK, clock=lambda: NOW).reprocess(
        TWO_READINGS, reference=REFERENCE, dry_run=False
    )

    assert summary["unmerged_conflicts"] == 1
    assert summary["merged"] == 0
    labels = {(v["cat_id"], v["label_source"]) for v in store.visits()}
    assert labels == {(alpha, "user"), (beta, "user")}


def test_reprocess_merges_rows_whose_hand_labels_agree(store, cats):
    _, beta, _ = legacy_pair(store, cats)
    for visit in store.visits():
        cats.label_visit(visit["id"], "cat", beta)

    summary = Importer(store, cats, NEW_YORK, clock=lambda: NOW).reprocess(
        TWO_READINGS, reference=REFERENCE, dry_run=False
    )

    assert (summary["unmerged_conflicts"], summary["merged"]) == (0, 1)
    [only] = store.visits()
    assert (only["cat_id"], only["label_source"]) == (beta, "user")


def test_a_failed_backup_leaves_nothing_that_looks_like_one(store, tmp_path, monkeypatch):
    store.add_event(NOW, "visit", {"weight_lb": 9.0})

    def fail(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(store_module.os, "replace", fail)
    with pytest.raises(OSError, match="disk went away"):
        store.save_backup("test")

    assert list((tmp_path / "backups").iterdir()) == []


def test_a_backup_is_private_and_complete(store, tmp_path):
    store.add_event(NOW, "visit", {"weight_lb": 9.0})

    backup = store.save_backup("test")

    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert len(Store(backup).visits()) == 1


def test_heard_days_answers_each_question_without_walking_every_day(store):
    start = date(2023, 1, 1)
    tracker = CatTracker(store, UTC, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    store.add_presence(datetime(2023, 1, 1, tzinfo=UTC), True)
    store.add_presence(datetime(2024, 5, 1, tzinfo=UTC), False)
    store.add_presence(datetime(2024, 6, 1, tzinfo=UTC), True)
    first = datetime(2023, 1, 1, 12, tzinfo=UTC)
    heard = tracker._heard_days(first)
    known = tracker.coverage.days(start - timedelta(days=1), tracker.today())

    def brute(low: datetime, high: datetime) -> float:
        total = 0.0
        for day in known:
            begin = tracker.day_start(day.date)
            end = tracker.day_start(day.date + timedelta(days=1))
            overlap = (min(end, high) - max(begin, low)).total_seconds()
            if overlap > 0 and day.state != "none":
                total += overlap / 86400
        return total

    rng = random.Random(1)
    for _ in range(200):
        low = first + timedelta(hours=rng.uniform(0, 24 * 1000))
        high = low + timedelta(hours=rng.uniform(0, 24 * 60))
        assert heard(low, high) == pytest.approx(brute(low, high), abs=1e-6)

    calls = 0
    real = cats_module.bisect.bisect_right

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    cats_module.bisect.bisect_right = counting
    try:
        for _ in range(1000):
            heard(first, first + timedelta(days=900))
    finally:
        cats_module.bisect.bisect_right = real
    # Two lookups per question, however many days there are.
    assert calls == 2000


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config("LR4C000001", "right-key", "broker", 1883, tmp_path / "h.db", tmp_path)


def test_a_failing_rebuild_does_not_stop_the_server_and_is_retried(config, monkeypatch):
    store = Store(config.db_path)

    def boom(self):
        raise RuntimeError("bad history")

    monkeypatch.setattr(CatTracker, "rebuild", boom)
    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)) as c:
        assert c.get("/api/health").status_code == 200
        assert store.get_json(IDENTITY_VERSION_KEY) is None


def test_start_up_removes_identities_of_visits_that_are_gone(config):
    store = Store(config.db_path)
    store.apply_auto_labels({}, {99: ("confident", 1, 0.99)})
    store._db.execute("INSERT INTO visit_labels VALUES (99, 'cat', 1, 'user')")

    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)):
        assert store._db.execute("SELECT COUNT(*) FROM visit_identity").fetchone()[0] == 0
        assert store._db.execute("SELECT COUNT(*) FROM visit_labels").fetchone()[0] == 0


def test_a_reused_event_id_does_not_inherit_a_stale_identity_or_hand_label(store):
    store.apply_auto_labels({}, {1: ("confident", 1, 0.99)})
    store._db.execute("INSERT INTO visit_labels VALUES (1, 'cat', 1, 'user')")

    event = store.add_event(NOW, "visit", {"weight_lb": 9.0})

    assert event == 1
    visit = store.visit(event)
    assert (visit["identity"], visit["label"], visit["label_source"]) == (None, "unknown", "auto")


def test_history_is_named_again_at_every_start_even_when_the_version_matches(config):
    store = Store(config.db_path)
    tracker = CatTracker(store)
    tracker.create_cat("Miso", 10.0)
    event = store.add_event(NOW, "visit", {"weight_lb": 10.0})
    store.put_json(IDENTITY_VERSION_KEY, IDENTITY_VERSION)
    assert store.visit(event)["identity"] is None

    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)):
        assert store.visit(event)["identity"]["status"] == "confident"

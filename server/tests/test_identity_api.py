"""The identity routes, the anchor the owner can reset, and unreliable days not teaching."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.cats import CatTracker, NotEnoughReadings
from whisker_local.config import Config
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
NOW = START + timedelta(days=40)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "h.db")
    store.add_presence(START - timedelta(days=1), True)
    return store


@pytest.fixture
def tracker(store: Store) -> CatTracker:
    return CatTracker(store, clock=lambda: NOW)


def add(store: Store, tracker: CatTracker, day: float, weight: float) -> int:
    event = store.add_event(START + timedelta(days=day), "visit", {"weight_lb": weight})
    tracker.on_visit_saved(event)
    return event


def seed_a_cat(store: Store, tracker: CatTracker, days: int = 20, weight: float = 10.0) -> int:
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for index in range(days):
        add(store, tracker, index * 0.7, weight + (index % 3 - 1) * 0.15)
    return cat


def test_a_cat_gets_its_anchor_from_its_first_readings(store: Store, tracker: CatTracker):
    cat = seed_a_cat(store, tracker)

    summary = tracker.cat(cat)["summary"]

    assert summary["anchor_weight_lb"] == pytest.approx(10.0, abs=0.15)
    assert summary["usual_weight_lb"] == summary["anchor_weight_lb"]


def test_readings_in_an_unreliable_range_do_not_set_the_anchor(store: Store, tracker: CatTracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    store.add_unreliable_range(date(2026, 9, 1), date(2026, 9, 4), "moved into a cabinet", NOW)
    for index in range(6):
        add(store, tracker, index * 0.5, 9.2)
    for index in range(20):
        add(store, tracker, 5 + index * 0.7, 10.0 + (index % 3 - 1) * 0.15)

    assert tracker.cat(cat)["summary"]["anchor_weight_lb"] == pytest.approx(10.0, abs=0.15)


def test_confident_visits_leave_out_unreliable_days_unless_asked(store: Store, tracker: CatTracker):
    cat = seed_a_cat(store, tracker, days=12)
    store.add_unreliable_range(date(2026, 9, 3), date(2026, 9, 4), "moved the box", NOW)

    kept = tracker.confident_visits(cat)
    everything = tracker.confident_visits(cat, reliable_only=False)

    assert len(everything) == 12
    assert 0 < len(kept) < len(everything)
    assert all(not ("2026-09-03" <= v["at"][:10] <= "2026-09-04") for v in kept)


def test_the_owner_can_re_anchor_to_a_weight_or_to_the_latest_readings(
    store: Store, tracker: CatTracker
):
    cat = seed_a_cat(store, tracker)
    for index in range(16):
        add(store, tracker, 30 + index * 0.3, 10.4)

    by_hand = tracker.set_anchor(cat, 9.8)
    assert (by_hand["weight_lb"], by_hand["source"]) == (9.8, "owner")

    recent = tracker.set_anchor(cat, None)
    assert recent["weight_lb"] == pytest.approx(10.4, abs=0.2)
    assert recent["source"] == "re-anchored"


def test_re_anchoring_to_readings_needs_enough_of_them(store: Store, tracker: CatTracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add(store, tracker, 1, 10.0)

    with pytest.raises(NotEnoughReadings):
        tracker.set_anchor(cat, None)


def test_api_anchor_and_identity_routes(client: TestClient, parts):
    _, store, _ = parts
    cat = client.post("/api/cats", json={"name": "Miso", "reference_weight_lb": 10}, headers=AUTH)
    cat_id = cat.json()["id"]
    tracker = CatTracker(store)
    for index in range(16):
        event = store.add_event(
            datetime.now(UTC) - timedelta(days=12 - index * 0.7), "visit", {"weight_lb": 10.1}
        )
        tracker.on_visit_saved(event)

    set_by_hand = client.put(f"/api/cats/{cat_id}/anchor", json={"weight_lb": 9.9}, headers=AUTH)
    assert set_by_hand.status_code == 200
    assert set_by_hand.json()["weight_lb"] == 9.9

    identity = client.get("/api/identity", params={"days": 30}, headers=AUTH).json()
    assert identity["anchors"][str(cat_id)]["weight_lb"] == 9.9
    assert set(identity) >= {"visits", "named", "not_sure", "unsettled", "silent_cats", "by_status"}

    assert client.delete(f"/api/cats/{cat_id}/anchor", headers=AUTH).status_code == 204
    assert client.get("/api/cats", headers=AUTH).json()[0]["summary"]["anchor_weight_lb"] == (
        pytest.approx(10.1, abs=0.1)
    )


def test_api_refuses_an_anchor_without_readings_and_for_a_missing_cat(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()["id"]
    assert client.put(f"/api/cats/{cat}/anchor", json={}, headers=AUTH).status_code == 409
    assert client.put("/api/cats/99/anchor", json={}, headers=AUTH).status_code == 404
    assert (
        client.put(f"/api/cats/{cat}/anchor", json={"weight_lb": -1}, headers=AUTH).status_code
        == 422
    )
    assert client.put(f"/api/cats/{cat}/anchor", json={}).status_code == 401


def test_history_is_named_afresh_once_when_the_rules_change(tmp_path: Path):
    config = Config("LR4C000001", "right-key", "broker", 1883, tmp_path / "h.db", tmp_path)
    store = Store(config.db_path)
    tracker = CatTracker(store)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    old = store.add_event(START, "visit", {"weight_lb": 10.0})
    store.apply_auto_labels({old: cat})
    assert store.visit(old)["identity"] is None

    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)):
        assert store.visit(old)["identity"]["status"] == "confident"

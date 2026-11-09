from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local import alert_settings
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def state(**fields):
    body = json.dumps({"robotStatus": 4, "DFILevelPercent": 20, **fields})
    return parse_message("prod/LR4/LR4C000001/state", body)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


def make_bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config("LR4C000001", "k", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    return RobotBridge(config, store)


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    return make_bridge(tmp_path, store)


def history(store: Store) -> list[dict]:
    return [e["detail"] for e in reversed(store.events(limit=50, kinds=["alert"]))]


def test_a_raised_and_a_cleared_alert_each_leave_an_event(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    assert history(store) == []

    bridge.ingest(state(isDFIFull=1, DFILevelPercent=100), START + timedelta(minutes=1))
    bridge.ingest(state(isDFIFull=0, DFILevelPercent=5), START + timedelta(minutes=9))

    keys = [(d["key"], d["raised"]) for d in history(store)]
    assert ("drawer_full", True) in keys
    assert keys.index(("drawer_full", False)) > keys.index(("drawer_full", True))
    raised = next(d for d in history(store) if d["key"] == "drawer_full" and d["raised"])
    assert raised == {
        "key": "drawer_full",
        "raised": True,
        "urgent": False,
        "message": "Waste drawer full. Empty it and press Reset.",
    }
    cleared = next(d for d in history(store) if d["key"] == "drawer_full" and not d["raised"])
    assert cleared["message"] == "Waste drawer emptied."


def test_urgent_alerts_are_marked_urgent_in_the_history(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(state(isBonnetRemoved=1), START)
    bridge.ingest(state(isBonnetRemoved=1), START + timedelta(minutes=6))

    [event] = history(store)
    assert (event["key"], event["urgent"], event["raised"]) == ("bonnet_removed", True, True)


def test_a_condition_that_holds_is_recorded_once(bridge: RobotBridge, store: Store):
    for minute in range(5):
        bridge.ingest(state(isDFIFull=1), START + timedelta(minutes=minute))

    assert [d["key"] for d in history(store)] == ["drawer_full"]


def test_a_short_blip_that_never_raised_leaves_no_trace(bridge: RobotBridge, store: Store):
    bridge.ingest(state(isBonnetRemoved=1), START)
    bridge.ingest(state(isBonnetRemoved=0), START + timedelta(minutes=2))
    assert history(store) == []


def test_alerts_are_not_announced_again_after_a_restart(tmp_path: Path, store: Store):
    first = make_bridge(tmp_path, store)
    first.ingest(state(isDFIFull=1), START)
    assert len(history(store)) == 1

    second = make_bridge(tmp_path, store)
    second.ingest(state(isDFIFull=1), START + timedelta(minutes=5))

    assert len(history(store)) == 1
    assert second._alerts.active == first._alerts.active


def test_an_alert_that_cleared_while_the_server_was_down_is_cleared_on_return(
    tmp_path: Path, store: Store
):
    make_bridge(tmp_path, store).ingest(state(isDFIFull=1), START)

    second = make_bridge(tmp_path, store)
    second.ingest(state(isDFIFull=0), START + timedelta(hours=2))

    assert [(d["key"], d["raised"]) for d in history(store)] == [
        ("drawer_full", True),
        ("drawer_full", False),
    ]


@pytest.mark.parametrize("stored", ["nonsense", 7, ["not_an_alert"], [3], {"a": 1}])
def test_garbage_in_the_stored_alert_list_is_ignored(tmp_path: Path, store: Store, stored):
    store.put_json("alerts_active", stored)
    assert make_bridge(tmp_path, store)._alerts.active == set()


def test_events_can_be_filtered_by_kind(client: TestClient, parts):
    _, store, _ = parts
    store.add_event(START, "alert", {"key": "offline", "raised": True})
    store.add_event(START, "clean_cycle", {"count": 1})
    store.add_event(START, "visit", {})

    only = client.get("/api/events?kind=alert", headers=AUTH).json()
    assert [e["kind"] for e in only] == ["alert"]
    two = client.get("/api/events?kind=alert&kind=visit", headers=AUTH).json()
    assert sorted(e["kind"] for e in two) == ["alert", "visit"]
    assert len(client.get("/api/events", headers=AUTH).json()) == 3


# Alert settings


def test_defaults_are_the_librarys(client: TestClient):
    body = client.get("/api/alert-settings", headers=AUTH).json()
    assert (
        body["settings"]
        == body["defaults"]
        == {
            "drawer_level_percent": 80,
            "litter_low_percent": 30,
            "no_visit_hours": 24,
            "long_visit_minutes": 5,
            "frequent_visits_per_hour": 5,
            "deep_clean_days": 90,
            "filter_days": 30,
            "globe_clean_days": 60,
        }
    )


def test_patch_changes_only_what_it_names(client: TestClient):
    body = client.patch("/api/alert-settings", json={"drawer_level_percent": 60}, headers=AUTH)
    assert body.json()["settings"]["drawer_level_percent"] == 60
    assert body.json()["settings"]["litter_low_percent"] == 30
    again = client.patch("/api/alert-settings", json={"filter_days": 14}, headers=AUTH).json()
    assert again["settings"]["drawer_level_percent"] == 60
    assert again["settings"]["filter_days"] == 14
    assert again["defaults"]["filter_days"] == 30


def test_delete_goes_back_to_the_defaults(client: TestClient):
    client.patch("/api/alert-settings", json={"drawer_level_percent": 60}, headers=AUTH)
    body = client.delete("/api/alert-settings", headers=AUTH).json()
    assert body["settings"] == body["defaults"]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"drawer_level_percent": 49},
        {"drawer_level_percent": 96},
        {"litter_low_percent": 9},
        {"litter_low_percent": 51},
        {"no_visit_hours": 0},
        {"filter_days": 0},
        {"deep_clean_days": 3651},
        {"filter_days": "soon"},
        {"filter_days": None},
        {"long_visit_minutes": 1},
        {"long_visit_minutes": 6},
        {"frequent_visits_per_hour": 2},
    ],
)
def test_alert_settings_reject_bad_values(client: TestClient, bad: dict):
    assert client.patch("/api/alert-settings", json=bad, headers=AUTH).status_code == 422


def test_a_lower_drawer_threshold_raises_the_alert(client: TestClient, parts):
    _, store, bridge = parts
    now = datetime.now(UTC)
    bridge.ingest(state(DFILevelPercent=65), now)
    assert bridge.status()["alerts"] == []

    client.patch("/api/alert-settings", json={"drawer_level_percent": 60}, headers=AUTH)

    assert [a["key"] for a in bridge.status()["alerts"]] == ["drawer_level"]
    assert history(store)[-1]["message"] == "Waste drawer is 65% full."


def test_maintenance_intervals_follow_the_settings(client: TestClient, parts):
    _, store, bridge = parts
    now = datetime.now(UTC)
    store.add_maintenance("filter", now.date() - timedelta(days=10), now)
    bridge.ingest(state(), now)
    assert bridge.status()["alerts"] == []

    client.patch("/api/alert-settings", json={"filter_days": 7}, headers=AUTH)

    assert [a["key"] for a in bridge.status()["alerts"]] == ["filter_due"]
    filter_item = client.get("/api/maintenance", headers=AUTH).json()["items"][0]
    assert (filter_item["interval_days"], filter_item["days_until_due"]) == (7, -3)


def test_only_deliberate_overrides_are_stored_so_defaults_can_improve(store: Store):
    alert_settings.update(store, {"filter_days": 14})
    assert store.get_json("alert_settings") == {"filter_days": 14}


def test_unusable_stored_values_fall_back_to_defaults(store: Store):
    store.put_json(
        "alert_settings", {"filter_days": 14, "drawer_level_percent": 500, "no_visit_hours": "x",
                           "mystery": 1, "globe_clean_days": True}
    )  # fmt: skip
    loaded = alert_settings.as_dict(alert_settings.load(store))
    assert loaded["filter_days"] == 14
    assert loaded["drawer_level_percent"] == 80
    assert loaded["no_visit_hours"] == 24
    assert loaded["globe_clean_days"] == 60

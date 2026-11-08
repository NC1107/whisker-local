from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.maintenance import MaintenanceInFuture, MaintenanceLog, MaintenanceNotFound
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
TODAY = date(2026, 11, 8)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def log(store: Store) -> MaintenanceLog:
    return MaintenanceLog(store, clock=lambda: NOW)


def item(view: dict, kind: str) -> dict:
    return next(i for i in view["items"] if i["kind"] == kind)


def test_nothing_logged_means_nothing_due(log: MaintenanceLog):
    view = log.view(limit=10)
    assert [i["kind"] for i in view["items"]] == ["filter", "deep_clean", "globe_clean"]
    assert item(view, "filter") == {
        "kind": "filter",
        "label": "Carbon filter",
        "interval_days": 30,
        "last_done": None,
        "due": None,
        "days_until_due": None,
        "is_due": False,
    }
    assert view["log"] == []


def test_due_date_is_last_done_plus_the_interval(log: MaintenanceLog):
    log.record("filter", TODAY - timedelta(days=10))
    log.record("deep_clean", TODAY)

    view = log.view(limit=10)

    assert item(view, "filter")["due"] == "2026-11-28"
    assert item(view, "filter")["days_until_due"] == 20
    assert item(view, "deep_clean")["due"] == "2027-02-06"
    assert item(view, "deep_clean")["interval_days"] == 90


def test_a_job_is_due_on_the_day_and_after(log: MaintenanceLog):
    log.record("filter", TODAY - timedelta(days=30))
    assert item(log.view(limit=1), "filter")["is_due"] is True
    assert item(log.view(limit=1), "filter")["days_until_due"] == 0

    log.record("globe_clean", TODAY - timedelta(days=61))
    assert item(log.view(limit=1), "globe_clean")["days_until_due"] == -1


def test_back_dating_never_moves_last_done_backwards(log: MaintenanceLog):
    log.record("filter", TODAY)
    log.record("filter", TODAY - timedelta(days=40))

    assert item(log.view(limit=10), "filter")["last_done"] == "2026-11-08"
    assert [e["done_on"] for e in log.view(limit=10)["log"]] == ["2026-11-08", "2026-09-29"]


def test_the_same_job_twice_on_one_day_is_one_entry(log: MaintenanceLog):
    first, created = log.record("filter", None)
    again, created_again = log.record("filter", TODAY)

    assert (created, created_again) == (True, False)
    assert first == again
    assert len(log.view(limit=10)["log"]) == 1


def test_the_future_is_refused(log: MaintenanceLog):
    with pytest.raises(MaintenanceInFuture):
        log.record("filter", TODAY + timedelta(days=1))


def test_today_is_the_owners_day_not_utc(store: Store):
    late = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    tokyo = MaintenanceLog(store, ZoneInfo("Asia/Tokyo"), clock=lambda: late)
    chicago = MaintenanceLog(store, ZoneInfo("America/Chicago"), clock=lambda: late)

    assert tokyo.today() == date(2026, 11, 9)
    assert chicago.today() == date(2026, 11, 8)
    assert chicago.record("filter", None)[0]["done_on"] == "2026-11-08"


def test_deleting_a_mistaken_entry_restores_the_previous_date(log: MaintenanceLog):
    log.record("filter", TODAY - timedelta(days=20))
    mistake, _ = log.record("filter", TODAY)

    log.delete(mistake["id"])

    assert item(log.view(limit=10), "filter")["last_done"] == "2026-10-19"
    with pytest.raises(MaintenanceNotFound):
        log.delete(mistake["id"])


# The alerts library only raises *_due alerts when it is handed the dates, so these go through
# the bridge.


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config("LR4C000001", "k", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    return RobotBridge(config, store)


def robot_state():
    return parse_message("prod/LR4/LR4C000001/state", json.dumps({"robotStatus": 4}))


def active(bridge: RobotBridge) -> set[str]:
    return {a["key"] for a in bridge.status()["alerts"]}


def test_overdue_maintenance_raises_its_alert_and_logging_it_clears_it(
    bridge: RobotBridge, store: Store
):
    now = datetime.now(UTC)
    today = now.date()
    store.add_maintenance("filter", today - timedelta(days=31), now)
    store.add_maintenance("deep_clean", today - timedelta(days=89), now)
    bridge.ingest(robot_state(), now)

    assert active(bridge) == {"filter_due"}
    [message] = [a["message"] for a in bridge.status()["alerts"]]
    assert message == "Time for a new carbon filter."

    store.add_maintenance("filter", today, now)
    assert active(bridge) == set()


def test_each_job_has_its_own_alert(bridge: RobotBridge, store: Store):
    now = datetime.now(UTC)
    for kind, days in (("filter", 30), ("deep_clean", 90), ("globe_clean", 60)):
        store.add_maintenance(kind, now.date() - timedelta(days=days), now)
    bridge.ingest(robot_state(), now)

    assert active(bridge) == {"filter_due", "deep_clean_due", "globe_clean_due"}


def test_the_alert_day_rolls_over_in_the_owners_timezone(tmp_path: Path, store: Store):
    config = Config(
        "LR4C000001", "k", "broker", 1883, tmp_path / "h.db", tmp_path / "web",
        ZoneInfo("Pacific/Auckland"),
    )  # fmt: skip
    local_bridge = RobotBridge(config, store)
    now = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)  # already the 9th in Auckland
    store.add_maintenance("filter", date(2026, 10, 10), now)  # due on the 9th
    local_bridge.ingest(robot_state(), now)

    assert "filter_due" in {a["key"] for a in local_bridge._alert_view()}


def test_maintenance_api_round_trip(client: TestClient):
    created = client.post("/api/maintenance", json={"kind": "filter"}, headers=AUTH)
    assert created.status_code == 201
    entry = created.json()
    assert entry["kind"] == "filter"
    assert entry["label"] == "Carbon filter"

    again = client.post("/api/maintenance", json={"kind": "filter"}, headers=AUTH)
    assert again.status_code == 200
    assert again.json() == entry

    view = client.get("/api/maintenance", headers=AUTH).json()
    assert item(view, "filter")["last_done"] == entry["done_on"]
    assert [e["id"] for e in view["log"]] == [entry["id"]]

    assert client.delete(f"/api/maintenance/{entry['id']}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/maintenance/{entry['id']}", headers=AUTH).status_code == 404
    assert client.get("/api/maintenance", headers=AUTH).json()["log"] == []


def test_marking_a_job_done_clears_its_alert_straight_away(client: TestClient, parts):
    _, store, bridge = parts
    now = datetime.now(UTC)
    store.add_maintenance("filter", now.date() - timedelta(days=40), now)
    bridge.ingest(robot_state(), now)
    assert active(bridge) == {"filter_due"}

    client.post("/api/maintenance", json={"kind": "filter"}, headers=AUTH)

    assert active(bridge) == set()
    [cleared] = [
        e for e in store.events(limit=10, kinds=["alert"]) if e["detail"]["raised"] is False
    ]
    assert cleared["detail"]["key"] == "filter_due"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "litter_change"},
        {},
        {"kind": "filter", "done_on": "not-a-date"},
        {"kind": "filter", "done_on": "2019-12-31"},
        {"kind": "filter", "done_on": "2999-01-01"},
        {"kind": "filter", "extra": 1},
    ],
)
def test_maintenance_api_rejects_bad_input(client: TestClient, body: dict):
    assert client.post("/api/maintenance", json=body, headers=AUTH).status_code == 422


def test_maintenance_can_be_back_dated(client: TestClient):
    response = client.post(
        "/api/maintenance", json={"kind": "deep_clean", "done_on": "2026-08-01"}, headers=AUTH
    )
    assert response.status_code == 201
    assert response.json()["done_on"] == "2026-08-01"


def test_maintenance_log_limit(client: TestClient):
    for day in (1, 2, 3):
        client.post(
            "/api/maintenance", json={"kind": "filter", "done_on": f"2026-10-0{day}"}, headers=AUTH
        )
    view = client.get("/api/maintenance", params={"limit": 2}, headers=AUTH).json()
    assert [e["done_on"] for e in view["log"]] == ["2026-10-03", "2026-10-02"]
    assert client.get("/api/maintenance?limit=0", headers=AUTH).status_code == 422


def test_a_due_job_shows_even_before_the_robot_has_ever_been_heard(
    bridge: RobotBridge, store: Store
):
    now = datetime.now(UTC)
    store.add_maintenance("filter", now.date() - timedelta(days=45), now)

    assert active(bridge) == {"filter_due"}

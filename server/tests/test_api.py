from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}


@pytest.fixture
def parts(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>dashboard</title>")
    config = Config("LR4C000001", "right-key", "broker", 1883, tmp_path / "h.db", web)
    store = Store(config.db_path)
    return config, store, RobotBridge(config, store)


@pytest.fixture
def client(parts):
    config, store, bridge = parts
    with TestClient(create_app(config, store, bridge, run_bridge=False)) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/status"),
        ("get", "/api/events"),
        ("get", "/api/visits"),
        ("post", "/api/actions/clean"),
        ("post", "/api/actions/reset"),
        ("patch", "/api/settings"),
    ],
)
def test_every_robot_endpoint_needs_the_key(client: TestClient, method: str, path: str):
    assert getattr(client, method)(path).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert getattr(client, method)(path, headers=wrong).status_code == 401


def test_health_and_dashboard_are_public(client: TestClient):
    assert client.get("/api/health").json() == {"ok": True}
    assert "dashboard" in client.get("/").text


def test_status_with_key(client: TestClient):
    body = client.get("/api/status", headers=AUTH).json()
    assert body["serial"] == "LR4C000001"
    assert body["online"] is False


def test_actions_report_offline_robot(client: TestClient):
    response = client.post("/api/actions/clean", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "The robot is offline."


@pytest.mark.parametrize(
    "bad",
    [
        {"night_light_brightness": 101},
        {"night_light_mode": "disco"},
        {"clean_cycle_wait_minutes": 4},
        {"sleep_minute": 1440},
        {"power": "off"},
    ],
)
def test_settings_rejects_bad_values(client: TestClient, bad: dict):
    assert client.patch("/api/settings", json=bad, headers=AUTH).status_code == 422


def test_there_is_no_route_to_power_or_wifi(client: TestClient):
    # A robot switched off or off wifi has left the network, and only a person can bring it back.
    assert client.post("/api/actions/power", headers=AUTH).status_code in {404, 405}
    assert client.post("/api/actions/wifi", headers=AUTH).status_code in {404, 405}


def test_visits_window_and_event_paging(client: TestClient, parts):
    _, store, _ = parts
    now = datetime.now(UTC)
    store.add_event(now - timedelta(days=40), "visit", {"weight_lb": 9.0})
    store.add_event(now - timedelta(days=1), "visit", {"weight_lb": 9.5})
    store.add_event(now, "clean_cycle", {"count": 2})

    visits = client.get("/api/visits?days=30", headers=AUTH).json()
    assert [v["detail"]["weight_lb"] for v in visits] == [9.5]

    newest = client.get("/api/events?limit=1", headers=AUTH).json()
    assert newest[0]["kind"] == "clean_cycle"
    older = client.get(
        "/api/events", params={"limit": 5, "before": newest[0]["at"]}, headers=AUTH
    ).json()
    assert [e["kind"] for e in older] == ["visit", "visit"]

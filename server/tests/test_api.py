from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiomqtt
import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.api import SettingsPatch, _setting_commands, create_app
from whisker_local.bridge import DrawerFull, PressNotAcknowledged, RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store, encode_cursor

AUTH = {"Authorization": "Bearer right-key"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/status"),
        ("get", "/api/events"),
        ("get", "/api/visits"),
        ("post", "/api/visits/1/label"),
        ("get", "/api/cats"),
        ("post", "/api/cats"),
        ("get", "/api/cats/suggestions"),
        ("get", "/api/cats/1"),
        ("patch", "/api/cats/1"),
        ("get", "/api/cats/1/series"),
        ("post", "/api/actions/clean"),
        ("post", "/api/actions/reset"),
        ("post", "/api/actions/empty"),
        ("post", "/api/actions/calibrate-litter"),
        ("delete", "/api/actions/calibrate-litter"),
        ("patch", "/api/settings"),
        ("get", "/api/maintenance"),
        ("post", "/api/maintenance"),
        ("delete", "/api/maintenance/1"),
        ("get", "/api/alert-settings"),
        ("patch", "/api/alert-settings"),
        ("delete", "/api/alert-settings"),
        ("get", "/api/insights"),
        ("get", "/api/export/visits.csv"),
        ("get", "/api/export/database"),
    ],
)
def test_every_robot_endpoint_needs_the_key(client: TestClient, method: str, path: str):
    assert getattr(client, method)(path).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert getattr(client, method)(path, headers=wrong).status_code == 401


def test_unauthenticated_oversized_body_is_refused_before_it_is_read(client: TestClient):
    response = client.post("/api/cats", content=b"x" * 200_000)
    assert response.status_code == 401


def test_authenticated_oversized_body_is_refused(client: TestClient):
    response = client.post("/api/cats", content=b"x" * 200_000, headers=AUTH)
    assert response.status_code == 413


def test_chunked_oversized_body_is_cut_off_without_a_length_header(client: TestClient):
    def chunks():
        for _ in range(100):
            yield b"x" * 4096

    response = client.post("/api/cats", content=chunks(), headers=AUTH)
    assert response.status_code == 413


def test_wrong_key_never_reaches_the_body():
    consumed = False

    async def app(scope, receive, send):
        pass

    async def receive():
        nonlocal consumed
        consumed = True
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    from whisker_local.api import GuardApi

    scope = {
        "type": "http",
        "path": "/api/cats",
        "headers": [(b"content-length", b"999999999")],
    }
    asyncio.run(GuardApi(app, b"right-key")(scope, receive, send))
    assert sent[0]["status"] == 401
    assert not consumed


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
    assert response.json()["detail"] == "Robot offline"


def test_empty_reports_offline_robot(client: TestClient):
    response = client.post("/api/actions/empty", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "Robot offline"


def test_empty_with_a_full_drawer_is_a_409(client: TestClient, parts, monkeypatch):
    _, _, bridge = parts

    async def full(*_, **__):
        raise DrawerFull

    monkeypatch.setattr(bridge, "press", full)
    response = client.post("/api/actions/empty", headers=AUTH)
    assert response.status_code == 409
    assert "drawer is full" in response.json()["detail"]


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
        "/api/events", params={"limit": 5, "before": newest[0]["cursor"]}, headers=AUTH
    ).json()
    assert [e["kind"] for e in older] == ["visit", "visit"]


def test_events_that_share_a_timestamp_are_all_reached_across_pages(client: TestClient, parts):
    _, store, _ = parts
    moment = datetime.now(UTC)
    for count in range(7):
        store.add_event(moment, "clean_cycle", {"count": count})
    store.add_event(moment - timedelta(seconds=1), "firmware", {"version": "1"})

    seen: list[dict] = []
    before = None
    while page := client.get(
        "/api/events", params={"limit": 3, **({"before": before} if before else {})}, headers=AUTH
    ).json():
        seen.extend(page)
        before = page[-1]["cursor"]

    assert [e["detail"].get("count") for e in seen] == [6, 5, 4, 3, 2, 1, 0, None]
    assert len({e["id"] for e in seen}) == 8
    assert len({e["cursor"] for e in seen}) == 8


@pytest.mark.parametrize("bad", ["2026-11-08T12:00:00.000+00:00", "!!!", "", "bm90LWEtY3Vyc29y"])
def test_events_reject_a_cursor_that_was_not_handed_out(client: TestClient, bad: str):
    response = client.get("/api/events", params={"before": bad}, headers=AUTH)
    assert response.status_code == 422


@pytest.mark.parametrize("event_id", [2**70, -(2**70), 2**63, -(2**63) - 1])
def test_events_reject_a_cursor_id_beyond_sqlite_integers(client: TestClient, event_id: int):
    forged = encode_cursor("2026-11-08T12:00:00.000+00:00", event_id)
    response = client.get("/api/events", params={"before": forged}, headers=AUTH)
    assert response.status_code == 422


def test_sleep_schedule_is_armed_after_its_times_are_written():
    patch = SettingsPatch(sleep_enabled=True, sleep_minute=1380, wake_minute=420)
    labels = [command.label for command in _setting_commands(patch)]
    assert labels[-1] == "setWeekdaySleepEnabled"
    assert labels.count("setWeekdaySleepEnabled") == 1
    assert len(labels) == 15


def test_sleep_schedule_is_disarmed_before_its_times_are_written():
    patch = SettingsPatch(sleep_enabled=False, sleep_minute=1380)
    labels = [command.label for command in _setting_commands(patch)]
    assert labels[0] == "setWeekdaySleepEnabled"
    assert len(labels) == 8


def test_sleep_times_alone_do_not_touch_the_mask():
    labels = [c.label for c in _setting_commands(SettingsPatch(sleep_minute=1380))]
    assert "setWeekdaySleepEnabled" not in labels


class _SettingsLink:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def apply_setting(self, command):
        return True


class _Link:
    def __init__(self, bridge: RobotBridge):
        self.bridge = bridge

    async def request_state(self):
        self.bridge.ingest(
            parse_message(
                "prod/LR4/LR4C000001/state",
                json.dumps({"robotStatus": 4, "nightLightBrightness": 35}),
            ),
            datetime.now(UTC),
        )


def test_patch_answers_with_the_values_it_just_wrote(
    client: TestClient, parts, monkeypatch: pytest.MonkeyPatch
):
    _, _, bridge = parts
    bridge.ingest(
        parse_message(
            "prod/LR4/LR4C000001/state", json.dumps({"robotStatus": 4, "nightLightBrightness": 50})
        ),
        datetime.now(UTC),
    )
    bridge._link = _Link(bridge)
    monkeypatch.setattr(bridge, "_open_link", lambda role: _SettingsLink())

    response = client.patch("/api/settings", json={"night_light_brightness": 35}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["robot"]["settings"]["night_light_brightness"] == 35


def test_broker_down_is_a_503_not_a_500(client: TestClient, parts, monkeypatch):
    _, _, bridge = parts
    bridge.ingest(
        parse_message("prod/LR4/LR4C000001/state", json.dumps({"robotStatus": 4})),
        datetime.now(UTC),
    )

    class Down:
        async def request_state(self):
            raise aiomqtt.MqttError("gone")

        async def publish(self, command):
            raise aiomqtt.MqttError("gone")

    bridge._link = Down()
    monkeypatch.setattr(
        bridge, "_open_link", lambda role: pytest.fail("a down broker should not get that far")
    )

    for method, path in (("post", "/api/actions/refresh"), ("post", "/api/actions/clean")):
        response = getattr(client, method)(path, headers=AUTH)
        assert response.status_code == 503
        assert "Broker" in response.json()["detail"]


def test_unacknowledged_press_is_a_504_with_a_reason(client: TestClient, parts, monkeypatch):
    _, _, bridge = parts

    async def unacknowledged(*_, **__):
        raise PressNotAcknowledged("cleanCycle")

    monkeypatch.setattr(bridge, "press", unacknowledged)
    response = client.post("/api/actions/clean", headers=AUTH)
    assert response.status_code == 504
    assert "did not acknowledge" in response.json()["detail"]


def test_health_fails_once_the_bridge_task_has_died(parts):
    config, store, bridge = parts

    async def dies():
        raise RuntimeError("boom")

    bridge.run = dies
    with TestClient(create_app(config, store, bridge)) as test_client:
        for _ in range(50):
            response = test_client.get("/api/health")
            if response.status_code == 503:
                break
            time.sleep(0.02)
    assert response.status_code == 503
    assert response.json() == {"ok": False}


def test_health_is_ok_while_the_bridge_task_runs(parts):
    config, store, bridge = parts

    async def runs_forever():
        await asyncio.Event().wait()

    bridge.run = runs_forever
    with TestClient(create_app(config, store, bridge)) as test_client:
        assert test_client.get("/api/health").json() == {"ok": True}


def add_visit(store: Store, weight: float | None, ago: timedelta = timedelta(hours=1)) -> int:
    return store.add_event(
        datetime.now(UTC) - ago, "visit", {"duration_s": 40, "weight_lb": weight}
    )


def make_cat(client: TestClient, name: str, weight: float | None = None) -> dict:
    body = {"name": name, **({} if weight is None else {"estimated_weight_lb": weight})}
    response = client.post("/api/cats", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()


def test_status_has_no_cats_and_no_waiting_visits_on_a_fresh_install(client: TestClient):
    body = client.get("/api/status", headers=AUTH).json()
    assert body["cats"] == []
    assert body["visits_needing_cat"] == 0


def test_status_lists_each_cats_summary_and_the_visits_waiting_for_a_cat(client: TestClient, parts):
    _, store, _ = parts
    miso = make_cat(client, "Miso", 10.0)
    pip = make_cat(client, "Pip", 11.0)
    add_visit(store, 10.0)
    add_visit(store, 10.5, ago=timedelta(hours=2))

    body = client.get("/api/status", headers=AUTH).json()

    assert [c["name"] for c in body["cats"]] == ["Miso", "Pip"]
    assert [c["color_index"] for c in body["cats"]] == [0, 1]
    assert body["visits_needing_cat"] == 2
    assert body["cats"][0]["id"] == miso["id"]
    assert body["cats"][1]["id"] == pip["id"]
    assert set(body["cats"][0]["summary"]) == {
        "latest_weight_lb",
        "usual_weight_lb",
        "anchor_weight_lb",
        "recent_weight_lb",
        "recent_readings",
        "visits_today",
        "visits_per_day",
        "avg_visit_s",
        "weight_change_pct",
        "last_visit_at",
        "yesterday_weight_lb",
        "yesterday_visits",
        "yesterday_minutes",
        "yesterday_coverage",
        "week_visits",
        "week_days_with_data",
        "common_visit_hour",
    }


def test_new_visits_are_auto_labelled_only_when_the_cat_is_clear(client: TestClient, parts):
    _, store, _ = parts
    miso = make_cat(client, "Miso", 10.0)
    make_cat(client, "Pip", 11.5)
    add_visit(store, 10.1)
    add_visit(store, 10.8, ago=timedelta(hours=2))
    client.post("/api/cats", json={"name": "Third"}, headers=AUTH)

    waiting = client.get("/api/visits?needs_cat=true", headers=AUTH).json()
    assert [v["detail"]["weight_lb"] for v in waiting] == [10.8]
    assert waiting[0]["identity"]["status"] == "unsure"
    assert waiting[0]["label"] == "unknown"
    assert waiting[0]["cat_id"] is None
    assert waiting[0]["label_source"] == "auto"

    mine = client.get(f"/api/visits?cat_id={miso['id']}", headers=AUTH).json()
    assert [v["detail"]["weight_lb"] for v in mine] == [10.1]
    assert (mine[0]["label"], mine[0]["cat_id"], mine[0]["label_source"]) == (
        "cat",
        miso["id"],
        "auto",
    )


def test_visits_cannot_filter_by_cat_and_by_needing_one(client: TestClient):
    response = client.get("/api/visits?cat_id=1&needs_cat=true", headers=AUTH)
    assert response.status_code == 422


def test_labelling_a_visit_over_the_api(client: TestClient, parts):
    _, store, _ = parts
    miso = make_cat(client, "Miso", 10.0)
    make_cat(client, "Pip", 11.0)
    between = add_visit(store, 10.5)

    labelled = client.post(
        f"/api/visits/{between}/label", json={"label": "cat", "cat_id": miso["id"]}, headers=AUTH
    )

    assert labelled.status_code == 200
    assert labelled.json()["label"] == "cat"
    assert labelled.json()["label_source"] == "user"
    assert client.get("/api/status", headers=AUTH).json()["visits_needing_cat"] == 0

    stranger = client.post(f"/api/visits/{between}/label", json={"label": "not_cat"}, headers=AUTH)
    assert (stranger.json()["label"], stranger.json()["cat_id"]) == ("not_cat", None)

    reset = client.post(f"/api/visits/{between}/label", json={"label": "unknown"}, headers=AUTH)
    assert (reset.json()["label"], reset.json()["label_source"]) == ("unknown", "auto")


@pytest.mark.parametrize(
    "body",
    [
        {"label": "cat"},
        {"label": "not_cat", "cat_id": 1},
        {"label": "unknown", "cat_id": 1},
        {"label": "dog"},
        {},
    ],
)
def test_label_requests_must_make_sense(client: TestClient, parts, body: dict):
    _, store, _ = parts
    make_cat(client, "Miso", 10.0)
    event = add_visit(store, 10.0)
    assert client.post(f"/api/visits/{event}/label", json=body, headers=AUTH).status_code == 422


def test_labelling_something_that_does_not_exist_or_cannot_hold_it(client: TestClient, parts):
    _, store, _ = parts
    gone = make_cat(client, "Gone")
    event = add_visit(store, 10.0)
    cycle = store.add_event(datetime.now(UTC), "clean_cycle", {})

    assert (
        client.post("/api/visits/9999/label", json={"label": "not_cat"}, headers=AUTH).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/visits/{cycle}/label", json={"label": "not_cat"}, headers=AUTH
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/visits/{event}/label", json={"label": "cat", "cat_id": 99}, headers=AUTH
        ).status_code
        == 404
    )

    client.patch(f"/api/cats/{gone['id']}", json={"archived": True}, headers=AUTH)
    archived = client.post(
        f"/api/visits/{event}/label", json={"label": "cat", "cat_id": gone["id"]}, headers=AUTH
    )
    assert archived.status_code == 409


def test_cat_lifecycle_over_the_api(client: TestClient):
    miso = make_cat(client, "  Miso ", 10.0)
    assert miso["name"] == "Miso"
    assert miso["estimated_weight_lb"] == 10.0
    assert miso["archived"] is False

    renamed = client.patch(f"/api/cats/{miso['id']}", json={"name": "Misu"}, headers=AUTH).json()
    assert renamed["name"] == "Misu"
    assert renamed["color_index"] == miso["color_index"]

    client.patch(f"/api/cats/{miso['id']}", json={"archived": True}, headers=AUTH)
    assert client.get("/api/cats", headers=AUTH).json() == []
    everyone = client.get("/api/cats?include_archived=true", headers=AUTH).json()
    assert [(c["name"], c["archived"]) for c in everyone] == [("Misu", True)]
    assert client.get(f"/api/cats/{miso['id']}", headers=AUTH).json()["archived"] is True

    client.patch(f"/api/cats/{miso['id']}", json={"archived": False}, headers=AUTH)
    assert [c["name"] for c in client.get("/api/cats", headers=AUTH).json()] == ["Misu"]


def test_cat_requests_that_cannot_work(client: TestClient):
    make_cat(client, "Miso")
    other = make_cat(client, "Pip")
    post = client.post
    assert post("/api/cats", json={"name": "miso"}, headers=AUTH).status_code == 409
    assert post("/api/cats", json={"name": "  "}, headers=AUTH).status_code == 422
    assert post("/api/cats", json={"name": "x" * 41}, headers=AUTH).status_code == 422
    assert (
        post("/api/cats", json={"name": "Z", "estimated_weight_lb": 1}, headers=AUTH).status_code
        == 422
    )
    assert (
        post("/api/cats", json={"name": "Z", "estimated_weight_lb": 41}, headers=AUTH).status_code
        == 422
    )
    assert post("/api/cats", json={"name": "Z", "colour": 3}, headers=AUTH).status_code == 422
    assert (
        client.patch(f"/api/cats/{other['id']}", json={"name": "MISO"}, headers=AUTH).status_code
        == 409
    )
    assert client.patch(f"/api/cats/{other['id']}", json={}, headers=AUTH).status_code == 422
    assert client.get("/api/cats/99", headers=AUTH).status_code == 404
    assert client.patch("/api/cats/99", json={"name": "x"}, headers=AUTH).status_code == 404
    assert client.get("/api/cats/99/series", headers=AUTH).status_code == 404


def test_suggestions_endpoint_says_when_there_is_not_enough_data(client: TestClient, parts):
    _, store, _ = parts
    empty = client.get("/api/cats/suggestions", headers=AUTH).json()
    assert empty["enough_data"] is False
    assert empty["clusters"] == []

    for hour in range(20):
        add_visit(store, 9.0 + (hour % 2) * 0.1, ago=timedelta(days=hour // 4, hours=hour))
        add_visit(
            store, 13.0 + (hour % 2) * 0.1, ago=timedelta(days=hour // 4, hours=hour, minutes=5)
        )
    full = client.get("/api/cats/suggestions", headers=AUTH).json()

    assert full["enough_data"] is True
    assert [c["visits"] for c in full["clusters"]] == [20, 20]
    assert set(full["clusters"][0]) == {"center_lb", "low_lb", "high_lb", "visits"}

    make_cat(client, "Small", full["clusters"][0]["center_lb"])
    after = client.get("/api/cats/suggestions", headers=AUTH).json()
    assert [c["visits"] for c in after["clusters"]] == [20]


def test_series_over_the_api(client: TestClient, parts):
    _, store, _ = parts
    miso = make_cat(client, "Miso", 10.0)
    for ago in (timedelta(days=1), timedelta(days=1, hours=1)):
        event = add_visit(store, 10.0, ago=ago)
        client.post(
            f"/api/visits/{event}/label", json={"label": "cat", "cat_id": miso["id"]}, headers=AUTH
        )

    default = client.get(f"/api/cats/{miso['id']}/series", headers=AUTH).json()
    assert len(default["days"]) == 30
    assert default["timezone"] == "UTC"
    assert default["end"] == default["days"][-1]["date"]
    assert sum(d["visits"] or 0 for d in default["days"]) == 2

    today = datetime.now(UTC).date()
    window = client.get(
        f"/api/cats/{miso['id']}/series",
        params={"start": (today - timedelta(days=2)).isoformat(), "end": today.isoformat()},
        headers=AUTH,
    ).json()
    assert [d["date"] for d in window["days"]] == [
        (today - timedelta(days=n)).isoformat() for n in (2, 1, 0)
    ]


def test_series_range_is_checked(client: TestClient):
    miso = make_cat(client, "Miso")
    url = f"/api/cats/{miso['id']}/series"
    assert (
        client.get(
            url, params={"start": "2026-02-01", "end": "2026-01-01"}, headers=AUTH
        ).status_code
        == 422
    )
    assert (
        client.get(
            url, params={"start": "2020-01-01", "end": "2026-01-01"}, headers=AUTH
        ).status_code
        == 422
    )
    assert (
        client.get(
            url, params={"start": "2025-01-01", "end": "2025-12-31"}, headers=AUTH
        ).status_code
        == 200
    )
    assert client.get(url, params={"start": "soon"}, headers=AUTH).status_code == 422


def test_days_follow_the_configured_timezone(tmp_path: Path):
    from zoneinfo import ZoneInfo

    web = tmp_path / "web"
    config = Config(
        "LR4C000001",
        "right-key",
        "broker",
        1883,
        tmp_path / "t.db",
        web,
        ZoneInfo("Pacific/Auckland"),
    )
    store = Store(config.db_path)
    store.add_presence(datetime(2026, 5, 1, tzinfo=UTC), True)
    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)) as c:
        cat = make_cat(c, "Miso", 10.0)
        # 23:00 UTC on 1 Jun is 11:00 on 2 Jun in Auckland.
        event = store.add_event(
            datetime(2026, 6, 1, 23, 0, tzinfo=UTC), "visit", {"weight_lb": 10.0}
        )
        c.post(
            f"/api/visits/{event}/label", json={"label": "cat", "cat_id": cat["id"]}, headers=AUTH
        )

        body = c.get(
            f"/api/cats/{cat['id']}/series",
            params={"start": "2026-06-01", "end": "2026-06-02"},
            headers=AUTH,
        ).json()

    assert body["timezone"] == "Pacific/Auckland"
    assert [d["visits"] for d in body["days"]] == [0, 1]


def test_config_reads_the_timezone_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", "k")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert str(Config.from_env().timezone) == "Europe/Berlin"
    monkeypatch.delenv("TZ")
    assert str(Config.from_env().timezone) == "UTC"
    monkeypatch.setenv("TZ", "")
    assert str(Config.from_env().timezone) == "UTC"


def test_status_offers_the_visit_waiting_for_a_cat_with_its_closest_match(
    client: TestClient, parts
):
    _, store, _ = parts
    miso = make_cat(client, "Miso", 10.0)
    make_cat(client, "Pip", 14.0)
    event_id = add_visit(store, 11.4)

    body = client.get("/api/status", headers=AUTH).json()

    waiting = body["latest_visit_needing_cat"]
    assert (waiting["id"], waiting["weight_lb"], waiting["duration_s"]) == (event_id, 11.4, 40)
    assert waiting["closest_cat_id"] == miso["id"]


def test_status_has_no_waiting_visit_when_there_is_none(client: TestClient):
    assert client.get("/api/status", headers=AUTH).json()["latest_visit_needing_cat"] is None


def test_the_schema_is_not_served_even_with_the_key(client: TestClient):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
        assert client.get(path, headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/cats/99999999999999999999999"),
        ("patch", "/api/cats/99999999999999999999999"),
        ("get", "/api/cats/99999999999999999999999/photo"),
        ("delete", "/api/cats/99999999999999999999999/photo"),
        ("get", "/api/cats/99999999999999999999999/insights"),
        ("get", "/api/cats/99999999999999999999999/series"),
        ("delete", "/api/maintenance/99999999999999999999999"),
        ("get", "/api/cats/9223372036854775808"),
        ("get", "/api/visits?cat_id=99999999999999999999999"),
        ("post", "/api/visits/99999999999999999999999/label"),
    ],
)
def test_an_id_too_big_for_the_database_is_a_client_error(
    client: TestClient, method: str, path: str
):
    kwargs = {"json": {"label": "not_cat"}} if method == "post" else {}
    response = getattr(client, method)(path, headers=AUTH, **kwargs)
    assert response.status_code in (404, 422)


def test_a_huge_cat_id_in_a_label_is_a_client_error(client: TestClient):
    body = {"label": "cat", "cat_id": 2**70}
    assert client.post("/api/visits/1/label", json=body, headers=AUTH).status_code == 422


def test_every_response_carries_the_browser_hardening_headers(client: TestClient):
    for response in (
        client.get("/"),
        client.get("/api/health"),
        client.get("/api/status"),
        client.get("/api/status", headers=AUTH),
        client.get("/missing"),
    ):
        headers = response.headers
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        policy = headers["content-security-policy"]
        assert "frame-ancestors 'none'" in policy
        assert "'wasm-unsafe-eval'" in policy
        assert "worker-src 'self' blob:" in policy
        assert "unsafe-eval'" not in policy.replace("'wasm-unsafe-eval'", "")


def test_stored_text_that_cannot_be_utf8_does_not_break_the_events_endpoint(
    client: TestClient, parts
):
    _, store, _ = parts
    store._db.execute(
        "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
        ("2026-01-01T00:00:00.000+00:00", "firmware", json.dumps({"version": "\ud800"})),
    )
    response = client.get("/api/events", headers=AUTH)
    assert response.status_code == 200
    assert response.json()[0]["kind"] == "firmware"


def test_a_validation_error_that_echoes_a_lone_surrogate_is_still_a_422(client: TestClient):
    body = b'{"name": "\\ud800", "sex": "\\ud800"}'
    response = client.post(
        "/api/cats", content=body, headers={**AUTH, "content-type": "application/json"}
    )
    assert response.status_code == 422


def test_guard_rejects_everything_when_built_with_an_empty_key(parts):
    config, store, bridge = parts
    app = create_app(dataclasses.replace(config, access_key=""), store, bridge, run_bridge=False)
    with TestClient(app) as open_client:
        assert open_client.get("/api/status").status_code == 401
        blank = {"Authorization": "Bearer "}
        assert open_client.get("/api/status", headers=blank).status_code == 401


def test_nan_in_a_body_is_rejected_cleanly(client: TestClient):
    response = client.post(
        "/api/cats",
        headers={**AUTH, "content-type": "application/json"},
        content=b'{"name": NaN}',
    )
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


def test_undecodable_body_is_rejected_cleanly(client: TestClient):
    response = client.post(
        "/api/cats", headers={**AUTH, "content-type": "text/plain"}, content=b"\xff\xfe"
    )
    assert 400 <= response.status_code < 500


def test_date_at_the_edge_of_the_calendar_is_rejected_cleanly(client: TestClient):
    response = client.get(
        "/api/insights", params={"start": "9999-12-31", "end": "9999-12-31"}, headers=AUTH
    )
    assert response.status_code < 500
    assert "x-content-type-options" in response.headers


NO_CACHE = "no-cache, no-transform"


def test_web_root_responses_are_always_revalidated_and_never_rewritten(parts):
    config, store, bridge = parts
    (config.web_root / "assets").mkdir()
    (config.web_root / "assets" / "main.dart.js").write_text("void main() {}")
    with TestClient(create_app(config, store, bridge, run_bridge=False)) as client:
        index = client.get("/")
        asset = client.get("/assets/main.dart.js")
        repeat = client.get(
            "/assets/main.dart.js", headers={"If-None-Match": asset.headers["etag"]}
        )
        assert index.headers["cache-control"] == NO_CACHE
        assert asset.headers["cache-control"] == NO_CACHE
        assert repeat.status_code == 304
        assert repeat.headers["cache-control"] == NO_CACHE


def test_web_root_errors_are_revalidated_too(parts):
    config, store, bridge = parts
    with TestClient(create_app(config, store, bridge, run_bridge=False)) as client:
        missing = client.get("/nope.js")
        wrong_method = client.post("/")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == NO_CACHE
    assert wrong_method.status_code == 405
    assert wrong_method.headers["cache-control"] == NO_CACHE


def test_api_routes_do_not_get_the_web_root_cache_header(client: TestClient):
    assert "cache-control" not in client.get("/api/health").headers


def test_status_has_no_last_clean_when_none_is_recorded(client: TestClient):
    assert client.get("/api/status", headers=AUTH).json()["last_clean_at"] is None


def test_status_reports_the_newest_clean_cycle(client: TestClient, parts):
    _, store, _ = parts
    now = datetime.now(UTC)
    store.add_event(now - timedelta(hours=5), "clean_cycle", {})
    store.add_event(now - timedelta(hours=1), "clean_cycle", {})
    store.add_event(now, "visit", {})
    body = client.get("/api/status", headers=AUTH).json()
    assert body["last_clean_at"] == (now - timedelta(hours=1)).isoformat(timespec="milliseconds")

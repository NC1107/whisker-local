from __future__ import annotations

import asyncio
import http.server
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local import push
from whisker_local.api import create_app, create_app_from_env
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.push import InvalidRelayUrl, PushNotifier
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TOKEN = "fcm:token-1"
RELAY = "https://relay.example.com/"


def state(**fields):
    body = json.dumps({"robotStatus": 4, "DFILevelPercent": 20, **fields})
    return parse_message("prod/LR4/LR4C000001/state", body)


class FakeRelay:
    """Stands in for the relay's HTTP API and records every request it gets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.send_status = 200
        self.statuses: dict[str, str] = {}
        self.fail: Exception | None = None

    def __call__(self, url: str, payload: dict[str, Any], key: str | None):
        self.calls.append((url, payload, key))
        if self.fail is not None:
            raise self.fail
        if url.endswith("/v1/register"):
            return 200, {"key": "llr_issued"}
        results = [
            {"token": m["token"], "status": self.statuses.get(m["token"], "delivered")}
            for m in payload["messages"]
        ]
        return self.send_status, {"results": results}

    def sends(self) -> list[dict[str, Any]]:
        return [p for url, p, _ in self.calls if url.endswith("/v1/send")]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    web = tmp_path / "web"
    web.mkdir()
    return Config("LR4C000001", "right-key", "broker", 1883, tmp_path / "h.db", web)


@pytest.fixture
def store(config: Config) -> Store:
    return Store(config.db_path)


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
def notifier(store: Store, relay: FakeRelay) -> PushNotifier:
    return PushNotifier(RELAY, store, post=relay)


@pytest.fixture
def client(config: Config, store: Store, notifier: PushNotifier):
    bridge = RobotBridge(config, store, notifier=notifier)
    app = create_app(config, store, bridge, run_bridge=False, notifier=notifier)
    with TestClient(app) as test_client:
        yield test_client


def raise_drawer_full(bridge: RobotBridge) -> None:
    bridge.ingest(state(), START)
    bridge.ingest(state(isDFIFull=1, DFILevelPercent=100), START + timedelta(minutes=1))


# Inert when unconfigured


def test_config_has_no_relay_by_default(monkeypatch):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", "right-key")
    monkeypatch.delenv("PUSH_RELAY_URL", raising=False)
    assert Config.from_env().push_relay_url == ""


def test_nothing_is_built_without_a_relay_url(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", "right-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("WEB_ROOT", str(tmp_path))
    monkeypatch.delenv("PUSH_RELAY_URL", raising=False)

    def forbidden(*_: Any, **__: Any):
        raise AssertionError("the network was used with no relay configured")

    monkeypatch.setattr(push, "post_json", forbidden)
    monkeypatch.setattr(push.urllib.request, "urlopen", forbidden)
    with TestClient(create_app_from_env()) as inert:
        status = inert.get("/api/status", headers=AUTH).json()
        assert status["push_configured"] is False
        assert inert.get("/api/push", headers=AUTH).json()["configured"] is False
        body = {"token": TOKEN, "platform": "ios"}
        assert inert.put("/api/push/devices", headers=AUTH, json=body).status_code == 404
        assert inert.post("/api/push/test", headers=AUTH).status_code == 404


def test_an_unconfigured_bridge_raises_alerts_without_a_notifier(config: Config, store: Store):
    bridge = RobotBridge(config, store)
    raise_drawer_full(bridge)
    assert any(e["detail"]["key"] == "drawer_full" for e in store.events(limit=50, kinds=["alert"]))


def test_a_malformed_relay_url_is_refused_at_startup(store: Store):
    with pytest.raises(InvalidRelayUrl):
        PushNotifier("ftp://relay.example.com", store)


# Registration needs the access key


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/push", None),
        ("put", "/api/push/devices", {"token": TOKEN, "platform": "ios"}),
        ("delete", "/api/push/devices", {"token": TOKEN}),
        ("patch", "/api/push/kinds", {"kinds": {"drawer_full": False}}),
        ("post", "/api/push/test", None),
    ],
)
def test_push_routes_need_the_access_key(client: TestClient, store: Store, method, path, body):
    wrong = {"Authorization": "Bearer wrong-key"}
    for headers in ({}, wrong):
        response = client.request(method, path, headers=headers, json=body)
        assert response.status_code == 401
    assert push.stored_devices(store) == []


def test_a_device_registers_and_unregisters(client: TestClient, store: Store):
    body = {"token": TOKEN, "platform": "android"}
    assert client.put("/api/push/devices", headers=AUTH, json=body).status_code == 204
    assert client.put("/api/push/devices", headers=AUTH, json=body).status_code == 204
    assert client.get("/api/push", headers=AUTH).json()["devices"] == 1

    assert client.request("DELETE", "/api/push/devices", headers=AUTH, json={"token": TOKEN})
    assert push.stored_devices(store) == []


@pytest.mark.parametrize(
    "body",
    [
        {"token": "", "platform": "ios"},
        {"token": "has space", "platform": "ios"},
        {"token": "x" * 1025, "platform": "ios"},
        {"token": TOKEN, "platform": "web"},
        {"token": TOKEN},
    ],
)
def test_a_bad_device_is_refused(client: TestClient, body):
    assert client.put("/api/push/devices", headers=AUTH, json=body).status_code == 422


def test_only_a_few_devices_are_kept(store: Store):
    for n in range(push.MAX_DEVICES + 3):
        push.add_device(store, f"t{n}", "ios")
    tokens = [d["token"] for d in push.stored_devices(store)]
    assert len(tokens) == push.MAX_DEVICES
    assert tokens[-1] == f"t{push.MAX_DEVICES + 2}"


def test_status_says_whether_push_is_configured(client: TestClient):
    assert client.get("/api/status", headers=AUTH).json()["push_configured"] is True


# Per-kind settings


def test_kinds_default_and_can_be_changed(client: TestClient):
    view = client.get("/api/push", headers=AUTH).json()
    assert view["kinds"]["drawer_full"] is True
    assert view["kinds"]["visit"] is False

    response = client.patch(
        "/api/push/kinds", headers=AUTH, json={"kinds": {"drawer_full": False, "visit": True}}
    )
    assert response.json()["kinds"]["drawer_full"] is False
    assert response.json()["kinds"]["visit"] is True
    assert (
        client.patch("/api/push/kinds", headers=AUTH, json={"kinds": {"nope": True}}).status_code
        == 422
    )
    assert client.patch("/api/push/kinds", headers=AUTH, json={"kinds": {}}).status_code == 422


# Delivery


def test_a_raised_alert_reaches_the_relay_once_registered(
    config: Config, store: Store, notifier: PushNotifier, relay: FakeRelay
):
    push.add_device(store, TOKEN, "ios")
    bridge = RobotBridge(config, store, notifier=notifier)

    async def scenario() -> None:
        task = asyncio.create_task(notifier.run())
        raise_drawer_full(bridge)
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(scenario())

    messages = [m for sent in relay.sends() for m in sent["messages"]]
    message = next(m for m in messages if m["collapseId"] == "drawer")
    assert message["token"] == TOKEN
    assert message["platform"] == "ios"
    assert message["title"] == "Waste drawer is full"
    assert message["body"] == "Empty the drawer and press Reset."
    registrations = [key for url, _, key in relay.calls if url.endswith("/v1/register")]
    assert registrations == [None]
    assert {key for url, _, key in relay.calls if url.endswith("/v1/send")} == {"llr_issued"}
    assert store.get_json(push.RELAY_KEY_KEY) == "llr_issued"


def test_a_cleared_alert_sends_nothing(
    config: Config, store: Store, notifier: PushNotifier, relay: FakeRelay
):
    push.add_device(store, TOKEN, "ios")
    bridge = RobotBridge(config, store, notifier=notifier)
    raise_drawer_full(bridge)
    notifier._pending.clear()
    bridge.ingest(state(isDFIFull=0, DFILevelPercent=5), START + timedelta(minutes=9))
    assert not notifier._pending


def test_a_switched_off_kind_is_not_queued(store: Store, notifier: PushNotifier):
    push.add_device(store, TOKEN, "ios")
    push.update_kinds(store, {"drawer_full": False})
    notifier.alert("drawer_full", "Waste drawer full.", False)
    assert not notifier._pending


def test_nothing_is_queued_when_no_phone_registered(notifier: PushNotifier):
    notifier.alert("drawer_full", "Waste drawer full.", False)
    assert not notifier._pending


def test_a_dead_token_is_forgotten(store: Store, notifier: PushNotifier, relay: FakeRelay):
    push.add_device(store, "dead", "ios")
    push.add_device(store, "live", "android")
    relay.statuses["dead"] = "unregistered"
    delivered = asyncio.run(notifier.send_test())
    assert delivered == 1
    assert [d["token"] for d in push.stored_devices(store)] == ["live"]


def test_a_refused_key_is_dropped_so_the_server_registers_again(
    store: Store, notifier: PushNotifier, relay: FakeRelay
):
    push.add_device(store, TOKEN, "ios")
    store.put_json(push.RELAY_KEY_KEY, "llr_revoked")
    relay.send_status = 401
    with pytest.raises(push.RelayError):
        asyncio.run(notifier.send_test())
    assert store.get_json(push.RELAY_KEY_KEY) is None


# A relay outage never reaches the robot


def test_a_failing_relay_does_not_disturb_the_bridge(
    config: Config, store: Store, relay: FakeRelay
):
    clock = [0.0]
    notifier = PushNotifier(RELAY, store, post=relay, clock=lambda: clock[0])
    push.add_device(store, TOKEN, "ios")
    relay.fail = OSError("relay is down")
    bridge = RobotBridge(config, store, notifier=notifier)

    async def scenario() -> None:
        task = asyncio.create_task(notifier.run())
        raise_drawer_full(bridge)
        await asyncio.sleep(0.2)
        assert not task.done()
        # The loop is still alive and still delivers once the relay is back.
        relay.fail = None
        clock[0] += push.REGISTER_RETRY_SECONDS + 1
        notifier.alert("filter_due", "Replace the filter.", False)
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(scenario())

    keys = {e["detail"]["key"] for e in store.events(limit=50, kinds=["alert"])}
    assert "drawer_full" in keys
    assert bridge.latest is not None
    assert relay.sends()


def test_a_hung_relay_is_cut_off(store: Store, relay: FakeRelay, monkeypatch):
    push.add_device(store, TOKEN, "ios")
    monkeypatch.setattr(push, "CALL_DEADLINE_SECONDS", 0.2)

    def slow(url: str, payload: dict[str, Any], key: str | None):
        time.sleep(1.5)
        return 200, {"key": "k", "results": []}

    notifier = PushNotifier(RELAY, store, post=slow)

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await notifier.send_test()

    asyncio.run(scenario())


def test_the_queue_is_bounded(store: Store, notifier: PushNotifier):
    push.add_device(store, TOKEN, "ios")
    push.update_kinds(store, {"visit": True})
    for n in range(1000):
        notifier.notify(push.Push("visit", f"message {n}", False, "A cat used the box", n + 1))
    assert len(notifier._pending) == push.MAX_PENDING
    assert notifier._pending[-1].message == "message 999"


def test_registration_is_not_retried_in_a_tight_loop(store: Store):
    push.add_device(store, TOKEN, "ios")
    clock = [0.0]
    calls = []

    def down(url: str, payload: dict[str, Any], key: str | None):
        calls.append(url)
        raise OSError("down")

    notifier = PushNotifier(RELAY, store, post=down, clock=lambda: clock[0])

    async def attempt() -> None:
        with pytest.raises((OSError, push.RelayError)):
            await notifier.send_test()

    asyncio.run(attempt())
    asyncio.run(attempt())
    assert len(calls) == 1
    clock[0] += push.REGISTER_RETRY_SECONDS + 1
    asyncio.run(attempt())
    assert len(calls) == 2


def test_a_test_push_with_no_phone_is_a_conflict(client: TestClient):
    assert client.post("/api/push/test", headers=AUTH).status_code == 409


def test_a_test_push_reports_a_relay_that_is_down(
    client: TestClient, store: Store, relay: FakeRelay
):
    store.put_json(push.RELAY_KEY_KEY, "llr_issued")
    client.put("/api/push/devices", headers=AUTH, json={"token": TOKEN, "platform": "ios"})
    relay.fail = OSError("down")
    assert client.post("/api/push/test", headers=AUTH).status_code == 502
    relay.fail = None
    assert client.post("/api/push/test", headers=AUTH).json() == {"delivered": 1}


# Transport and URL hardening


@pytest.mark.parametrize(
    "url",
    ["http://relay.example.com", "http://192.168.1.5:8090", "ftp://relay.example.com", "relay"],
)
def test_a_relay_must_be_https_unless_it_is_on_this_machine(store: Store, url: str):
    with pytest.raises(InvalidRelayUrl):
        PushNotifier(url, store)


@pytest.mark.parametrize(
    "url",
    ["https://relay.example.com", "http://127.0.0.1:8090", "http://localhost", "http://[::1]:1"],
)
def test_https_and_loopback_urls_are_accepted(store: Store, url: str):
    assert PushNotifier(url, store)


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []  # noqa: RUF012 - shared with the test on purpose
    mode = "ok"

    def log_message(self, *_: Any) -> None:
        pass

    def do_POST(self) -> None:
        type(self).hits.append(self.path)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/landing")
            self.end_headers()
        elif self.path == "/drip":
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass
        else:
            body = b'{"key": "llr_x"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture
def local_relay():
    _Handler.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _Handler
    server.shutdown()
    server.server_close()


def test_the_transport_posts_and_decodes(local_relay):
    base, _ = local_relay
    assert push.post_json(f"{base}/v1/register", {}, None) == (200, {"key": "llr_x"})


def test_a_redirect_is_not_followed(local_relay):
    base, handler = local_relay
    status, _ = push.post_json(f"{base}/redirect", {"a": 1}, "llr_secret")
    assert status == 302
    assert handler.hits == ["/redirect"]


def test_a_relay_that_drips_bytes_hits_the_total_deadline(local_relay, monkeypatch):
    base, _ = local_relay
    monkeypatch.setattr(push, "TOTAL_DEADLINE_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        push.post_json(f"{base}/drip", {}, None)
    assert time.monotonic() - started < 3


def test_sends_run_on_the_notifiers_own_threads(store: Store):
    push.add_device(store, TOKEN, "ios")
    names: list[str] = []

    def post(url: str, payload: dict[str, Any], key: str | None):
        names.append(threading.current_thread().name)
        if url.endswith("/v1/register"):
            return 200, {"key": "llr_issued"}
        return 200, {"results": []}

    asyncio.run(PushNotifier(RELAY, store, post=post).send_test())
    assert names
    assert all(name.startswith("push") for name in names)


def test_the_relay_is_not_told_the_servers_address(store: Store, relay: FakeRelay):
    push.add_device(store, TOKEN, "ios")
    asyncio.run(PushNotifier(RELAY, store, post=relay).send_test())
    [(_, payload, _)] = [c for c in relay.calls if c[0].endswith("/v1/register")]
    assert payload == {}


def test_a_forbidden_result_does_not_delete_the_device(
    store: Store, notifier: PushNotifier, relay: FakeRelay
):
    push.add_device(store, "claimed", "ios")
    relay.statuses["claimed"] = "forbidden"
    asyncio.run(notifier.send_test())
    assert [d["token"] for d in push.stored_devices(store)] == ["claimed"]


@pytest.mark.parametrize(
    "garbage",
    [
        ValueError("not json"),
        KeyError("results"),
        RuntimeError("anything"),
    ],
)
def test_a_test_push_survives_any_exception_from_the_relay(
    client: TestClient, store: Store, relay: FakeRelay, garbage: Exception
):
    store.put_json(push.RELAY_KEY_KEY, "llr_issued")
    client.put("/api/push/devices", headers=AUTH, json={"token": TOKEN, "platform": "ios"})
    relay.fail = garbage
    assert client.post("/api/push/test", headers=AUTH).status_code == 502


def test_a_test_push_survives_a_relay_answering_with_nonsense(
    client: TestClient, store: Store, notifier: PushNotifier
):
    store.put_json(push.RELAY_KEY_KEY, "llr_issued")
    client.put("/api/push/devices", headers=AUTH, json={"token": TOKEN, "platform": "ios"})
    notifier._post = lambda *_: (200, {"results": 5})
    response = client.post("/api/push/test", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"delivered": 0}

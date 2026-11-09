from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiomqtt
import pytest
from whiskerless.devices.litter_robot_4 import commands, const, parse_message
from whiskerless.exceptions import WhiskerlessConnectionError

from whisker_local import bridge as bridge_module
from whisker_local.bridge import (
    EVENT_BURST,
    BrokerUnavailable,
    DrawerFull,
    PressNotAcknowledged,
    RobotBridge,
    RobotLocked,
    RobotOffline,
    SettingNotConfirmed,
    refuse_when_drawer_full,
)
from whisker_local.config import Config
from whisker_local.store import Store

SERIAL = "LR4C000001"
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
READY = {
    "robotStatus": 4,
    "catDetect": 0,
    "DFILevelPercent": 35,
    "litterLevelPercentage": 62,
    "litterLevel": 455,
    "odometerCleanCycles": 100,
    "isKeypadLockout": 0,
    "espFirmware": "1.1.75",
}


def state(**fields):
    body = json.dumps({**READY, **fields})
    return parse_message(f"prod/LR4/{SERIAL}/state", body)


def activity(*codes):
    return parse_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": list(codes)}))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    return RobotBridge(config, store)


def walk_a_visit(bridge: RobotBridge) -> datetime:
    bridge.ingest(state(), START)
    bridge.ingest(state(robotStatus=25, catDetect=3), START + timedelta(seconds=10))
    bridge.ingest(activity("0x0903AE"), START + timedelta(seconds=30))
    ended = START + timedelta(seconds=45)
    bridge.ingest(state(robotStatus=7, catDetect=0), ended)
    return ended


def test_visit_waits_for_its_weight_then_records_it(bridge: RobotBridge, store: Store):
    ended = walk_a_visit(bridge)
    assert store.events(limit=10, kinds=["visit"]) == []

    bridge.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(seconds=16))

    [visit] = store.events(limit=10, kinds=["visit"])
    assert visit["detail"]["duration_s"] == 35
    assert visit["detail"]["weight_lb"] == pytest.approx(9.42)


def test_a_saved_visit_is_labelled_at_once_when_the_cat_is_clear(bridge: RobotBridge, store: Store):
    cat_id = store.add_cat("Miso", 9.4, START)
    ended = walk_a_visit(bridge)

    bridge.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(seconds=16))

    [visit] = store.visits()
    assert (visit["label"], visit["cat_id"], visit["label_source"]) == ("cat", cat_id, "auto")


def test_a_labelling_failure_never_loses_the_visit_or_stops_the_bridge(
    bridge: RobotBridge, store: Store, monkeypatch: pytest.MonkeyPatch
):
    def broken(_: int) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(bridge._cats, "on_visit_saved", broken)
    ended = walk_a_visit(bridge)

    bridge.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(seconds=16))

    assert len(store.visits()) == 1


def test_hand_on_the_scale_is_not_a_visit(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(state(robotStatus=25, catDetect=2), START + timedelta(seconds=5))
    bridge.ingest(state(), START + timedelta(seconds=10))
    bridge.ingest(state(), START + timedelta(minutes=5))
    assert store.events(limit=10, kinds=["visit"]) == []


def test_clean_cycle_recorded_when_robot_returns_to_ready(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(state(robotStatus=10), START + timedelta(seconds=5))
    assert store.events(limit=10, kinds=["clean_cycle"]) == []

    bridge.ingest(state(odometerCleanCycles=101), START + timedelta(minutes=3))

    [cycle] = store.events(limit=10, kinds=["clean_cycle"])
    assert cycle["detail"] == {"count": 101, "drawer_percent": 35}


def test_litter_gauge_keeps_last_reading_while_globe_turns(bridge: RobotBridge):
    bridge.ingest(state(), START)
    assert bridge._litter_percent == 62

    bridge.ingest(state(robotStatus=10), START + timedelta(seconds=5))

    assert bridge.latest.litter_level is None
    assert bridge._litter_percent == 62


def test_drawer_pull_burst_is_one_event(bridge: RobotBridge, store: Store):
    bridge.ingest(activity("0x56000E"), START)
    bridge.ingest(activity("0x560010"), START + timedelta(seconds=2))
    bridge.ingest(activity("0x56000E"), START + timedelta(minutes=2))
    assert len(store.events(limit=10, kinds=["drawer_moved"])) == 2


def test_globe_fault_recorded_once_per_change(bridge: RobotBridge, store: Store):
    bridge.ingest(activity("0x350001"), START)
    bridge.ingest(activity("0x350001"), START + timedelta(seconds=1))
    bridge.ingest(activity("0x350000"), START + timedelta(seconds=2))
    assert [e["detail"]["code"] for e in store.events(limit=10, kinds=["fault"])] == [1]


def test_every_raw_message_is_kept(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(activity("0x0903AE"), START + timedelta(seconds=1))
    count = store._db.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert count == 2


def test_derived_state_survives_restart(tmp_path: Path, bridge: RobotBridge, store: Store):
    bridge.ingest(activity("0x0903AE"), START)
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    assert RobotBridge(config, store).derived.cat_weight_lb == pytest.approx(9.42)


def test_unknown_activity_register_is_harmless(bridge: RobotBridge):
    bridge.ingest(activity("0xEE1234", "not-hex"), START)
    assert bridge.last_seen == START


class _FakeLink:
    """The main broker connection with a robot behind it that only answers when asked."""

    def __init__(self, bridge: RobotBridge, **fields):
        self.bridge = bridge
        self.fields = fields
        self.sent: list[str] = []
        self.echoes_presses = True
        self.failure: Exception | None = None

    async def request_state(self):
        self._fail_if_broken()
        self.sent.append("requestState")
        self.bridge.ingest(state(**self.fields), datetime.now(UTC))

    async def publish(self, command):
        self._fail_if_broken()
        self.sent.append(command.code)
        if self.echoes_presses and command.register == const.Register.PANEL_BUTTON:
            self.bridge.ingest(activity(f"0x01{command.value:04X}"), datetime.now(UTC))

    def _fail_if_broken(self):
        if self.failure is not None:
            raise self.failure


def online_with_link(bridge: RobotBridge, **fields) -> _FakeLink:
    bridge.ingest(state(), datetime.now(UTC))
    bridge._link = link = _FakeLink(bridge, **fields)
    return link


@pytest.fixture
def quick_timeouts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bridge_module, "FRESH_STATE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(bridge_module, "PRESS_ECHO_TIMEOUT_SECONDS", 0.05)


def test_press_refused_while_panel_locked(bridge: RobotBridge):
    bridge.ingest(state(isKeypadLockout=1), datetime.now(UTC))
    bridge._link = link = _FakeLink(bridge, isKeypadLockout=1)

    with pytest.raises(RobotLocked):
        asyncio.run(bridge.press(commands.clean_cycle()))
    assert "0x02010201" not in link.sent


def test_press_sees_a_lock_set_at_the_panel_since_the_last_state(bridge: RobotBridge):
    link = online_with_link(bridge, isKeypadLockout=1)
    assert bridge.latest.keypad_lockout is False

    with pytest.raises(RobotLocked):
        asyncio.run(bridge.press(commands.clean_cycle()))
    assert "0x02010201" not in link.sent


def test_press_refused_when_robot_silent(bridge: RobotBridge):
    bridge.ingest(state(), datetime.now(UTC) - timedelta(hours=1))
    bridge._link = _FakeLink(bridge)

    with pytest.raises(RobotOffline):
        asyncio.run(bridge.press(commands.clean_cycle()))


def test_press_is_sent_once_and_logged(bridge: RobotBridge, store: Store):
    link = online_with_link(bridge)

    asyncio.run(bridge.press(commands.clean_cycle()))

    assert link.sent.count("0x02010201") == 1
    assert store.events(limit=1, kinds=["command"])[0]["detail"] == {"label": "cleanCycle"}


def test_empty_is_refused_when_the_drawer_is_full(bridge: RobotBridge):
    bridge.ingest(state(isDFIFull=1), datetime.now(UTC))
    bridge._link = link = _FakeLink(bridge, isDFIFull=1)

    with pytest.raises(DrawerFull):
        asyncio.run(bridge.press(commands.empty_cycle(), refuse=refuse_when_drawer_full))
    assert "0x02010801" not in link.sent


def test_empty_is_refused_while_panel_locked(bridge: RobotBridge):
    bridge.ingest(state(isKeypadLockout=1), datetime.now(UTC))
    bridge._link = link = _FakeLink(bridge, isKeypadLockout=1)

    with pytest.raises(RobotLocked):
        asyncio.run(bridge.press(commands.empty_cycle()))
    assert "0x02010801" not in link.sent


def test_a_real_empty_cycle_reads_as_emptying(bridge: RobotBridge):
    bridge.ingest(state(robotStatus=11, robotCycleStatus=6, robotCycleState=12), START)

    robot = bridge.status()["robot"]

    assert robot["status"] == "empty_cycle"
    assert robot["status_label"] == "Emptying"
    assert robot["is_cleaning"] is True


def test_the_globe_coming_home_from_empty_no_longer_reads_as_parked(bridge: RobotBridge):
    bridge.ingest(state(robotStatus=11, robotCycleStatus=7, robotCycleState=4), START)

    robot = bridge.status()["robot"]

    assert robot["status"] == "returning_home"
    assert robot["status_label"] == "Returning home"
    assert robot["is_cleaning"] is True


def test_empty_counts_as_started_on_the_status_a_real_robot_sends(
    bridge: RobotBridge, quick_timeouts
):
    link = online_with_link(bridge)
    link.echoes_presses = False

    async def press():
        task = asyncio.create_task(
            bridge.press(
                commands.empty_cycle(), confirms=lambda robot: robot.robot_status == "empty_cycle"
            )
        )
        await asyncio.sleep(0.02)
        link.fields["robotStatus"] = 11
        await task

    asyncio.run(press())


def test_empty_is_sent_once_and_logged(bridge: RobotBridge, store: Store):
    link = online_with_link(bridge)

    asyncio.run(bridge.press(commands.empty_cycle()))

    assert link.sent.count("0x02010801") == 1
    assert store.events(limit=1, kinds=["command"])[0]["detail"] == {"label": "emptyCycle"}


def test_press_without_an_echo_is_reported_not_retried(bridge: RobotBridge, quick_timeouts):
    link = online_with_link(bridge)
    link.echoes_presses = False

    with pytest.raises(PressNotAcknowledged):
        asyncio.run(bridge.press(commands.clean_cycle()))
    assert link.sent.count("0x02010201") == 1


def test_press_without_an_echo_counts_when_the_robot_shows_it_acted(
    bridge: RobotBridge, quick_timeouts
):
    link = online_with_link(bridge)
    link.echoes_presses = False

    async def press():
        task = asyncio.create_task(
            bridge.press(
                commands.clean_cycle(), confirms=lambda robot: robot.robot_status == "clean_cycle"
            )
        )
        await asyncio.sleep(0.02)
        link.fields["robotStatus"] = 10
        await task

    asyncio.run(press())


@pytest.mark.parametrize("failure", [aiomqtt.MqttError("gone"), WhiskerlessConnectionError("gone")])
def test_broker_failure_is_a_clean_unavailable_error(bridge: RobotBridge, failure: Exception):
    link = online_with_link(bridge)
    link.failure = failure

    for call in (bridge.press(commands.clean_cycle()), bridge.refresh()):
        with pytest.raises(BrokerUnavailable):
            asyncio.run(call)


def test_settings_connection_failure_is_a_clean_unavailable_error(
    bridge: RobotBridge, monkeypatch: pytest.MonkeyPatch
):
    online_with_link(bridge)
    monkeypatch.setattr(
        bridge, "_open_link", lambda role: _BrokenLink(WhiskerlessConnectionError("down"))
    )

    with pytest.raises(BrokerUnavailable):
        asyncio.run(bridge.apply(commands_for_brightness()))


def commands_for_brightness():
    return [commands.set_night_light_brightness(35)]


class _SettingsLink:
    def __init__(self, confirms: list[bool]):
        self.confirms = confirms
        self.applied: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def apply_setting(self, command):
        self.applied.append(command.label)
        return self.confirms[len(self.applied) - 1]


def test_apply_waits_for_the_state_that_shows_the_new_values(
    bridge: RobotBridge, monkeypatch: pytest.MonkeyPatch
):
    link = online_with_link(bridge, nightLightBrightness=35)
    monkeypatch.setattr(bridge, "_open_link", lambda role: _SettingsLink([True]))
    assert bridge.latest.night_light_brightness is None

    asyncio.run(bridge.apply(commands_for_brightness()))

    assert bridge.status()["robot"]["settings"]["night_light_brightness"] == 35
    assert link.sent == ["requestState"]


def test_apply_that_stops_part_way_shows_where_the_robot_really_is(
    bridge: RobotBridge, monkeypatch: pytest.MonkeyPatch
):
    link = online_with_link(bridge)
    settings_link = _SettingsLink([True, False])
    monkeypatch.setattr(bridge, "_open_link", lambda role: settings_link)

    with pytest.raises(SettingNotConfirmed):
        asyncio.run(bridge.apply([*commands_for_brightness(), commands.set_keypad_lockout(True)]))

    assert len(settings_link.applied) == 2
    assert link.sent == ["requestState"]


def robot_message(topic: str, payload: str, *, retain: bool = False):
    return SimpleNamespace(topic=topic, payload=payload.encode(), retain=retain)


def activity_message(*codes: str):
    return robot_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": list(codes)}))


async def drain():
    for _ in range(5):
        await asyncio.sleep(0)


def test_activity_makes_the_bridge_ask_for_state(bridge: RobotBridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "ACTIVITY_REFRESH_GAP_SECONDS", 0)
    link = online_with_link(bridge)

    async def scenario():
        bridge._handle(activity_message("0x0903AE"), link)
        await drain()

    asyncio.run(scenario())

    assert link.sent == ["requestState"]


def test_a_burst_of_activity_costs_one_question_that_trails_the_burst(
    bridge: RobotBridge, monkeypatch
):
    monkeypatch.setattr(bridge_module, "ACTIVITY_REFRESH_GAP_SECONDS", 0.3)
    link = online_with_link(bridge)

    async def scenario():
        for _ in range(4):
            bridge._handle(activity_message("0x0903AE"), link)
            await drain()
        await asyncio.sleep(0.01)
        assert link.sent == ["requestState"]
        bridge._handle(activity_message("0xBC0014"), link)
        await asyncio.sleep(0.6)

    asyncio.run(scenario())

    assert link.sent == ["requestState", "requestState"]


def test_no_question_while_a_write_is_in_progress(bridge: RobotBridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "ACTIVITY_REFRESH_GAP_SECONDS", 0)
    link = online_with_link(bridge)

    async def scenario():
        async with bridge._io_lock:
            bridge._handle(activity_message("0x0903AE"), link)
            await drain()

    asyncio.run(scenario())

    assert link.sent == []


def test_visit_is_recorded_when_the_robot_only_answers_when_asked(bridge: RobotBridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "ACTIVITY_REFRESH_GAP_SECONDS", 0)
    link = online_with_link(bridge)

    async def scenario():
        link.fields.update(robotStatus=25, catDetect=3)
        bridge._handle(activity_message("0x371021"), link)
        await drain()
        link.fields.update(robotStatus=7, catDetect=0)
        bridge._handle(activity_message("0xBC0014"), link)
        await drain()

    asyncio.run(scenario())

    assert len(bridge._visits.waiting) == 1


@pytest.mark.parametrize(
    "payload",
    [
        '{"type":"state","litterLevel":1e999}',
        '{"type":"state","robotStatus":Infinity}',
        "[" * 100000,
        "not json",
        '{"data": "0x09"}',
    ],
)
def test_a_bad_message_is_dropped_and_the_next_one_still_lands(bridge: RobotBridge, payload: str):
    link = online_with_link(bridge)
    for topic in ("state", "activity"):
        bridge._handle(robot_message(f"prod/LR4/{SERIAL}/{topic}", payload), link)

    bridge._handle(robot_message(f"prod/LR4/{SERIAL}/state", json.dumps(READY)), link)

    assert bridge.latest is not None
    assert bridge.latest.waste_drawer_level == 35


@pytest.mark.parametrize(
    ("topic", "retain"),
    [
        (f"prod/LR4/{SERIAL}/x/state", False),
        (f"prod/LR4/{SERIAL}/state/extra", False),
        (f"prod/LR4/{SERIAL}/command", False),
        ("prod/LR4/LR4C999999/state", False),
        (f"prod/LR4/{SERIAL}/state", True),
    ],
)
def test_only_the_robots_own_live_topics_are_recorded(
    bridge: RobotBridge, store: Store, topic: str, retain: bool
):
    link = online_with_link(bridge)
    before = store._db.execute("SELECT count(*) FROM messages").fetchone()[0]

    body = json.dumps({**READY, "DFILevelPercent": 99})
    bridge._handle(robot_message(topic, body, retain=retain), link)

    assert store._db.execute("SELECT count(*) FROM messages").fetchone()[0] == before
    assert bridge.latest.waste_drawer_level == 35


class _BrokenLink:
    def __init__(self, error: BaseException):
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, *_):
        return None


def test_run_survives_any_failure_and_reconnects(bridge: RobotBridge, monkeypatch):
    real_sleep = asyncio.sleep
    attempts: list[BaseException] = [
        RuntimeError("unexpected"),
        RecursionError(),
        aiomqtt.MqttError("gone"),
        asyncio.CancelledError(),
    ]

    async def instant(_):
        await real_sleep(0)

    monkeypatch.setattr(bridge_module.asyncio, "sleep", instant)
    monkeypatch.setattr(bridge, "_open_link", lambda role: _BrokenLink(attempts.pop(0)))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bridge.run())
    assert attempts == []


def test_each_connection_gets_its_own_unguessable_client_id(bridge: RobotBridge):
    async def open_links():
        return [bridge._open_link(role) for role in ("bridge", "bridge", "settings")]

    ids = {link._settings.client_id for link in asyncio.run(open_links())}
    assert len(ids) == 3
    assert all(i.startswith("whisker-local-") for i in ids)
    assert "whisker-local" not in ids and "whisker-local-settings" not in ids


def test_status_before_robot_ever_connects(bridge: RobotBridge):
    view = bridge.status()
    assert view["online"] is False
    assert view["robot"] is None
    assert view["alerts"] == []


def test_status_marks_drawer_gauge_as_estimate_after_reset(bridge: RobotBridge):
    bridge.ingest(state(isDFIResetPending=1, DFILevelPercent=0), datetime.now(UTC))
    assert bridge.status()["robot"]["drawer_is_estimate"] is True


def test_a_visit_waiting_for_its_weight_survives_a_restart(tmp_path: Path, store: Store):
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    first = RobotBridge(config, store)
    ended = walk_a_visit(first)
    assert store.visits() == []

    second = RobotBridge(config, store)
    second.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(seconds=40))

    [visit] = store.visits()
    assert visit["detail"]["duration_s"] == 35
    assert visit["detail"]["weight_lb"] == pytest.approx(9.42)


def test_a_restarted_visit_is_saved_once(tmp_path: Path, store: Store):
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    first = RobotBridge(config, store)
    ended = walk_a_visit(first)
    first.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(seconds=16))
    assert len(store.visits()) == 1

    # The process died after saving the visit but before the waiting list was rewritten.
    store.put_json(
        bridge_module.PENDING_VISITS_KEY,
        [{"started": (ended - timedelta(seconds=35)).isoformat(), "ended": ended.isoformat()}],
    )
    second = RobotBridge(config, store)
    second.ingest(state(robotStatus=7, catDetect=0), ended + timedelta(minutes=2))

    assert len(store.visits()) == 1


def test_unreadable_waiting_visits_are_ignored(tmp_path: Path, store: Store):
    store.put_json(bridge_module.PENDING_VISITS_KEY, [{"started": "nonsense"}])
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    assert RobotBridge(config, store)._visits.waiting == []


def test_a_flood_of_changing_firmware_versions_leaves_the_events_bounded(
    bridge: RobotBridge, store: Store
):
    bridge.ingest(state(), START)
    for i in range(500):
        bridge.ingest(state(espFirmware=f"2.{i}"), START + timedelta(seconds=i))

    firmware = store.events(limit=1000, kinds=["firmware"])
    assert len(firmware) == EVENT_BURST


def test_a_firmware_version_that_is_not_a_version_is_not_stored(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(state(espFirmware="x" * 14_000), START + timedelta(seconds=1))

    assert store.events(limit=10, kinds=["firmware"]) == []


def test_a_flood_fills_a_bounded_queue_and_logs_the_drops_once(caplog):
    asyncio.run(_flood(caplog))


async def _flood(caplog):
    client = aiomqtt.Client("broker")
    bridge_module.bound_inbound_queue(client)
    flood = bridge_module.INBOUND_QUEUE_MESSAGES * 3
    message = SimpleNamespace(topic="t", payload=b"x", qos=0, retain=False, mid=1, properties=None)
    with caplog.at_level("WARNING", logger="mqtt"):
        for _ in range(flood):
            client._on_message(None, None, message)
    assert client._queue.qsize() == bridge_module.INBOUND_QUEUE_MESSAGES
    warnings = [r for r in caplog.records if "queue is full" in r.getMessage()]
    assert len(warnings) == 1


FORGED_STATE = '{"robotStatus":4,"espFirmware":"\\ud800"}'


def test_a_message_with_a_lone_surrogate_is_dropped_before_it_is_stored(
    bridge: RobotBridge, store: Store
):
    link = online_with_link(bridge)
    before = bridge.status()

    bridge._handle(robot_message(f"prod/LR4/{SERIAL}/state", FORGED_STATE), link)

    assert store.events(limit=10, kinds=["firmware"]) == []
    assert bridge.status() == before
    json.dumps(store.events(limit=10), ensure_ascii=False).encode("utf-8")


def test_a_surrogate_hidden_deep_in_a_message_is_dropped_too(bridge: RobotBridge, store: Store):
    link = online_with_link(bridge)
    deep = '{"robotStatus":4,"extra":{"list":[{"\\udc00":1}]}}'

    bridge._handle(robot_message(f"prod/LR4/{SERIAL}/state", deep), link)

    assert bridge.status()["robot"]["status"] == "ready"


@pytest.mark.parametrize("version", ["a b", "1.2\n3", "v1.2/3", "é", "", "1" * 65, "<b>1</b>"])
def test_firmware_versions_outside_the_printable_pattern_are_not_stored(
    bridge: RobotBridge, store: Store, version: str
):
    bridge.ingest(state(), START)
    bridge.ingest(state(espFirmware=version), START + timedelta(seconds=1))

    assert store.events(limit=10, kinds=["firmware"]) == []
    assert bridge.status()["robot"]["esp_firmware"] is None


def test_a_normal_firmware_version_is_still_recorded(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), START)
    bridge.ingest(state(espFirmware="1.2.3-rc_4"), START + timedelta(seconds=1))

    assert [e["detail"]["version"] for e in store.events(limit=10, kinds=["firmware"])] == [
        "1.2.3-rc_4"
    ]


def test_a_restarted_server_still_says_when_it_last_heard_the_robot(tmp_path: Path, store: Store):
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    RobotBridge(config, store).ingest(state(), START)

    status = RobotBridge(config, store).status()

    assert status["online"] is False
    assert status["last_seen"] == bridge_module.stamp(START)

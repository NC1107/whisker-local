from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from whiskerless.devices.litter_robot_4 import commands, parse_message

from whisker_local.bridge import RobotBridge, RobotLocked, RobotOffline
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
    def __init__(self):
        self.sent = []

    async def publish(self, command):
        self.sent.append(command.code)


def test_press_refused_while_panel_locked(bridge: RobotBridge):
    now = datetime.now(UTC)
    bridge.ingest(state(isKeypadLockout=1), now)
    link = _FakeLink()
    bridge._link = link

    with pytest.raises(RobotLocked):
        asyncio.run(bridge.press(commands.clean_cycle()))
    assert link.sent == []


def test_press_refused_when_robot_silent(bridge: RobotBridge):
    bridge.ingest(state(), datetime.now(UTC) - timedelta(hours=1))
    bridge._link = _FakeLink()

    with pytest.raises(RobotOffline):
        asyncio.run(bridge.press(commands.clean_cycle()))


def test_press_is_sent_and_logged(bridge: RobotBridge, store: Store):
    bridge.ingest(state(), datetime.now(UTC))
    link = _FakeLink()
    bridge._link = link

    asyncio.run(bridge.press(commands.clean_cycle()))

    assert link.sent == ["0x02010201"]
    assert store.events(limit=1, kinds=["command"])[0]["detail"] == {"label": "cleanCycle"}


def test_status_before_robot_ever_connects(bridge: RobotBridge):
    view = bridge.status()
    assert view["online"] is False
    assert view["robot"] is None
    assert view["alerts"] == []


def test_status_marks_drawer_gauge_as_estimate_after_reset(bridge: RobotBridge):
    bridge.ingest(state(isDFIResetPending=1, DFILevelPercent=0), datetime.now(UTC))
    assert bridge.status()["robot"]["drawer_is_estimate"] is True

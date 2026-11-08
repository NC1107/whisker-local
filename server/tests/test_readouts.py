from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiomqtt
import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import derive, parse_message

from whisker_local import bridge as bridge_module
from whisker_local.bridge import LitterNotReadable, RobotBridge, StateNotFresh
from whisker_local.config import Config
from whisker_local.store import Store

SERIAL = "LR4C000001"
AUTH = {"Authorization": "Bearer right-key"}
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
# No litterLevelPercentage: the robot gave a distance only, so the percentage is ours to work out.
DISTANCE_ONLY = {"robotStatus": 4, "catDetect": 0, "litterLevel": 455}


def state(**fields):
    body = json.dumps({**DISTANCE_ONLY, **fields})
    return parse_message(f"prod/LR4/{SERIAL}/state", body)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


def make_bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    return RobotBridge(config, store)


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    return make_bridge(tmp_path, store)


class FakeLink:
    """The broker connection, with a robot that answers a state request with its current fields."""

    def __init__(self, bridge: RobotBridge, **fields):
        self.bridge = bridge
        self.fields = {**DISTANCE_ONLY, **fields}
        self.requests = 0
        self.answers = True
        self.failure: Exception | None = None

    async def request_state(self):
        if self.failure is not None:
            raise self.failure
        self.requests += 1
        if self.answers:
            self.bridge.ingest(
                parse_message(f"prod/LR4/{SERIAL}/state", json.dumps(self.fields)),
                datetime.now(UTC),
            )


def online(bridge: RobotBridge, **fields) -> FakeLink:
    bridge.ingest(state(), datetime.now(UTC))
    bridge._link = link = FakeLink(bridge, **fields)
    return link


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bridge_module, "FRESH_STATE_TIMEOUT_SECONDS", 0.05)


def robot(bridge: RobotBridge) -> dict:
    return bridge.status()["robot"]


# Litter calibration


def test_an_uncalibrated_gauge_says_where_its_number_comes_from(bridge: RobotBridge):
    bridge.ingest(state(), START)

    assert robot(bridge)["litter_calibrated"] is False
    assert robot(bridge)["litter_source"] == "default"
    assert robot(bridge)["litter_level_mm"] == 455

    bridge.derived.learned_litter.low = 440
    assert robot(bridge)["litter_source"] == "learned"


def test_a_percentage_from_the_firmware_wins_over_any_calibration(bridge: RobotBridge):
    bridge.ingest(state(litterLevelPercentage=62), START)
    assert robot(bridge)["litter_percent"] == 62
    assert robot(bridge)["litter_source"] == "reported"


def test_calibrating_full_pins_the_reading_at_ninety_percent(bridge: RobotBridge):
    link = online(bridge, litterLevel=420)

    asyncio.run(bridge.calibrate_litter("full"))

    view = robot(bridge)
    assert link.requests == 1
    assert view["litter_calibrated"] is True
    assert view["litter_source"] == "calibrated"
    assert view["litter_calibration"] == {"full_mm": 420, "empty_mm": None}
    assert view["litter_percent"] == 90
    bridge.ingest(state(litterLevel=450), START)
    assert robot(bridge)["litter_percent"] == 40


def test_calibrating_empty_as_well_makes_a_two_point_scale(bridge: RobotBridge):
    link = online(bridge, litterLevel=400)
    asyncio.run(bridge.calibrate_litter("full"))
    link.fields["litterLevel"] = 600
    asyncio.run(bridge.calibrate_litter("empty"))

    assert robot(bridge)["litter_calibration"] == {"full_mm": 400, "empty_mm": 600}
    assert robot(bridge)["litter_percent"] == 0
    bridge.ingest(state(litterLevel=500), START)
    assert robot(bridge)["litter_percent"] == 50
    bridge.ingest(state(litterLevel=380), START)
    assert robot(bridge)["litter_percent"] == 100


def test_the_measurement_is_taken_after_the_press_not_from_the_cache(bridge: RobotBridge):
    link = online(bridge, litterLevel=410)
    assert bridge.latest.litter_level_mm == 455

    asyncio.run(bridge.calibrate_litter("full"))

    assert robot(bridge)["litter_calibration"]["full_mm"] == 410
    assert link.requests == 1


def test_the_calibration_lowers_litter_alerts_with_it(bridge: RobotBridge):
    online(bridge, litterLevel=420)
    bridge.ingest(state(litterLevel=460), datetime.now(UTC))
    assert robot(bridge)["litter_percent"] == 70
    assert "litter_low" not in {a["key"] for a in bridge.status()["alerts"]}

    asyncio.run(bridge.calibrate_litter("full"))
    bridge.ingest(state(litterLevel=460), datetime.now(UTC))

    assert robot(bridge)["litter_percent"] == 23
    assert "litter_low" in {a["key"] for a in bridge.status()["alerts"]}


@pytest.mark.parametrize(
    "fields",
    [
        {"robotStatus": 10},
        {"robotStatus": 25, "catDetect": 3},
        {"catDetect": 3},
        {"robotStatus": 14},
        {"litterLevel": None},
    ],
)
def test_calibration_is_refused_while_the_sensor_is_not_looking_at_the_litter(
    bridge: RobotBridge, fields: dict
):
    online(bridge, **fields)

    with pytest.raises(LitterNotReadable):
        asyncio.run(bridge.calibrate_litter("full"))

    assert robot(bridge)["litter_calibrated"] is False
    assert bridge._store.get_json("litter_calibration") is None


def test_calibration_needs_a_fresh_reading(bridge: RobotBridge, quick):
    link = online(bridge)
    link.answers = False

    with pytest.raises(StateNotFresh):
        asyncio.run(bridge.calibrate_litter("full"))
    assert robot(bridge)["litter_calibrated"] is False


def test_calibration_refused_when_the_robot_is_offline(bridge: RobotBridge):
    from whisker_local.bridge import RobotOffline

    bridge.ingest(state(), datetime.now(UTC) - timedelta(hours=1))
    bridge._link = FakeLink(bridge)
    with pytest.raises(RobotOffline):
        asyncio.run(bridge.calibrate_litter("full"))


def test_calibration_broker_failure_is_unavailable_not_a_crash(bridge: RobotBridge):
    from whisker_local.bridge import BrokerUnavailable

    link = online(bridge)
    link.failure = aiomqtt.MqttError("gone")
    with pytest.raises(BrokerUnavailable):
        asyncio.run(bridge.calibrate_litter("full"))


def test_empty_has_to_read_farther_than_full_and_the_other_way_round(bridge: RobotBridge):
    link = online(bridge, litterLevel=500)
    asyncio.run(bridge.calibrate_litter("full"))
    link.fields["litterLevel"] = 450
    with pytest.raises(LitterNotReadable):
        asyncio.run(bridge.calibrate_litter("empty"))
    assert robot(bridge)["litter_calibration"] == {"full_mm": 500, "empty_mm": None}

    link.fields["litterLevel"] = 600
    asyncio.run(bridge.calibrate_litter("empty"))
    link.fields["litterLevel"] = 650
    with pytest.raises(LitterNotReadable):
        asyncio.run(bridge.calibrate_litter("full"))
    assert robot(bridge)["litter_calibration"] == {"full_mm": 500, "empty_mm": 600}


def test_calibration_survives_a_restart(tmp_path: Path, bridge: RobotBridge, store: Store):
    online(bridge, litterLevel=410)
    asyncio.run(bridge.calibrate_litter("full"))

    again = make_bridge(tmp_path, store)
    again.ingest(state(litterLevel=410), START)

    assert robot(again)["litter_calibration"] == {"full_mm": 410, "empty_mm": None}
    assert robot(again)["litter_percent"] == 90


def test_clearing_returns_to_the_learned_scale_straight_away(bridge: RobotBridge, store: Store):
    online(bridge, litterLevel=410)
    asyncio.run(bridge.calibrate_litter("full"))
    assert robot(bridge)["litter_percent"] == 90

    bridge.clear_litter_calibration()

    view = robot(bridge)
    assert view["litter_calibrated"] is False
    assert view["litter_source"] == "default"
    assert view["litter_percent"] != 90
    assert store.get_json("litter_calibration") == {"full_mm": None, "empty_mm": None}


def test_clearing_works_while_the_reading_is_withheld(bridge: RobotBridge):
    online(bridge, litterLevel=410)
    asyncio.run(bridge.calibrate_litter("full"))
    bridge.ingest(state(robotStatus=10, litterLevel=574), datetime.now(UTC))

    bridge.clear_litter_calibration()

    assert robot(bridge)["litter_percent"] is not None
    assert robot(bridge)["litter_level_mm"] == 410


@pytest.mark.parametrize(
    "stored", ["x", 3, [1], {"full_mm": "400", "empty_mm": 1.5}, {"full_mm": True}]
)
def test_a_damaged_stored_calibration_is_ignored(tmp_path: Path, store: Store, stored):
    store.put_json("litter_calibration", stored)
    assert make_bridge(tmp_path, store)._calibration == {"full_mm": None, "empty_mm": None}


def test_litter_calibration_api(client: TestClient, parts):
    _, _, bridge = parts
    online(bridge, litterLevel=420)

    body = client.post("/api/actions/calibrate-litter", json={"point": "full"}, headers=AUTH)

    assert body.status_code == 200
    assert body.json()["robot"]["litter_calibration"]["full_mm"] == 420
    assert body.json()["robot"]["litter_percent"] == 90

    cleared = client.delete("/api/actions/calibrate-litter", headers=AUTH)
    assert cleared.status_code == 200
    assert cleared.json()["robot"]["litter_calibrated"] is False


def test_litter_calibration_api_errors(client: TestClient, parts):
    _, _, bridge = parts
    assert (
        client.post(
            "/api/actions/calibrate-litter", json={"point": "full"}, headers=AUTH
        ).status_code
        == 503
    )
    online(bridge, robotStatus=10)

    busy = client.post("/api/actions/calibrate-litter", json={"point": "full"}, headers=AUTH)
    assert busy.status_code == 409
    assert "litter reading" in busy.json()["detail"]
    for bad in ({}, {"point": "half"}, {"point": "full", "mm": 3}):
        assert (
            client.post("/api/actions/calibrate-litter", json=bad, headers=AUTH).status_code == 422
        )


def test_litter_calibration_api_reports_a_robot_that_does_not_answer(
    client: TestClient, parts, quick
):
    _, _, bridge = parts
    online(bridge).answers = False
    response = client.post("/api/actions/calibrate-litter", json={"point": "full"}, headers=AUTH)
    assert response.status_code == 504


# Readouts


def test_status_before_any_sighting_has_no_hopper(bridge: RobotBridge):
    bridge.ingest(state(), START)
    view = robot(bridge)
    assert view["hopper"] is None
    assert view["empty_cycles"] is None
    assert view["drawer_last_moved"] is None
    assert view["bonnet_removed"] is False
    assert view["drawer_full_lockout"] is False


def test_empty_cycle_count_and_flags_come_from_the_robot(bridge: RobotBridge):
    bridge.ingest(
        state(
            odometerEmptyCycles=7,
            isBonnetRemoved=1,
            isDFIFull=1,
            DFILevelPercent=100,
            cleanCycleWaitTime=7,
        ),
        START,
    )
    view = robot(bridge)
    assert view["empty_cycles"] == 7
    assert view["bonnet_removed"] is True
    assert view["drawer_full"] is True


def test_drawer_full_lockout_is_reported_apart_from_the_merged_flag(bridge: RobotBridge):
    bridge.ingest(state(isDFIFull=0, DFIFullCounter=1), START)
    assert robot(bridge)["drawer_full_lockout"] is False

    bridge.ingest(state(isDFIFull=0, DFIFullCounter=3), START)

    assert robot(bridge)["drawer_full_lockout"] is True
    assert robot(bridge)["drawer_full"] is True
    assert "drawer_full_lockout" in {a["key"] for a in bridge.status()["alerts"]}


def test_last_drawer_movement_is_the_time_the_bay_moved(bridge: RobotBridge):
    moved = parse_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": ["0x56000E"]}))
    bridge.ingest(state(), START)
    bridge.ingest(moved, START + timedelta(minutes=3))

    assert robot(bridge)["drawer_last_moved"] == "2026-11-08T12:03:00.000+00:00"


def sight_hopper(bridge: RobotBridge, **fields):
    bridge.derived.sightings[derive.Capability.HOPPER] = derive.Evidence.DISPENSE
    for name, value in fields.items():
        setattr(bridge.derived, name, value)


def test_hopper_appears_only_once_one_has_been_sighted(bridge: RobotBridge):
    bridge.ingest(state(), START)
    assert robot(bridge)["hopper"] is None

    sight_hopper(bridge, hopper_connected=True)

    assert robot(bridge)["hopper"]["status"] == "ok"


def test_an_unlearned_hopper_gauge_is_labelled_an_estimate(bridge: RobotBridge):
    bridge.ingest(state(), START)
    sight_hopper(
        bridge,
        hopper_connected=True,
        hopper_fill_raw=85,
        last_hopper_dispensed=START,
    )

    hopper = robot(bridge)["hopper"]

    assert hopper["level_source"] == "estimate"
    assert hopper["fill_raw"] == 85
    assert 0 <= hopper["level_percent"] <= 100
    assert hopper["last_dispensed"] == "2026-11-08T12:00:00.000+00:00"
    assert hopper["connected"] is True


def test_a_learned_hopper_gauge_is_measured(bridge: RobotBridge):
    bridge.ingest(state(), START)
    sight_hopper(bridge, hopper_connected=True, hopper_fill_raw=85)
    learned = bridge.derived.learned_hopper
    learned.low, learned.high, learned.low_hits = 60, 90, 3

    hopper = robot(bridge)["hopper"]

    assert hopper["level_source"] == "measured"
    assert hopper["level_percent"] == derive.hopper_level_percent(bridge.derived)


def test_a_stale_hopper_gauge_gives_no_level(bridge: RobotBridge):
    bridge.ingest(state(), START)
    sight_hopper(bridge, hopper_connected=True, hopper_fill_raw=85, hopper_gauge_stale=True)

    hopper = robot(bridge)["hopper"]

    assert hopper["level_percent"] is None
    assert hopper["level_source"] is None
    assert hopper["status"] == "ok"


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"hopper_connected": False}, "disconnected"),
        ({"hopper_connected": True}, "ok"),
        ({"hopper_connected": None}, "unknown"),
        ({"hopper_connected": False, "hopper_fill_raw": 10}, "disconnected"),
    ],
)
def test_hopper_status(bridge: RobotBridge, fields: dict, status: str):
    bridge.ingest(state(), START)
    sight_hopper(bridge, **fields)
    assert robot(bridge)["hopper"]["status"] == status


def test_an_empty_hopper_says_so_and_raises_the_alert(bridge: RobotBridge):
    bridge.ingest(state(), datetime.now(UTC))
    sight_hopper(bridge, hopper_connected=True, hopper_fill_raw=5)
    learned = bridge.derived.learned_hopper
    learned.low, learned.high, learned.low_hits = 5, 90, 3

    hopper = robot(bridge)["hopper"]

    assert hopper["empty"] is True
    assert hopper["status"] == "empty"
    assert "hopper_empty" in {a["key"] for a in bridge.status()["alerts"]}

"""Visit capture from invented message sequences, in the shapes the audit reproduced.

Each test feeds the bridge what the robot would send, with the timing the real stream shows, so
the recorded visits are exactly what the owner would see.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store

SERIAL = "LR4C000001"
T0 = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)
READY = {
    "robotStatus": 4,
    "catDetect": 0,
    "DFILevelPercent": 35,
    "litterLevel": 455,
    "odometerCleanCycles": 100,
    "isKeypadLockout": 0,
    "espFirmware": "1.1.84",
}


def doc(status: int = 4, cat: int = 0, **fields):
    return parse_message(
        f"prod/LR4/{SERIAL}/state",
        json.dumps({**READY, "robotStatus": status, "catDetect": cat, **fields}),
    )


def act(*codes: str):
    return parse_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": list(codes)}))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "h.db")


def pair(folder: Path) -> tuple[Store, RobotBridge]:
    folder.mkdir()
    store = Store(folder / "h.db")
    config = Config(SERIAL, "key", "broker", 1883, folder / "h.db", folder / "web")
    return store, RobotBridge(config, store)


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    return RobotBridge(config, store)


def play(bridge: RobotBridge, steps, *, settle: float = 120) -> None:
    """Feed (seconds, message) pairs, then a quiet ready document so waiting visits are saved."""
    for seconds, message in steps:
        bridge.ingest(message, T0 + timedelta(seconds=seconds))
    last = max(seconds for seconds, _ in steps)
    bridge.ingest(doc(4, 0), T0 + timedelta(seconds=last + settle))


def visits(store: Store) -> list[dict]:
    return store.events(limit=50, kinds=["visit"])[::-1]


CANONICAL = [
    (0, doc()),
    (5, act("0x370011")),
    (7, doc(25, 3)),
    (20, act("0x370021")),
    (41, act("0x370010")),
    (42, act("0x370020")),
    (43, act("0x0902C3")),
    (43.1, act("0x570013", "0x6F0029", "0xB90001", "0xBC000A")),
    (45, doc(7, 0)),
]


def test_a_canonical_visit_is_one_visit_with_weight_and_the_robots_own_duration(
    bridge: RobotBridge, store: Store
):
    play(bridge, CANONICAL)

    [visit] = visits(store)
    detail = visit["detail"]
    assert detail["weight_lb"] == pytest.approx(7.07)
    assert detail["weight_raw"] == 707
    assert detail["duration_s"] == 41
    assert detail["settled_s"] == 10
    assert detail["origin"] == "live"


def test_start_is_backdated_to_the_beam_not_the_late_state_document(
    bridge: RobotBridge, store: Store
):
    steps = [s for s in CANONICAL if s[0] < 43.1 or s[0] > 44]
    play(bridge, steps)

    [visit] = visits(store)
    assert visit["detail"]["started"].endswith("03:00:05.000+00:00")
    assert visit["detail"]["duration_s"] == 40


def test_no_state_document_during_the_visit_still_records_it_when_a_weight_arrives(
    bridge: RobotBridge, store: Store
):
    play(
        bridge,
        [
            (0, doc()),
            (5, act("0x370011")),
            (41, act("0x370010")),
            (42, act("0x370020")),
            (43, act("0x0902C3")),
            (43.1, act("0xBC000A")),
            (60, doc(7, 0)),
        ],
    )

    [visit] = visits(store)
    assert visit["detail"]["weight_lb"] == pytest.approx(7.07)
    assert visit["detail"]["started"].endswith("03:00:05.000+00:00")


def test_a_visit_with_no_state_document_and_no_weight_is_not_guessed(
    bridge: RobotBridge, store: Store
):
    play(
        bridge,
        [
            (0, doc()),
            (5, act("0x370011")),
            (15, act("0x370010")),
            (16, act("0xBC0000")),
            (60, doc()),
        ],
    )

    assert visits(store) == []


def test_a_ten_second_hop_is_a_visit_with_unknown_weight(bridge: RobotBridge, store: Store):
    play(
        bridge,
        [(0, doc()), (5, act("0x370011")), (7, doc(25, 3)), (15, act("0x370010")), (16, doc(4, 0))],
    )

    [visit] = visits(store)
    assert visit["detail"]["weight_lb"] is None
    assert visit["detail"]["duration_s"] == 11


def test_a_weight_that_lands_after_the_visit_was_saved_is_added_to_it(
    bridge: RobotBridge, store: Store
):
    play(
        bridge,
        [(0, doc()), (5, act("0x370011")), (7, doc(25, 3)), (41, act("0x370010")), (45, doc(7, 0))],
        settle=30,
    )
    [saved] = visits(store)
    assert saved["detail"]["weight_lb"] is None

    bridge.ingest(act("0x0902C3"), T0 + timedelta(seconds=95))

    [visit] = visits(store)
    assert visit["id"] == saved["id"]
    assert visit["detail"]["weight_lb"] == pytest.approx(7.07)
    assert visit["detail"]["weight_raw"] == 707


def test_a_weight_twenty_seconds_late_still_belongs_to_the_visit_before_it(
    bridge: RobotBridge, store: Store
):
    play(
        bridge,
        [
            (0, doc()),
            (5, act("0x370011")),
            (7, doc(25, 3)),
            (41, act("0x370010")),
            (45, doc(7, 0)),
            (66, act("0x0902C3")),
        ],
    )

    [visit] = visits(store)
    assert visit["detail"]["weight_lb"] == pytest.approx(7.07)


def test_a_repeated_weight_for_the_same_visit_does_not_make_a_second_visit(
    bridge: RobotBridge, store: Store
):
    play(bridge, CANONICAL)

    bridge.ingest(act("0x0902C8"), T0 + timedelta(seconds=250))

    assert len(visits(store)) == 1


def test_an_inert_load_for_hours_is_not_a_visit(bridge: RobotBridge, store: Store):
    play(
        bridge,
        [
            (0, doc()),
            (5, act("0x370021")),
            (7, doc(25, 2)),
            (8100, act("0x370020")),
            (8102, doc(7, 0)),
        ],
    )

    assert visits(store) == []


def test_a_cat_that_sits_for_hours_is_one_visit_without_an_invented_duration(
    bridge: RobotBridge, store: Store
):
    play(bridge, [(0, doc()), (5, act("0x370011")), (7, doc(25, 3)), (8100, doc(7, 0))])

    [visit] = visits(store)
    assert visit["detail"]["duration_s"] is None
    assert visit["detail"]["long_stay"] is True


def test_two_cats_in_one_stretch_are_two_visits(bridge: RobotBridge, store: Store):
    play(
        bridge,
        [
            (0, doc()),
            (5, act("0x370011")),
            (7, doc(25, 3)),
            (40, act("0x0903FE")),
            (42, doc(25, 3)),
            (80, act("0x090366")),
            (84, doc(7, 0)),
        ],
    )

    weights = [v["detail"]["weight_lb"] for v in visits(store)]
    assert weights == [pytest.approx(10.22), pytest.approx(8.70)]


def test_two_readings_of_one_cat_are_one_visit(bridge: RobotBridge, store: Store):
    play(
        bridge,
        [
            (0, doc()),
            (7, doc(25, 3)),
            (40, act("0x09038E")),
            (100, act("0x090398")),
            (104, doc(7, 0)),
        ],
    )

    [visit] = visits(store)
    assert visit["detail"]["weight_lb"] == pytest.approx(9.15, abs=0.01)


def test_a_cat_inside_during_a_cycle_is_a_visit_only_once_a_weight_proves_it(tmp_path: Path):
    steps = [
        (0, doc()),
        (1, doc(10, 0)),
        (10, act("0x370021")),
        (12, doc(10, 2)),
        (40, act("0x370011")),
        (42, doc(10, 3)),
        (46, act("0x370010")),
        (47, doc(10, 2)),
        (55, act("0x370020")),
        (58, act("0xBC0005", "0xB90001")),
        (60, doc(10, 0)),
    ]
    silent_store, silent = pair(tmp_path / "silent")
    play(silent, steps, settle=100)
    assert visits(silent_store) == []

    weighed_store, weighed = pair(tmp_path / "weighed")
    play(weighed, [*steps[:-1], (59, act("0x0902C3")), (60, doc(10, 0))], settle=100)
    [visit] = visits(weighed_store)
    assert visit["detail"]["weight_lb"] == pytest.approx(7.07)


def test_the_globe_raising_catdetect_in_every_cycle_is_never_a_visit(
    bridge: RobotBridge, store: Store
):
    play(
        bridge,
        [
            (0, doc()),
            (1, doc(10, 0)),
            (20, act("0x370011")),
            (22, doc(10, 1)),
            (30, doc(10, 1)),
            (40, act("0x370010")),
            (42, doc(10, 0)),
            (50, doc(4, 0)),
        ],
    )

    assert visits(store) == []
    assert len(store.events(limit=10, kinds=["clean_cycle"])) == 1


def test_catdetect_alone_in_the_ready_state_is_not_a_visit(bridge: RobotBridge, store: Store):
    play(bridge, [(0, doc()), (5, doc(4, 3)), (9, doc(4, 3)), (12, doc(4, 0))])

    assert visits(store) == []


def test_a_hand_in_the_beam_is_not_a_visit(bridge: RobotBridge, store: Store):
    play(
        bridge,
        [(0, doc()), (5, act("0x370011")), (6, doc(4, 1)), (9, act("0x370010")), (10, doc())],
    )

    assert visits(store) == []


def test_the_robots_last_visit_is_the_last_recorded_visit_not_a_sensor_stamp(
    bridge: RobotBridge, store: Store
):
    play(
        bridge, [(0, doc()), (1, doc(10, 0)), (20, act("0x370011")), (22, doc(10, 1)), (50, doc())]
    )
    assert bridge.status()["robot"]["last_visit_at"] is None
    assert bridge.status()["robot"]["last_visit_duration_s"] is None

    play(bridge, [(s + 1000, m) for s, m in CANONICAL])

    robot = bridge.status()["robot"]
    [visit] = visits(store)
    assert robot["last_visit_at"] == visit["at"]
    assert robot["last_visit_duration_s"] == 41


def test_the_no_visit_alert_clock_ignores_cycles_and_hands(bridge: RobotBridge, store: Store):
    play(bridge, CANONICAL)
    later = 25 * 3600
    cycle = [
        (later, doc(10, 0)),
        (later + 20, act("0x370011")),
        (later + 22, doc(10, 1)),
        (later + 50, doc()),
    ]
    play(bridge, cycle)

    now = T0 + timedelta(seconds=later + 3600)
    bridge.ingest(doc(4, 0), now)
    bridge.ingest(doc(4, 0), now + timedelta(hours=1))
    assert "no_visit" in {alert.value for alert in bridge._alerts.active}


def test_a_visit_waiting_for_its_weight_keeps_its_readings_across_a_restart(
    tmp_path: Path, store: Store
):
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "h.db", tmp_path / "web")
    first = RobotBridge(config, store)
    for seconds, message in CANONICAL[:-1]:
        first.ingest(message, T0 + timedelta(seconds=seconds))
    first.ingest(doc(7, 0), T0 + timedelta(seconds=45))
    assert visits(store) == []

    second = RobotBridge(config, store)
    second.ingest(doc(4, 0), T0 + timedelta(seconds=90))

    [visit] = visits(store)
    assert visit["detail"]["weight_lb"] == pytest.approx(7.07)


REPLAY_DB = os.environ.get("WHISKER_REPLAY_DB")


@pytest.mark.skipif(not REPLAY_DB, reason="needs a private database of raw robot messages")
def test_replaying_the_real_stream_finds_no_false_visits_and_all_cycles(tmp_path: Path):
    source = sqlite3.connect(f"file:{REPLAY_DB}?mode=ro", uri=True)
    store = Store(tmp_path / "replay.db")
    config = Config(SERIAL, "key", "broker", 1883, tmp_path / "replay.db", tmp_path / "web")
    replay = RobotBridge(config, store)
    last = None
    for at, kind, body in source.execute("SELECT at, kind, body FROM messages ORDER BY id"):
        last = datetime.fromisoformat(at)
        replay.ingest(parse_message(f"prod/LR4/{SERIAL}/{kind}", body), last)
    assert last is not None
    replay.ingest(doc(4, 0), last + timedelta(minutes=5))

    assert visits(store) == []
    assert len(store.events(limit=50, kinds=["clean_cycle"])) == 3

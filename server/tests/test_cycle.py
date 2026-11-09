from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local import bridge as bridge_module
from whisker_local import cycle
from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.push_catalog import CATALOG
from whisker_local.store import Store

SERIAL = "LR4C000001"
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer right-key"}
READY = {
    "robotStatus": 4,
    "robotCycleState": 1,
    "robotCycleStatus": 1,
    "catDetect": 0,
    "DFILevelPercent": 35,
    "litterLevelPercentage": 62,
    "litterLevel": 455,
    "odometerCleanCycles": 100,
    "isKeypadLockout": 0,
    "espFirmware": "1.4.4",
}

# An invented cycle shaped like a captured one: two cat interruptions during the dump, the first
# with only the load cell tripped and the second with the beam as well, then the three
# remaining stages. Offsets are seconds from the start of the cycle, fields are what changes.
REPLAY: list[tuple[int, dict[str, int]]] = [
    (0, {"robotStatus": 10, "robotCycleState": 3, "robotCycleStatus": 2}),
    (3, {"robotCycleState": 4, "catDetect": 2}),
    (20, {"catDetect": 0}),
    (41, {"robotCycleState": 3}),
    (70, {"robotCycleState": 4, "catDetect": 3}),
    (130, {"catDetect": 0}),
    (140, {"robotCycleState": 3}),
    (155, {"robotCycleStatus": 3}),
    (170, {"robotCycleStatus": 4}),
    (212, {"robotCycleStatus": 5}),
    (
        226,
        {"robotStatus": 4, "robotCycleState": 1, "robotCycleStatus": 1, "odometerCleanCycles": 101},
    ),
]
PAUSED_SECONDS = (41 - 3) + (140 - 70)


def doc(**fields) -> object:
    return parse_message(f"prod/LR4/{SERIAL}/state", json.dumps({**READY, **fields}))


def replay(
    bridge: RobotBridge,
    start: datetime,
    steps: list[tuple[int, dict[str, int]]] = REPLAY,
    *,
    until: int | None = None,
) -> None:
    fields: dict[str, int] = {}
    for offset, change in steps:
        if until is not None and offset > until:
            return
        fields.update(change)
        bridge.ingest(doc(**fields), start + timedelta(seconds=offset))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def bridge(tmp_path: Path, store: Store) -> RobotBridge:
    config = Config(SERIAL, "right-key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    return RobotBridge(config, store)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    now = {"at": START}
    monkeypatch.setattr(bridge_module, "_utcnow", lambda: now["at"])
    return now


def add_history(store: Store, count: int, active_s: int = 200, paused_s: int = 0) -> None:
    for i in range(count):
        store.add_event(
            START - timedelta(hours=count - i),
            "clean_cycle",
            {
                "count": 90 + i,
                "drawer_percent": 30,
                "duration_s": active_s + paused_s,
                "pauses": 1 if paused_s else 0,
                "paused_seconds": paused_s,
            },
        )


def view(bridge: RobotBridge, clock, at: datetime):
    clock["at"] = at
    return bridge.status()["robot"]["cycle"]


def test_the_stage_follows_the_robot_through_a_cycle(bridge: RobotBridge, clock):
    bridge.ingest(doc(), START - timedelta(seconds=30))
    assert view(bridge, clock, START - timedelta(seconds=30)) is None

    seen = {}
    fields: dict[str, int] = {}
    for offset, change in REPLAY[:-1]:
        fields.update(change)
        bridge.ingest(doc(**fields), START + timedelta(seconds=offset))
        seen[offset] = view(bridge, clock, START + timedelta(seconds=offset))["stage"]

    assert seen[0] == seen[140] == 0
    assert (seen[155], seen[170], seen[212]) == (1, 2, 3)


def test_stage_names_are_the_four_the_robot_goes_through(bridge: RobotBridge, clock):
    replay(bridge, START, until=0)
    assert view(bridge, clock, START)["stages"] == ["Dump", "Measure", "Level", "Home"]


def test_the_cycle_view_is_gone_once_the_robot_is_home(bridge: RobotBridge, clock):
    replay(bridge, START)
    assert view(bridge, clock, START + timedelta(seconds=230)) is None


def test_a_cat_pause_with_only_the_scale_tripped_says_cat_sensor(bridge: RobotBridge, clock):
    replay(bridge, START, until=3)
    paused = view(bridge, clock, START + timedelta(seconds=4))
    assert paused["paused"] is True
    assert paused["pause_label"] == "Paused, cat sensor"


def test_a_pause_with_beam_and_scale_both_tripped_says_looks_like_a_cat(bridge: RobotBridge, clock):
    replay(bridge, START, until=70)
    paused = view(bridge, clock, START + timedelta(seconds=71))
    assert paused["pause_label"] == "Paused, looks like a cat"


def test_a_pause_stays_a_pause_after_the_sensors_clear(bridge: RobotBridge, clock):
    replay(bridge, START, until=20)
    paused = view(bridge, clock, START + timedelta(seconds=21))
    assert paused["paused"] is True
    assert paused["pause_label"] == "Paused, cat sensor"


def test_the_cycle_resumes_when_the_robot_leaves_the_pause(bridge: RobotBridge, clock):
    replay(bridge, START, until=41)
    running = view(bridge, clock, START + timedelta(seconds=45))
    assert running["paused"] is False
    assert running["pause_label"] is None


def test_the_saved_cycle_counts_its_pauses_and_their_seconds(bridge: RobotBridge, store: Store):
    replay(bridge, START)

    [saved] = store.events(limit=5, kinds=["clean_cycle"])
    assert saved["detail"] == {
        "count": 101,
        "drawer_percent": 35,
        "duration_s": 226,
        "pauses": 2,
        "paused_seconds": PAUSED_SECONDS,
    }


def test_an_uninterrupted_cycle_records_zero_pauses(bridge: RobotBridge, store: Store):
    replay(bridge, START, [REPLAY[0], *REPLAY[7:]])

    [saved] = store.events(limit=5, kinds=["clean_cycle"])
    assert (saved["detail"]["pauses"], saved["detail"]["paused_seconds"]) == (0, 0)


def test_a_cycle_the_server_joined_midway_records_no_pause_figures(
    bridge: RobotBridge, store: Store
):
    midway = {"robotStatus": 10, "robotCycleState": 3, "robotCycleStatus": 3}
    replay(bridge, START, [(155, midway), *REPLAY[8:]])

    [saved] = store.events(limit=5, kinds=["clean_cycle"])
    assert saved["detail"] == {"count": 101, "drawer_percent": 35}


def test_a_cycle_with_a_long_blind_gap_records_no_pause_figures(bridge: RobotBridge, store: Store):
    replay(bridge, START, [*REPLAY[:3], *[(o + 600, c) for o, c in REPLAY[3:]]])

    [saved] = store.events(limit=5, kinds=["clean_cycle"])
    assert "pauses" not in saved["detail"]


def test_a_cycle_that_never_returns_to_ready_records_nothing(bridge: RobotBridge, store: Store):
    replay(bridge, START, until=170)
    bridge.ingest(doc(robotStatus=1), START + timedelta(seconds=180))
    assert store.events(limit=5, kinds=["clean_cycle"]) == []
    assert bridge.status()["robot"]["cycle"] is None


def test_without_history_there_is_a_stage_and_no_percentage(bridge: RobotBridge, clock):
    replay(bridge, START, until=175)
    shown = view(bridge, clock, START + timedelta(seconds=176))
    assert shown["stage"] == 2
    assert shown["progress"] is None


def test_cycles_without_figures_count_as_no_history(bridge: RobotBridge, store: Store, clock):
    for i in range(6):
        store.add_event(START - timedelta(hours=i + 1), "clean_cycle", {"count": 90 + i})
    replay(bridge, START, until=175)
    assert view(bridge, clock, START + timedelta(seconds=176))["progress"] is None


def test_cycles_without_figures_do_not_crowd_out_the_ones_with(
    bridge: RobotBridge, store: Store, clock
):
    add_history(store, 4, active_s=200)
    for i in range(30):
        store.add_event(START - timedelta(minutes=i + 1), "clean_cycle", {"count": 50 + i})
    replay(bridge, START, until=175)
    assert view(bridge, clock, START + timedelta(seconds=176))["progress"] is not None


def test_two_recorded_cycles_are_too_few_to_estimate_from(bridge: RobotBridge, store: Store, clock):
    add_history(store, 2)
    replay(bridge, START, until=175)
    assert view(bridge, clock, START + timedelta(seconds=176))["progress"] is None


def test_with_history_progress_is_an_estimate_inside_the_current_stage(
    bridge: RobotBridge, store: Store, clock
):
    add_history(store, 5, active_s=200)
    replay(bridge, START, until=175)
    shown = view(bridge, clock, START + timedelta(seconds=176))
    # 176 s elapsed minus 108 s paused is 68 s of a typical 200, which is behind the level stage.
    assert shown["progress_is_estimate"] is True
    assert shown["progress"] == pytest.approx(0.5, abs=0.001)


def test_progress_time_does_not_run_during_a_pause(bridge: RobotBridge, store: Store, clock):
    add_history(store, 5, active_s=200)
    replay(bridge, START, until=3)
    early = view(bridge, clock, START + timedelta(seconds=10))["progress"]
    later = view(bridge, clock, START + timedelta(seconds=30))["progress"]
    assert early == later


def test_a_cycle_running_long_stays_below_the_top_of_its_stage():
    assert cycle.progress(1, 10_000, 200) < 0.5
    assert cycle.progress(3, 10_000, 200) < 1.0


def test_a_quick_cycle_cannot_fall_behind_the_stage_it_is_in():
    assert cycle.progress(2, 1, 200) == 0.5


def test_progress_is_unknown_when_the_stage_is_unknown():
    assert cycle.progress(None, 50, 200) is None


def test_the_typical_length_leaves_out_pauses_and_uses_the_middle(store: Store):
    add_history(store, 3, active_s=180, paused_s=60)
    store.add_event(
        START, "clean_cycle", {"count": 1, "duration_s": 900, "pauses": 0, "paused_seconds": 0}
    )
    recent = store.events(limit=10, kinds=["clean_cycle"])
    assert cycle.typical_active_seconds(recent) == 180


@pytest.fixture
def client(tmp_path: Path, store: Store, bridge: RobotBridge):
    config = Config(SERIAL, "right-key", "broker", 1883, tmp_path / "history.db", tmp_path / "web")
    with TestClient(create_app(config, store, bridge, run_bridge=False)) as test_client:
        yield test_client


def test_the_pause_is_on_api_status(client: TestClient, bridge: RobotBridge):
    replay(bridge, datetime.now(UTC) - timedelta(seconds=20), until=3)

    robot = client.get("/api/status", headers=AUTH).json()["robot"]

    assert robot["cycle"]["paused"] is True
    assert robot["cycle"]["pause_label"] == "Paused, cat sensor"
    assert robot["cycle"]["stage"] == 0


def test_a_quiet_robot_has_no_cycle_on_api_status(client: TestClient, bridge: RobotBridge):
    bridge.ingest(doc(), START)
    assert client.get("/api/status", headers=AUTH).json()["robot"]["cycle"] is None


def day_rows(client: TestClient, today: date) -> dict[str, dict]:
    body = client.get(
        "/api/data-quality",
        params={"start": str(today - timedelta(days=6)), "end": str(today)},
        headers=AUTH,
    ).json()
    return {row["date"]: row for row in body["days"]}


def test_data_quality_gives_pauses_per_cycle_for_days_with_watched_cycles(
    client: TestClient, store: Store
):
    today = datetime.now(UTC).date()
    noon = datetime(today.year, today.month, today.day, 0, 5, tzinfo=UTC)
    for pauses in (2, 0, 1):
        store.add_event(noon, "clean_cycle", {"count": 1, "pauses": pauses, "paused_seconds": 9})
        noon += timedelta(minutes=1)

    row = day_rows(client, today)[today.isoformat()]

    assert row["cycles_watched"] == 3
    assert row["pauses_per_cycle"] == 1.0


def test_imported_cycles_leave_a_gap_not_a_zero(client: TestClient, store: Store):
    today = datetime.now(UTC).date()
    at = datetime(today.year, today.month, today.day, 0, 5, tzinfo=UTC)
    store.add_event(at, "clean_cycle", {"count": 5, "source": "import"})

    row = day_rows(client, today)[today.isoformat()]

    assert row["cycles_watched"] == 0
    assert row["pauses_per_cycle"] is None


def test_a_day_with_no_cycles_is_a_gap(client: TestClient):
    today = datetime.now(UTC).date()
    row = day_rows(client, today)[(today - timedelta(days=2)).isoformat()]
    assert row["pauses_per_cycle"] is None


def test_a_hopper_that_cannot_be_detected_is_not_offered_as_a_notification():
    assert "hopper_disconnected" not in {kind.key for kind in CATALOG}
    assert "hopper_empty" in {kind.key for kind in CATALOG}


def test_the_catalog_the_app_reads_has_no_hopper_disconnected(client: TestClient):
    keys = {entry["key"] for entry in client.get("/api/push", headers=AUTH).json()["catalog"]}
    assert "hopper_disconnected" not in keys

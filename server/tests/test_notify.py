"""Notification controls: moments from saved events, per-cat choices, quiet hours and delivery.

The relay is always a fake that records what it was sent; nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from whiskerless.devices.litter_robot_4 import alerts, parse_message

from whisker_local import alert_settings, push, push_catalog, push_options
from whisker_local import bridge as bridge_module
from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.moments import PowerWatch
from whisker_local.push import Push, PushNotifier
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
SERIAL = "LR4C000001"
START = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TOKEN = "apns:token-1"
READY = {
    "robotStatus": 4,
    "catDetect": 0,
    "DFILevelPercent": 35,
    "litterLevel": 455,
    "odometerCleanCycles": 100,
    "isKeypadLockout": 0,
    "espFirmware": "1.1.75",
}


def state(**fields):
    return parse_message(f"prod/LR4/{SERIAL}/state", json.dumps({**READY, **fields}))


def weight(pounds: float) -> str:
    return f"0x09{round(pounds * 100):04X}"


def activity(*codes: str):
    return parse_message(f"prod/LR4/{SERIAL}/activity", json.dumps({"data": list(codes)}))


class FakeRelay:
    """Stands in for the relay and records when each send arrived."""

    def __init__(self, clock: list[float]) -> None:
        self._clock = clock
        self.sends: list[tuple[float, dict[str, Any]]] = []
        self.statuses: list[int] = []

    def __call__(self, url: str, payload: dict[str, Any], key: str | None):
        if url.endswith("/v1/register"):
            return 200, {"key": "llr_issued"}
        status = self.statuses.pop(0) if self.statuses else 200
        self.sends.append((self._clock[0], payload))
        if status != 200:
            return status, {"error": "slow down"}
        results = [{"token": m["token"], "status": "delivered"} for m in payload["messages"]]
        return 200, {"results": results}

    def messages(self) -> list[dict[str, Any]]:
        return [m for _, payload in self.sends for m in payload["messages"]]

    def titles(self) -> list[str]:
        return [m["title"] for m in self.messages()]


class Rig:
    """A bridge with a notifier whose relay, clock and local time the test controls."""

    def __init__(self, tmp_path: Path) -> None:
        web = tmp_path / "web"
        web.mkdir(exist_ok=True)
        self.config = Config(SERIAL, "right-key", "broker", 1883, tmp_path / "h.db", web)
        self.store = Store(self.config.db_path)
        self.clock = [0.0]
        self.sleeps: list[float] = []
        self.wall = [START]
        self.relay = FakeRelay(self.clock)
        push.add_device(self.store, TOKEN, "ios")
        self.notifier = self.new_notifier()
        self.bridge = RobotBridge(self.config, self.store, notifier=self.notifier)

    def new_notifier(self) -> PushNotifier:
        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.clock[0] += seconds

        return PushNotifier(
            "https://relay.example.com",
            self.store,
            post=self.relay,
            clock=lambda: self.clock[0],
            sleep=sleep,
            now=lambda: self.wall[0],
        )

    def restart(self) -> None:
        self.notifier = self.new_notifier()
        self.bridge = RobotBridge(self.config, self.store, notifier=self.notifier)

    def flush(self) -> list[str]:
        asyncio.run(self.notifier.flush())
        return self.relay.titles()

    def kinds(self, **on: bool) -> None:
        push.update_kinds(self.store, on)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    monkeypatch.setattr(bridge_module, "_utcnow", lambda: START)
    return Rig(tmp_path)


def walk_a_visit(
    bridge: RobotBridge, at: datetime, *, seconds: int = 35, pounds: float = 9.42
) -> datetime:
    """A visit that starts at ``at``, and the time its weight has been read."""
    bridge.ingest(state(), at)
    bridge.ingest(state(robotStatus=25, catDetect=3), at + timedelta(seconds=5))
    bridge.ingest(activity(weight(pounds)), at + timedelta(seconds=seconds - 5))
    ended = at + timedelta(seconds=seconds)
    bridge.ingest(state(robotStatus=7, catDetect=0), ended)
    done = ended + timedelta(seconds=16)
    bridge.ingest(state(robotStatus=7, catDetect=0), done)
    return done


# Moments are fired from saved visits


def test_a_visit_is_announced_once_its_weight_is_known(rig: Rig):
    rig.kinds(visit=True)
    done = walk_a_visit(rig.bridge, START)
    assert rig.flush() == ["A cat used the box"]
    [message] = rig.relay.messages()
    assert message["body"] == "30 s, 9.42 lb."
    assert message["collapseId"] == "visit"
    assert done > START


def test_a_visit_names_the_cat_and_has_a_collapse_id_of_its_own(rig: Rig):
    tom = rig.store.add_cat("Tom", 9.4, START)
    rig.store.add_cat("Kit", 13.0, START)
    rig.kinds(visit=True)
    walk_a_visit(rig.bridge, START)
    rig.flush()
    [message] = rig.relay.messages()
    assert message["title"] == "Tom used the box"
    assert message["collapseId"] == f"visit:{tom}"


def test_a_restart_does_not_resend_an_announced_visit(rig: Rig):
    rig.kinds(visit=True)
    walk_a_visit(rig.bridge, START)
    rig.flush()
    assert len(rig.relay.messages()) == 1

    rig.restart()
    rig.bridge.ingest(state(), START + timedelta(minutes=5))
    rig.bridge.status()
    rig.flush()

    assert len(rig.relay.messages()) == 1


def test_a_visit_still_waiting_for_its_weight_is_announced_once_after_a_restart(rig: Rig):
    rig.kinds(visit=True)
    rig.bridge.ingest(state(), START)
    rig.bridge.ingest(state(robotStatus=25, catDetect=3), START + timedelta(seconds=5))
    rig.bridge.ingest(state(robotStatus=7, catDetect=0), START + timedelta(seconds=40))
    # Without a weight the visit is saved after the grace and announced after a short wait.
    rig.bridge.ingest(state(robotStatus=7), START + timedelta(seconds=60))
    assert len(rig.store.visits()) == 1
    assert rig.flush() == []

    rig.restart()
    later = START + timedelta(seconds=120)
    rig.bridge.ingest(state(), later)
    rig.bridge.ingest(state(), later + timedelta(seconds=1))
    assert rig.flush() == ["A cat used the box"]

    rig.restart()
    rig.bridge.ingest(state(), later + timedelta(minutes=5))
    assert rig.flush() == ["A cat used the box"]


def test_turning_push_on_does_not_announce_old_visits(rig: Rig):
    rig.store.add_event(START - timedelta(minutes=5), "visit", {"origin": "live", "weight_lb": 9})
    rig.store.put_json("push_moments", None)
    rig.restart()
    rig.kinds(visit=True)
    rig.bridge.ingest(state(), START + timedelta(minutes=1))
    assert rig.flush() == []


def test_a_visit_from_before_an_outage_is_not_announced_late(rig: Rig):
    rig.kinds(visit=True)
    rig.store.put_json("push_moments", {"cursors": {"visit": 0}})
    old = START - timedelta(hours=3)
    rig.store.add_event(old, "visit", {"origin": "live", "weight_lb": 9.4, "duration_s": 30})
    rig.restart()
    rig.bridge.ingest(state(), START)
    assert rig.flush() == []


def test_the_cat_sensor_bit_alone_never_announces_anything(rig: Rig):
    rig.kinds(visit=True, long_visit=True, frequent_visits=True, cycle_completed=True)
    rig.bridge.ingest(state(), START)
    for n in range(1, 8):
        at = START + timedelta(seconds=n * 20)
        rig.bridge.ingest(state(robotStatus=10, catDetect=1), at)
        rig.bridge.ingest(state(robotStatus=10, catDetect=0), at + timedelta(seconds=10))
    assert rig.flush() == []


def test_an_imported_visit_is_never_announced(rig: Rig):
    rig.kinds(visit=True)
    rig.bridge.ingest(state(), START)
    rig.store.add_event(
        START, "visit", {"origin": "import", "import_batch": "b1", "weight_lb": 9.4}
    )
    rig.bridge.ingest(state(), START + timedelta(minutes=1))
    assert rig.flush() == []


def test_a_visit_marked_not_a_cat_is_not_announced(rig: Rig):
    rig.kinds(visit=True)
    rig.bridge.ingest(state(), START)
    event = rig.store.add_event(
        START, "visit", {"origin": "live", "weight_lb": 9.4, "duration_s": 20}
    )
    rig.store.set_user_label(event, "not_cat", None)
    rig.bridge._moments.visit_saved(event)
    rig.bridge.ingest(state(), START + timedelta(seconds=30))
    assert rig.flush() == []


# Kinds


def test_a_long_visit_follows_the_threshold(rig: Rig):
    rig.kinds(long_visit=True)
    alert_settings.update(rig.store, {"long_visit_minutes": 2})
    walk_a_visit(rig.bridge, START, seconds=100)
    assert rig.flush() == []
    walk_a_visit(rig.bridge, START + timedelta(minutes=10), seconds=150)
    assert rig.flush() == ["A cat stayed a while"]
    [message] = rig.relay.messages()
    assert message["body"] == "2 min in the box."


def test_many_visits_in_an_hour_are_one_notice_per_burst(rig: Rig):
    rig.kinds(frequent_visits=True)
    alert_settings.update(rig.store, {"frequent_visits_per_hour": 3})
    for n in range(5):
        walk_a_visit(rig.bridge, START + timedelta(minutes=n * 5))
    assert rig.flush() == ["Busy box"]
    assert rig.relay.messages()[0]["body"] == "3 visits in the last hour."


def test_a_weight_change_is_judged_per_cat(rig: Rig):
    tom = rig.store.add_cat("Tom", 9.4, START)
    rig.store.add_cat("Kit", 13.0, START)
    rig.kinds(weight_change=True)
    for n in range(1, 10):
        event = rig.store.add_event(
            START - timedelta(days=n), "visit", {"origin": "live", "weight_lb": 9.4}
        )
        rig.store.set_user_label(event, "cat", tom)
    walk_a_visit(rig.bridge, START, pounds=9.5)
    assert rig.flush() == []
    event = rig.store.add_event(
        START + timedelta(minutes=2),
        "visit",
        {"origin": "live", "weight_lb": 10.6, "duration_s": 30},
    )
    rig.store.set_user_label(event, "cat", tom)
    rig.bridge._moments.visit_saved(event)
    rig.bridge.ingest(state(), START + timedelta(minutes=3))
    assert rig.flush() == ["Tom's weight changed"]


def test_a_finished_cycle_is_announced_once(rig: Rig):
    rig.kinds(cycle_completed=True)
    rig.bridge.ingest(state(), START)
    rig.bridge.ingest(state(robotStatus=10), START + timedelta(seconds=5))
    rig.bridge.ingest(state(odometerCleanCycles=101), START + timedelta(minutes=3))
    assert rig.flush() == ["Cycle finished"]
    assert rig.relay.messages()[0]["body"] == "The drawer is 35% full."
    [cycle] = rig.store.events(limit=5, kinds=["clean_cycle"])
    assert not rig.bridge._moments.claim("clean_cycle", cycle["id"])


def test_a_control_lock_change_is_announced_but_the_first_reading_is_not(rig: Rig):
    rig.kinds(panel_lock_changed=True)
    rig.bridge.ingest(state(isKeypadLockout=0), START)
    assert rig.flush() == []
    rig.bridge.ingest(state(isKeypadLockout=1), START + timedelta(minutes=1))
    assert rig.flush() == ["Control lock on"]
    rig.restart()
    rig.bridge.ingest(state(isKeypadLockout=1), START + timedelta(minutes=2))
    assert rig.flush() == ["Control lock on"]
    rig.bridge.ingest(state(isKeypadLockout=0), START + timedelta(minutes=3))
    assert rig.flush() == ["Control lock on", "Control lock off"]
    assert [e["detail"]["locked"] for e in rig.store.events(limit=5, kinds=["panel_lock"])] == [
        False,
        True,
    ]


def test_a_new_firmware_is_announced(rig: Rig):
    rig.bridge.ingest(state(espFirmware="1.1.75"), START)
    rig.bridge.ingest(state(espFirmware="1.1.80"), START + timedelta(minutes=1))
    assert rig.flush() == ["Robot updated"]
    assert rig.relay.messages()[0]["body"] == "Firmware is now 1.1.80."


def test_power_loss_is_held_for_two_minutes_then_announced_once(rig: Rig):
    rig.bridge.ingest(state(unitPowerType=0), START)
    rig.bridge.ingest(state(unitPowerType=1), START + timedelta(seconds=30))
    rig.bridge.ingest(state(unitPowerType=1), START + timedelta(seconds=90))
    assert rig.flush() == []
    rig.bridge.ingest(state(unitPowerType=1), START + timedelta(seconds=160))
    rig.bridge.ingest(state(unitPowerType=1), START + timedelta(seconds=200))
    assert rig.flush() == ["Running on battery"]
    rig.restart()
    rig.bridge.ingest(state(unitPowerType=1), START + timedelta(seconds=260))
    assert rig.flush() == ["Running on battery"]
    keys = [e["detail"]["key"] for e in rig.store.events(limit=10, kinds=["alert"])]
    assert keys.count("power_loss") == 1


def test_a_short_battery_blip_says_nothing():
    watch = PowerWatch()
    start = START
    assert watch.update(True, start) is None
    assert watch.update(False, start + timedelta(seconds=60)) is None
    assert watch.update(True, start + timedelta(seconds=90)) is None
    assert watch.update(True, start + timedelta(seconds=150)) is None
    assert watch.update(True, start + timedelta(seconds=215)) is True
    assert watch.update(False, start + timedelta(seconds=300)) is False


def test_the_daily_summary_goes_out_once_after_the_chosen_hour(rig: Rig):
    rig.kinds(daily_summary=True)
    push_options.update(rig.store, {"daily_summary_hour": 8})
    rig.store.add_cat("Tom", 9.4, START)
    rig.store.add_cat("Kit", 13.0, START)
    walk_a_visit(rig.bridge, START)
    rig.store.add_event(
        START + timedelta(hours=1), "clean_cycle", {"count": 101, "drawer_percent": 40}
    )
    next_day = datetime(2026, 11, 9, 7, 59, tzinfo=UTC)
    rig.bridge.ingest(state(), next_day)
    assert rig.flush() == []
    rig.bridge.ingest(state(), next_day + timedelta(minutes=2))
    assert rig.flush() == ["Yesterday in the box"]
    assert rig.relay.messages()[0]["body"] == "Tom: 1 visit. 1 cycle. Drawer 40%."
    rig.restart()
    rig.bridge.ingest(state(), next_day + timedelta(minutes=10))
    assert rig.flush() == ["Yesterday in the box"]


def test_a_daily_summary_that_is_hours_late_is_skipped(rig: Rig):
    rig.kinds(daily_summary=True)
    rig.bridge.ingest(state(), START)
    rig.bridge.ingest(state(), datetime(2026, 11, 9, 14, 0, tzinfo=UTC))
    assert rig.flush() == []
    assert rig.store.get_json("push_moments")["summary_day"] == "2026-11-09"


def test_changes_in_routine_are_off_until_asked_for_and_inert_by_default(rig: Rig):
    assert rig.bridge.on_irregularity(3, "weight", "Kit eats less", "Down 20%.") is False
    assert rig.flush() == []
    rig.kinds(behaviour_change=True)
    assert rig.bridge.on_irregularity({"id": 3}, "weight", "Kit eats less", "Down 20%.") is True
    assert rig.bridge.on_irregularity(3, "visits", "Kit visits less", "Down 3.") is True
    assert rig.flush() == ["Kit eats less", "Kit visits less"]
    assert push_catalog.BY_KEY["behaviour_change"].default is False


def test_without_a_notifier_nothing_is_kept_or_sent(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    config = Config(SERIAL, "right-key", "broker", 1883, tmp_path / "h.db", web)
    store = Store(config.db_path)
    bridge = RobotBridge(config, store)
    walk_a_visit(bridge, START)
    assert store.get_json("push_moments") is None
    assert bridge.on_irregularity(1, "x", "t", "b") is False


# Per-cat choices


def test_a_cat_filter_lets_only_the_chosen_cat_through(rig: Rig):
    rig.kinds(visit=True)
    tom, kit = 1, 2
    push_options.update(rig.store, {"cats": {"visit": {"mode": "selected", "cat_ids": [tom]}}})
    assert rig.notifier.notify(Push("visit", "a", False, "Tom used the box", tom)) is True
    assert rig.notifier.notify(Push("visit", "b", False, "Kit used the box", kit)) is False
    assert rig.notifier.notify(Push("visit", "c", False, "A cat used the box", None)) is False
    assert rig.flush() == ["Tom used the box"]


def test_visits_with_no_cat_come_through_when_asked_for(rig: Rig):
    rig.kinds(visit=True)
    push_options.update(
        rig.store,
        {"cats": {"visit": {"mode": "selected", "cat_ids": [1], "include_unknown": True}}},
    )
    assert rig.notifier.notify(Push("visit", "c", False, "A cat used the box", None)) is True
    assert rig.notifier.notify(Push("visit", "b", False, "Kit used the box", 2)) is False


def test_a_visit_the_identity_is_not_sure_about_counts_as_unknown(rig: Rig):
    rig.store.add_cat("Tom", 9.4, START)
    rig.store.add_cat("Kit", 9.9, START)
    rig.kinds(visit=True)
    push_options.update(rig.store, {"cats": {"visit": {"mode": "selected", "cat_ids": [1]}}})
    walk_a_visit(rig.bridge, START, pounds=9.65)
    [visit] = rig.store.visits()
    assert visit["label"] == "unknown"
    assert rig.flush() == []


def test_the_filter_follows_the_label_at_the_time_of_the_visit(rig: Rig):
    rig.store.add_cat("Tom", 9.4, START)
    rig.store.add_cat("Kit", 13.0, START)
    rig.kinds(visit=True)
    push_options.update(rig.store, {"cats": {"visit": {"mode": "selected", "cat_ids": [2]}}})
    walk_a_visit(rig.bridge, START)
    assert rig.flush() == []
    walk_a_visit(rig.bridge, START + timedelta(minutes=10), pounds=13.0)
    assert rig.flush() == ["Kit used the box"]


# Quiet hours


def quiet(rig: Rig, mode: str = "hold") -> None:
    push_options.update(
        rig.store,
        {"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "mode": mode}},
    )


def test_quiet_hours_hold_and_merge_quiet_notices_but_not_urgent_ones(rig: Rig):
    rig.kinds(visit=True, frequent_visits=True, cycle_completed=True)
    quiet(rig)
    rig.wall[0] = datetime(2026, 11, 8, 23, 0, tzinfo=UTC)
    rig.notifier.notify(Push("visit", "35 s.", False, "Tom used the box", 1))
    rig.notifier.notify(Push("cycle_completed", "Done.", False, "Cycle finished"))
    rig.notifier.notify(Push("frequent_visits", "5 visits.", False, "Busy box"))
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    assert rig.flush() == ["Bonnet is off"]

    # A restart in the night keeps what was held.
    rig.restart()
    rig.wall[0] = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)
    rig.notifier._release_held()
    assert rig.flush() == ["Bonnet is off"]

    rig.wall[0] = datetime(2026, 11, 9, 7, 5, tzinfo=UTC)
    rig.notifier._release_held()
    assert rig.flush() == ["Bonnet is off", "While you slept"]
    summary = rig.relay.messages()[-1]
    assert summary["body"] == "Tom used the box; Cycle finished; Busy box"
    rig.notifier._release_held()
    assert rig.flush() == ["Bonnet is off", "While you slept"]


def test_a_single_held_notice_comes_back_as_itself(rig: Rig):
    rig.kinds(cycle_completed=True)
    quiet(rig)
    rig.wall[0] = datetime(2026, 11, 8, 23, 0, tzinfo=UTC)
    rig.notifier.notify(Push("cycle_completed", "Done.", False, "Cycle finished"))
    rig.wall[0] = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
    rig.notifier._release_held()
    assert rig.flush() == ["Cycle finished"]


def test_quiet_hours_can_drop_instead_of_hold(rig: Rig):
    rig.kinds(cycle_completed=True)
    quiet(rig, "drop")
    rig.wall[0] = datetime(2026, 11, 8, 23, 0, tzinfo=UTC)
    rig.notifier.notify(Push("cycle_completed", "Done.", False, "Cycle finished"))
    rig.wall[0] = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    rig.notifier._release_held()
    assert rig.flush() == []


def test_quiet_hours_use_the_local_time_zone(rig: Rig):
    rig.kinds(cycle_completed=True)
    quiet(rig)
    rig.notifier._tz = ZoneInfo("America/New_York")
    rig.wall[0] = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # 07:00 in New York: just ended
    rig.notifier.notify(Push("cycle_completed", "Done.", False, "Cycle finished"))
    assert rig.flush() == ["Cycle finished"]
    rig.wall[0] = datetime(2026, 11, 8, 11, 0, tzinfo=UTC)  # 06:00 in New York: still quiet
    rig.notifier.notify(Push("cycle_completed", "Done.", False, "Cycle finished"))
    assert rig.flush() == ["Cycle finished"]
    assert len(rig.store.get_json(push.HELD_KEY)) == 1


# Delivery


def test_sends_are_paced_under_the_relay_limit(rig: Rig):
    urgent = [
        "globe_fault",
        "stuck_cycle",
        "excess_weight",
        "drawer_full_lockout",
        "bonnet_removed",
        "cat_sensor_blocked",
        "offline",
        "power_loss",
    ]
    for kind in urgent:
        rig.notifier.notify(Push(kind, "x", True, kind))
    rig.flush()
    assert len(rig.relay.messages()) == 8
    stamps = [at for at, _ in rig.relay.sends]
    for at in stamps:
        in_window = [t for t in stamps if at <= t < at + 60]
        assert len(in_window) <= push.SENDS_PER_MINUTE
    assert rig.sleeps


def test_a_burst_of_quiet_notices_is_folded_into_one(rig: Rig):
    rig.kinds(visit=True)
    for cat in range(1, 7):
        rig.notifier.notify(Push("visit", "35 s.", False, f"Cat {cat} used the box", cat))
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    assert rig.flush() == ["Bonnet is off", "6 updates"]


def test_a_few_quiet_notices_are_not_folded(rig: Rig):
    rig.kinds(visit=True)
    for cat in range(1, 4):
        rig.notifier.notify(Push("visit", "35 s.", False, f"Cat {cat} used the box", cat))
    assert len(rig.flush()) == 3


def test_a_rate_limited_push_is_tried_once_more(rig: Rig):
    rig.relay.statuses = [429, 200]
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    rig.flush()
    assert [len(p["messages"]) for _, p in rig.relay.sends] == [1, 1]
    assert rig.sleeps == [push.RETRY_AFTER_SECONDS]
    assert rig.relay.sends[-1][0] >= push.RETRY_AFTER_SECONDS


def test_a_push_the_relay_keeps_refusing_is_given_up_after_one_retry(rig: Rig):
    rig.relay.statuses = [429, 429, 200]
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    rig.flush()
    assert len(rig.relay.sends) == 2
    assert not rig.notifier._pending


def test_a_failing_relay_is_also_asked_twice(rig: Rig):
    rig.relay.statuses = [503, 200]
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    rig.flush()
    assert len(rig.relay.sends) == 2


def test_urgent_notices_go_first(rig: Rig):
    rig.kinds(visit=True)
    rig.notifier.notify(Push("visit", "x", False, "A cat used the box", 1))
    rig.notifier.notify(Push("bonnet_removed", "Off.", True, "Bonnet is off"))
    assert rig.flush() == ["Bonnet is off", "A cat used the box"]


def test_titles_and_bodies_are_cut_to_the_relay_limits_by_bytes(rig: Rig):
    rig.kinds(visit=True)
    name = "\U0001f408" * 300
    rig.notifier.notify(Push("visit", "é" * 2000, False, f"{name} used the box", 7))
    rig.flush()
    [message] = rig.relay.messages()
    assert len(message["title"].encode()) <= 256
    assert len(message["body"].encode()) <= 1024
    assert len(message["collapseId"].encode()) <= 64
    message["title"].encode("utf-8")
    message["body"].encode("utf-8")


def test_a_long_cat_name_is_shortened_in_the_title():
    title, _ = push_catalog.visit_text("visit", "N" * 80, {"duration_s": 30})
    assert title == "N" * 19 + "…" + " used the box"


def test_a_filling_drawer_is_one_push_when_the_steps_come_together(rig: Rig):
    for kind in ("drawer_level", "drawer_full", "drawer_full_lockout"):
        rig.notifier.notify(Push(kind, "x", kind == "drawer_full_lockout", kind))
    assert rig.flush() == ["drawer_full_lockout"]
    assert rig.relay.messages()[0]["collapseId"] == "drawer"


def test_a_drawer_notice_no_worse_than_the_last_waits_for_the_drawer_to_clear(rig: Rig):
    rig.notifier.notify(Push("drawer_full", "x", False, "Waste drawer is full"))
    rig.flush()
    rig.notifier.notify(Push("drawer_level", "x", False, "Drawer filling up"))
    rig.notifier.notify(Push("drawer_full", "x", False, "Waste drawer is full"))
    assert not rig.notifier._pending
    rig.notifier.cleared("drawer_full")
    rig.notifier.notify(Push("drawer_level", "x", False, "Drawer filling up"))
    assert len(rig.notifier._pending) == 1


def test_a_worse_drawer_step_still_comes_through_later(rig: Rig):
    rig.notifier.notify(Push("drawer_level", "x", False, "Drawer filling up"))
    rig.flush()
    rig.notifier.notify(Push("drawer_full_lockout", "x", True, "Robot stopped, drawer full"))
    assert rig.flush() == ["Drawer filling up", "Robot stopped, drawer full"]


# Turning a kind on while its alert is active


def test_a_kind_switched_on_while_its_alert_is_active_is_sent_once(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.kinds(drawer_full=False, drawer_level=False)
    rig.bridge.ingest(state(), START)
    rig.bridge.ingest(state(isDFIFull=1, DFILevelPercent=100), START + timedelta(minutes=1))
    assert rig.flush() == []
    app = create_app(rig.config, rig.store, rig.bridge, run_bridge=False, notifier=rig.notifier)
    with TestClient(app) as client:
        on = {"kinds": {"drawer_full": True}}
        client.patch("/api/push/kinds", headers=AUTH, json=on)
        assert rig.flush() == ["Waste drawer is full"]
        client.patch("/api/push/kinds", headers=AUTH, json={"kinds": {"drawer_full": False}})
        client.patch("/api/push/kinds", headers=AUTH, json=on)
        assert rig.flush() == ["Waste drawer is full"]


def test_a_kind_switched_on_with_no_active_alert_sends_nothing(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.kinds(drawer_full=False)
    rig.bridge.ingest(state(), START)
    app = create_app(rig.config, rig.store, rig.bridge, run_bridge=False, notifier=rig.notifier)
    with TestClient(app) as client:
        client.patch("/api/push/kinds", headers=AUTH, json={"kinds": {"drawer_full": True}})
    assert rig.flush() == []


def test_an_alert_announced_before_a_restart_is_not_announced_again(rig: Rig):
    rig.bridge.ingest(state(), START)
    rig.bridge.ingest(state(isDFIFull=1, DFILevelPercent=100), START + timedelta(minutes=1))
    assert rig.flush() == ["Waste drawer is full"]
    rig.restart()
    rig.bridge.ingest(state(isDFIFull=1, DFILevelPercent=100), START + timedelta(minutes=5))
    rig.bridge.announce_active({"drawer_full"})
    assert rig.flush() == ["Waste drawer is full"]


# The catalog and options API


@pytest.fixture
def client(tmp_path: Path):
    rig = Rig(tmp_path)
    app = create_app(rig.config, rig.store, rig.bridge, run_bridge=False, notifier=rig.notifier)
    with TestClient(app) as test_client:
        test_client.rig = rig  # type: ignore[attr-defined]
        yield test_client


def test_the_catalog_lists_every_alert_the_server_can_raise(client: TestClient):
    view = client.get("/api/push", headers=AUTH).json()
    keys = {kind["key"] for kind in view["catalog"]}
    # The library can name an alert the robot never reports, which is not offered as a choice.
    assert {a.value for a in alerts.Alert} - {"hopper_disconnected"} <= keys
    assert set(view["kinds"]) == keys == set(view["defaults"])
    assert view["groups"][0] == "Needs you now"
    assert {kind["group"] for kind in view["catalog"]} <= set(view["groups"])


def test_the_catalog_says_what_is_urgent_and_what_has_a_threshold(client: TestClient):
    catalog = {k["key"]: k for k in client.get("/api/push", headers=AUTH).json()["catalog"]}
    assert catalog["bonnet_removed"]["urgent"] is True
    assert catalog["drawer_full"]["urgent"] is False
    assert catalog["visit"]["supports_cats"] is True
    assert catalog["drawer_level"]["threshold"] == {
        "setting": "drawer_level_percent",
        "unit": "%",
        "min": 50,
        "max": 95,
    }
    ranges = {
        key: (entry["threshold"]["min"], entry["threshold"]["max"])
        for key, entry in catalog.items()
        if entry["threshold"]
    }
    assert ranges["litter_low"] == (10, 50)
    assert ranges["long_visit"] == (2, 5)
    assert ranges["frequent_visits"] == (3, 10)
    assert ranges["no_visit"] == (1, 720)


def test_a_catalog_threshold_matches_what_the_settings_api_accepts(client: TestClient):
    for entry in client.get("/api/push", headers=AUTH).json()["catalog"]:
        threshold = entry["threshold"]
        if not threshold:
            continue
        for value, ok in (
            (threshold["min"], True),
            (threshold["max"], True),
            (threshold["min"] - 1, False),
            (threshold["max"] + 1, False),
        ):
            response = client.patch(
                "/api/alert-settings", headers=AUTH, json={threshold["setting"]: value}
            )
            assert (response.status_code == 200) is ok, (entry["key"], value)


def test_kinds_that_need_a_hopper_or_upkeep_are_marked_unavailable_until_seen(client: TestClient):
    catalog = {k["key"]: k for k in client.get("/api/push", headers=AUTH).json()["catalog"]}
    assert catalog["hopper_empty"]["available"] is False
    assert catalog["filter_due"]["available"] is False
    assert catalog["drawer_full"]["available"] is True


def test_quiet_hours_and_cat_choices_are_saved_and_served(client: TestClient):
    rig = client.rig  # type: ignore[attr-defined]
    tom = rig.store.add_cat("Tom", 9.4, START)
    body = {
        "quiet_hours": {"enabled": True, "start": "23:00", "end": "06:30"},
        "cats": {"visit": {"mode": "selected", "cat_ids": [tom], "include_unknown": True}},
        "daily_summary_hour": 9,
    }
    response = client.patch("/api/push/options", headers=AUTH, json=body)
    assert response.status_code == 200
    options = response.json()["options"]
    assert options["quiet_hours"] == {
        "enabled": True,
        "start": "23:00",
        "end": "06:30",
        "mode": "hold",
    }
    assert options["cats"]["visit"] == {
        "mode": "selected",
        "cat_ids": [tom],
        "include_unknown": True,
    }
    assert options["cats"]["long_visit"]["mode"] == "any"
    assert options["daily_summary_hour"] == 9
    assert client.get("/api/push", headers=AUTH).json()["options"] == options
    assert response.json()["cats"] == [{"id": tom, "name": "Tom"}]


def test_the_defaults_for_options_are_off_and_everyone(client: TestClient):
    options = client.get("/api/push", headers=AUTH).json()["options"]
    assert options["quiet_hours"]["enabled"] is False
    assert options["cats"]["visit"] == {"mode": "any", "cat_ids": [], "include_unknown": False}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"quiet_hours": {"start": "25:00"}},
        {"quiet_hours": {"start": "22:00", "end": "22:00"}},
        {"quiet_hours": {"mode": "later"}},
        {"cats": {"visit": {"mode": "selected", "cat_ids": []}}},
        {"cats": {"visit": {"mode": "selected", "cat_ids": [99]}}},
        {"cats": {"drawer_full": {"mode": "any"}}},
        {"daily_summary_hour": 24},
        {"daily_summary_hour": None},
        {"nonsense": 1},
    ],
)
def test_bad_options_are_refused(client: TestClient, body):
    assert client.patch("/api/push/options", headers=AUTH, json=body).status_code == 422


def test_options_need_the_access_key_and_a_relay(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    config = Config(SERIAL, "right-key", "broker", 1883, tmp_path / "h.db", web)
    store = Store(config.db_path)
    with TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False)) as c:
        assert c.patch("/api/push/options", json={"daily_summary_hour": 9}).status_code == 401
        assert (
            c.patch("/api/push/options", headers=AUTH, json={"daily_summary_hour": 9}).status_code
            == 404
        )


def test_unusable_stored_options_fall_back_to_the_defaults(store_path: Path):
    store = Store(store_path)
    store.put_json(
        push_options.KEY,
        {
            "quiet_hours": {"start": "x", "end": 4, "mode": "later"},
            "cats": 5,
            "daily_summary_hour": 99,
        },
    )
    options = push_options.load(store)
    assert options["quiet_hours"] == push_options.DEFAULT_QUIET
    assert options["daily_summary_hour"] == push_options.DEFAULT_SUMMARY_HOUR


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "options.db"


_WORDED_ELSEWHERE = {
    "visit",
    "long_visit",
    "weight_change",
    "panel_lock_changed",
    "daily_summary",
    "behaviour_change",
}


def test_visit_wording_fits_the_lock_screen_with_a_long_name():
    detail = {"duration_s": 3599, "weight_lb": 12.34, "usual_lb": 11.0, "change_percent": 12}
    for kind in ("visit", "long_visit", "weight_change"):
        title, body = push_catalog.visit_text(kind, "N" * 60, detail)
        assert len(title) <= 40 and len(body) <= 120
    assert push_catalog.lock_text(True) == ("Control lock on", "The panel is now locked.")


def test_every_kind_has_words_for_the_lock_screen():
    for kind in push_catalog.CATALOG:
        if kind.key in _WORDED_ELSEWHERE:
            continue
        title, body = push_catalog.alert_text(
            kind.key, {"level": 80, "hours": 24, "cause": "pinch", "visits": 5, "version": "1"}
        )
        assert title, kind.key
        assert len(title) <= 40, kind.key
        assert len(body) <= 120, kind.key
        assert "\u2014" not in title + body

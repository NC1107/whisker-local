"""Telling cats apart by weight: when to name one, when to say not sure, and drifting cats."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from whisker_local import identity
from whisker_local.cats import CatTracker
from whisker_local.identity import Reading, Seed, Tracker, assign, decide
from whisker_local.store import Store

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def trackers(a: float = 10.25, b: float = 8.7) -> list[Tracker]:
    return [Tracker(1, a, 0.35), Tracker(2, b, 0.4)]


def test_a_reading_in_the_middle_of_a_cat_is_named():
    verdict = decide(10.3, trackers(), {1: 30, 2: 48})
    assert (verdict.status, verdict.cat_id) == ("confident", 1)
    assert verdict.confidence is not None and verdict.confidence >= identity.CONFIDENCE


def test_a_reading_in_the_valley_between_the_cats_is_not_sure_and_names_nobody():
    verdict = decide(9.6, trackers(), {1: 30, 2: 48})
    assert (verdict.status, verdict.cat_id) == ("unsure", None)
    assert verdict.candidate_id in (1, 2)
    assert verdict.confidence is not None and verdict.confidence < identity.CONFIDENCE


@pytest.mark.parametrize("weight", [5.8, 12.4, 17.5, 3.0])
def test_a_reading_that_fits_no_cat_is_off_cluster(weight: float):
    verdict = decide(weight, trackers(), {1: 30, 2: 48})
    assert (verdict.status, verdict.cat_id) == ("off_cluster", None)


def test_a_cat_alone_is_named_for_readings_near_it_and_not_for_a_stranger():
    only = [Tracker(1, 10.0, 0.4)]
    assert decide(10.4, only, {}).status == "confident"
    assert decide(14.0, only, {}).status == "off_cluster"


def test_cats_closer_than_a_pound_cannot_be_split_by_weight():
    close = [Tracker(1, 10.0, 0.4), Tracker(2, 10.6, 0.4)]
    assert decide(10.0, close, {}).status == "unsure"
    assert decide(10.3, close, {}).status == "unsure"


def test_no_cats_means_nothing_to_name():
    assert decide(10.0, [], {}).cat_id is None


def reading(index: int, day: float, weight: float | None, user=None) -> Reading:
    return Reading(index, START + timedelta(days=day), weight, user)


def test_a_hand_label_always_wins_and_is_not_second_guessed():
    seeds = [Seed(1, 10.25), Seed(2, 8.7)]
    readings = [reading(1, 0, 8.7), reading(2, 1, 10.25), reading(3, 2, 8.7, ("cat", 1))]

    result = assign(readings, seeds, {})

    assert result.verdicts[3].status == "user"
    assert result.verdicts[3].cat_id == 1
    assert result.verdicts[1].cat_id == 2


def test_a_visit_the_owner_said_was_not_a_cat_is_left_out():
    result = assign([reading(1, 0, 8.7, ("not_cat", None))], [Seed(1, 8.7)], {})
    assert 1 not in result.verdicts


def test_a_visit_with_no_weight_is_kept_with_an_unknown_cat():
    result = assign([reading(1, 0, None)], [Seed(1, 8.7)], {})
    assert (result.verdicts[1].status, result.verdicts[1].cat_id) == ("no_weight", None)


def test_the_tracker_follows_a_cat_moving_away_from_the_other_but_slowly_toward_it():
    away = Tracker(1, 10.0, 0.35, visits=identity.LEARNING_VISITS)
    toward = Tracker(1, 10.0, 0.35, visits=identity.LEARNING_VISITS)
    for index in range(40):
        when = START + timedelta(days=index / 3)
        away.learn(when, 11.0, [8.0], user=False)
        toward.learn(when, 9.0, [8.0], user=False)
    # Thirteen days: the slow cap allows about 0.1 lb toward the other cat.
    assert away.mean > 10.1
    assert toward.mean > 9.85


def test_the_anchor_is_the_median_of_the_first_readings_and_then_frozen():
    seeds = [Seed(1, 10.0)]
    readings = [reading(i, i * 0.5, 10.0 + (i % 3 - 1) * 0.2) for i in range(1, 14)]
    readings += [reading(100 + i, 20 + i, 7.0 + i * 0.05) for i in range(30)]

    result = assign(readings, seeds, {})

    anchor = result.anchors[1]
    assert anchor.weight_lb == pytest.approx(10.0, abs=0.15)
    assert anchor.source == "first readings"
    again = assign(readings, seeds, result.anchors)
    assert again.anchors[1] == anchor
    assert again.new_anchors == {}


def test_readings_in_an_unreliable_stretch_are_named_but_do_not_teach():
    seeds = [Seed(1, 10.0)]
    readings = [reading(i, i * 0.5, 10.0) for i in range(1, 14)]
    readings += [reading(100 + i, 10 + i * 0.1, 11.2) for i in range(1, 30)]

    result = assign(readings, seeds, {}, trusted=lambda at: at < START + timedelta(days=10))

    assert result.trackers[1].mean < 10.5


# --- the simulations from the audit, run through the real tracker and store.

A_WEIGHT, B_WEIGHT = 10.25, 8.7
A_SD, B_SD = 0.31, 0.37
A_RATE, B_RATE = 1.3, 2.0
DAYS = 70


def poisson(rng: random.Random, rate: float) -> int:
    count, limit, product = 0, pow(2.718281828, -rate), rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


class Household:
    """Two cats using one box for DAYS days, one of whom changes weight."""

    def __init__(self, tmp_path: Path, a_weight_on_day) -> None:
        self.store = Store(tmp_path / "sim.db")
        self.clock = {"now": START}
        self.tracker = CatTracker(self.store, clock=lambda: self.clock["now"])
        self.store.add_presence(START - timedelta(days=1), True)
        self.a = self.tracker.create_cat("A", 10.1)["id"]
        self.b = self.tracker.create_cat("B", 9.0)["id"]
        self.truth: dict[int, str] = {}
        self.day_of: dict[int, int] = {}
        rng = random.Random(11)
        for day in range(DAYS):
            for cat, rate, mean, spread in (
                ("A", A_RATE, a_weight_on_day(day), A_SD),
                ("B", B_RATE, B_WEIGHT, B_SD),
            ):
                for _ in range(poisson(rng, rate)):
                    at = START + timedelta(days=day, hours=rng.uniform(-12, 11.5))
                    self.clock["now"] = max(self.clock["now"], at)
                    event = self.store.add_event(
                        at, "visit", {"weight_lb": round(rng.gauss(mean, spread), 2)}
                    )
                    self.truth[event] = cat
                    self.day_of[event] = day
                    self.tracker.on_visit_saved(event)
        self.clock["now"] = START + timedelta(days=DAYS + 1)

    def named_for(self, cat: str, since_day: int) -> dict[str, int]:
        """What the visits of one real cat after a day were named, as counts."""
        counts: dict[str, int] = {}
        for visit in self.store.visits():
            if self.truth[visit["id"]] != cat or self.day_of[visit["id"]] < since_day:
                continue
            if visit["label"] == "cat":
                name = "A" if visit["cat_id"] == self.a else "B"
            else:
                name = "not sure"
            counts[name] = counts.get(name, 0) + 1
        return counts


def test_a_stable_pair_is_named_correctly_and_most_visits_are_named(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT)

    a, b = house.named_for("A", 14), house.named_for("B", 14)

    assert a.get("B", 0) == 0 and b.get("A", 0) == 0
    assert a.get("A", 0) / sum(a.values()) > 0.8
    assert b.get("B", 0) / sum(b.values()) > 0.8


def test_a_cat_losing_a_quarter_of_its_weight_is_never_named_as_the_other_cat(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT - 0.04 * max(0, day - 7))

    lost = house.named_for("A", 40)

    assert sum(lost.values()) > 20
    assert lost.get("B", 0) == 0
    assert lost.get("not sure", 0) / sum(lost.values()) > 0.9


def test_a_sudden_twelve_percent_drop_is_never_named_as_the_other_cat(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT if day < 25 else A_WEIGHT - 1.2)

    dropped = house.named_for("A", 35)

    assert sum(dropped.values()) > 20
    assert dropped.get("B", 0) == 0


def test_the_anchor_does_not_follow_a_cat_that_loses_weight(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT - 0.04 * max(0, day - 7))

    summaries = {cat["id"]: cat["summary"] for cat in house.tracker.cats()}

    assert summaries[house.a]["anchor_weight_lb"] > A_WEIGHT - 0.5
    assert summaries[house.b]["anchor_weight_lb"] == pytest.approx(B_WEIGHT, abs=0.3)
    assert summaries[house.a]["usual_weight_lb"] == summaries[house.a]["anchor_weight_lb"]


def test_the_other_cats_tracker_is_not_dragged_by_the_cat_that_changed(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT if day < 25 else A_WEIGHT - 1.2)

    result = house.tracker._run(house.store.replay_rows())

    assert result.trackers[house.b].mean == pytest.approx(B_WEIGHT, abs=0.2)


def test_the_household_is_told_which_cat_has_gone_quiet(tmp_path: Path):
    house = Household(tmp_path, lambda day: A_WEIGHT if day < 25 else A_WEIGHT - 1.2)

    overview = house.tracker.identity_overview(days=30)

    assert [s["cat_id"] for s in overview["silent_cats"]] == [house.a]
    assert overview["unsettled"] > 0
    assert overview["not_sure"] >= overview["unsettled"]

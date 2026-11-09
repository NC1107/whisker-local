"""Replays invented households through the checks as they would have run on each day."""

from __future__ import annotations

from datetime import date, timedelta

from health_data import NOW, CatPlan, add_visit, build

from whisker_local.health import HealthService


def fired(world, prefix: str) -> list[str]:
    return [
        day
        for key, days in world.service.backtest().items()
        if key.startswith(prefix)
        for day in days
    ]


def test_a_stable_household_never_fires_over_eight_months(tmp_path):
    world = build(tmp_path, [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)], days=240)

    assert world.service.backtest() == {}


def test_a_real_weight_drop_fires_after_the_gates_and_not_before(tmp_path):
    drop = 150
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0, step=(drop, 0.93))],
        days=240,
    )

    days = fired(world, "weight_change:Bo")

    assert days, "a seven percent step down has to be noticed"
    onset = world.day(drop)
    assert date.fromisoformat(days[0]) > onset
    assert date.fromisoformat(days[0]) <= onset + timedelta(days=28)
    assert fired(world, "weight_change:Ada") == []


def test_the_weight_note_says_what_changed_and_since_when(tmp_path):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0, step=(170, 0.92)), CatPlan("Bo", 9.0)],
        days=240,
    )

    notes = world.service.notes()["notes"]
    weight = next(n for n in notes if n["kind"] == "weight_change")

    assert weight["cat_id"] == world.cat_ids["Ada"]
    assert "percent lower than the usual 10.0 lb" in weight["body"]
    assert date.fromisoformat(weight["since"]) >= world.day(170)
    assert "diagnos" not in weight["body"].lower() and "—" not in weight["body"]


def test_a_doubling_of_visits_is_noticed_for_the_household(tmp_path):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0, busier=(170, 2)), CatPlan("Bo", 9.0, busier=(170, 2))],
        days=240,
    )

    household = fired(world, "household_rate")

    assert household
    assert world.day(170) < date.fromisoformat(household[0]) <= world.day(170) + timedelta(days=14)


def test_a_doubling_of_one_cats_visits_is_noticed_for_that_cat_only(tmp_path):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0, busier=(170, 2))],
        days=240,
    )

    cat = fired(world, "cat_rate:Bo")

    assert cat and date.fromisoformat(cat[0]) > world.day(170)
    assert fired(world, "cat_rate:Ada") == []


def test_days_with_no_data_do_not_look_like_a_drop_in_visits(tmp_path):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)],
        days=240,
        offline=(150, 156),
    )

    assert world.service.backtest() == {}
    note_kinds = {n["kind"] for n in world.service.notes()["notes"]}
    assert note_kinds == set()


def test_a_long_quiet_spell_with_the_robot_online_fires_silence_once_it_is_long(tmp_path):
    quiet = (NOW - timedelta(days=2, hours=2), NOW)
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)],
        days=60,
        quiet=quiet,
    )

    notes = world.service.notes()["notes"]

    silence = next(n for n in notes if n["kind"] == "silence")
    assert "while the robot was online" in silence["body"]
    assert "sick" not in silence["body"].lower()


def test_the_same_quiet_spell_is_silent_when_the_robot_was_offline(tmp_path):
    quiet = (NOW - timedelta(days=2, hours=2), NOW)
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)],
        days=60,
        quiet=quiet,
    )
    world.store.add_presence(quiet[0] + timedelta(hours=3), False)
    world.service.forget()

    assert [n for n in world.service.notes()["notes"] if n["kind"] == "silence"] == []


def test_a_burst_of_short_visits_by_a_male_cat_is_urgent_and_names_the_vet(tmp_path):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0, sex="neutered_male", per_day=(1,)), CatPlan("Bo", 9.0)],
        days=60,
    )
    # Nothing else happened in the last day, so the burst is the only thing to find.
    for n in range(5):
        add_visit(world, NOW - timedelta(minutes=170 - n * 30), "Ada", 10.0, 12)
    world.service.forget()

    burst = next(n for n in world.service.notes()["notes"] if n["kind"] == "burst")

    assert burst["level"] == "urgent"
    assert "call a vet now" in burst["body"]
    assert "not a diagnosis" in burst["body"]


def test_a_burst_without_durations_cannot_be_judged_and_stays_quiet(tmp_path):
    world = build(
        tmp_path, [CatPlan("Ada", 10.0, sex="male", per_day=(1,)), CatPlan("Bo", 9.0)], days=60
    )
    for n in range(5):
        add_visit(world, NOW - timedelta(minutes=170 - n * 30), "Ada", 10.0, None)
    world.service.forget()

    assert [n for n in world.service.notes()["notes"] if n["kind"] == "burst"] == []


def test_on_irregularity_is_called_once_per_fired_check_and_logs_only(tmp_path, caplog):
    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0, step=(170, 0.92)), CatPlan("Bo", 9.0)],
        days=240,
    )
    calls = []
    service = HealthService(
        world.store, world.tracker, clock=lambda: NOW, notify=lambda *args: calls.append(args)
    )

    first = service.check()
    second = service.check()

    kinds = [call[1] for call in calls]
    assert kinds.count("weight_change") == 1
    assert next(call for call in calls if call[1] == "weight_change")[0]["name"] == "Ada"
    assert len(first) == len(calls) and second == []


def test_the_default_hook_only_logs(tmp_path, caplog):
    import logging

    world = build(
        tmp_path,
        [CatPlan("Ada", 10.0, step=(170, 0.92)), CatPlan("Bo", 9.0)],
        days=240,
    )

    with caplog.at_level(logging.INFO):
        world.service.check()

    assert "irregularity weight_change" in caplog.text


def test_waiting_items_say_how_much_more_history_is_needed(tmp_path):
    world = build(tmp_path, [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)], days=10)

    waiting = world.service.notes()["waiting"]

    texts = " ".join(item["text"] for item in waiting)
    assert "needs about" in texts and "of data" in texts
    assert not any(
        item["ready"] for item in waiting if item["kind"] in ("cat_rate", "weight_change")
    )
    assert world.service.notes()["notes"] == []

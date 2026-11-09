from __future__ import annotations

import random
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from whisker_local import cats as cats_module
from whisker_local.cats import (
    CatArchived,
    CatNameTaken,
    CatNotFound,
    CatTracker,
    VisitNotFound,
    find_clusters,
)
from whisker_local.store import Store

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "history.db")
    # The robot has been heard since October, so a day without a visit is a real zero.
    store.add_presence(datetime(2026, 10, 1, tzinfo=UTC), True)
    return store


@pytest.fixture
def tracker(store: Store) -> CatTracker:
    return CatTracker(store, clock=lambda: NOW)


def visit(
    store: Store,
    tracker: CatTracker,
    weight: float | None,
    *,
    ago: timedelta = timedelta(hours=1),
    duration_s: int | None = 40,
) -> int:
    at = NOW - ago
    event_id = store.add_event(
        at,
        "visit",
        {
            "started": (at - timedelta(seconds=duration_s or 0)).isoformat(),
            "duration_s": duration_s,
            "weight_lb": weight,
        },
    )
    tracker.on_visit_saved(event_id)
    return event_id


def label_of(store: Store, event_id: int) -> tuple[str, int | None, str]:
    found = store.visit(event_id)
    assert found is not None
    return found["label"], found["cat_id"], found["label_source"]


def test_no_cats_at_all_leaves_everything_unknown(store: Store, tracker: CatTracker):
    ids = [visit(store, tracker, 10.0, ago=timedelta(hours=h)) for h in range(1, 6)]
    assert all(label_of(store, i) == ("unknown", None, "auto") for i in ids)
    assert tracker.needing_cat() == 5
    assert tracker.cats() == []
    suggestions = tracker.suggestions()
    assert suggestions["enough_data"] is False
    assert [c["visits"] for c in suggestions["clusters"]] == [5]


def test_no_visits_at_all_is_quiet(store: Store, tracker: CatTracker):
    assert tracker.needing_cat() == 0
    assert tracker.suggestions()["visits_considered"] == 0
    assert tracker.suggestions()["clusters"] == []
    cat = tracker.create_cat("Miso", 10.0)
    assert cat["summary"] == {
        "latest_weight_lb": None,
        "usual_weight_lb": 10.0,
        "anchor_weight_lb": None,
        "recent_weight_lb": None,
        "recent_readings": 0,
        "visits_today": 0,
        "visits_per_day": None,
        "avg_visit_s": None,
        "weight_change_pct": None,
        "last_visit_at": None,
        "yesterday_weight_lb": None,
        "yesterday_visits": 0,
        "yesterday_minutes": 0.0,
        "yesterday_coverage": "full",
        "week_visits": 0,
        "week_days_with_data": 7,
        "common_visit_hour": None,
    }


def test_two_cats_close_in_weight_are_not_split_by_weight_alone(store: Store, tracker: CatTracker):
    tracker.create_cat("Miso", 10.0)
    tracker.create_cat("Pip", 10.6)

    for weight in (10.0, 10.6, 10.3, 10.05):
        assert label_of(store, visit(store, tracker, weight)) == ("unknown", None, "auto")
    assert tracker.needing_cat() == 4


def test_two_cats_a_pound_and_a_half_apart_are_named_when_clear_and_not_when_between(
    store: Store, tracker: CatTracker
):
    miso = tracker.create_cat("Miso", 10.25)["id"]
    pip = tracker.create_cat("Pip", 8.7)["id"]

    assert label_of(store, visit(store, tracker, 10.3)) == ("cat", miso, "auto")
    assert label_of(store, visit(store, tracker, 8.6)) == ("cat", pip, "auto")
    between = visit(store, tracker, 9.5)
    assert label_of(store, between) == ("unknown", None, "auto")
    assert store.visit(between)["identity"]["status"] == "unsure"
    assert tracker.needing_cat() == 1


def test_a_visit_that_fits_nobody_is_off_cluster_and_waits_for_the_owner(
    store: Store, tracker: CatTracker
):
    tracker.create_cat("Miso", 10.25)
    tracker.create_cat("Pip", 8.7)

    stranger = visit(store, tracker, 17.5)

    assert label_of(store, stranger)[0] == "unknown"
    assert store.visit(stranger)["identity"]["status"] == "off_cluster"


def test_a_visit_between_two_cats_waits_for_the_owner_and_then_counts_as_evidence(
    store: Store, tracker: CatTracker
):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    tracker.create_cat("Pip", 11.0)
    between = visit(store, tracker, 10.5)
    assert label_of(store, between)[0] == "unknown"
    assert store.visit(between)["identity"]["status"] == "unsure"

    tracker.label_visit(between, "cat", miso)

    assert label_of(store, between) == ("cat", miso, "user")
    assert tracker.needing_cat() == 0


def test_visits_without_a_weight_have_no_cat_and_do_not_nag(store: Store, tracker: CatTracker):
    tracker.create_cat("Miso", 10.0)
    bare = visit(store, tracker, None)
    assert label_of(store, bare) == ("unknown", None, "auto")
    assert store.visit(bare)["identity"]["status"] == "no_weight"
    assert tracker.needing_cat() == 0
    assert tracker.latest_unassigned_visit() is None


def test_a_user_correction_survives_every_rerun(store: Store, tracker: CatTracker):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    pip = tracker.create_cat("Pip", 13.0)["id"]
    wrongly = visit(store, tracker, 10.1, ago=timedelta(days=2))
    assert label_of(store, wrongly) == ("cat", miso, "auto")

    tracker.label_visit(wrongly, "cat", pip)
    tracker.create_cat("Newcomer", 7.0)
    tracker.update_cat(miso, name="Misu")
    tracker.update_cat(pip, archived=True)
    tracker.update_cat(pip, archived=False)
    tracker.relabel(NOW - timedelta(days=60))

    assert label_of(store, wrongly) == ("cat", pip, "user")


def test_not_a_cat_is_kept_and_unknown_hands_the_visit_back_to_auto(
    store: Store, tracker: CatTracker
):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    event = visit(store, tracker, 10.0)
    tracker.label_visit(event, "not_cat", None)
    assert label_of(store, event) == ("not_cat", None, "user")
    tracker.create_cat("Pip", 15.0)
    assert label_of(store, event) == ("not_cat", None, "user")

    tracker.label_visit(event, "unknown", None)

    assert label_of(store, event) == ("cat", miso, "auto")


def test_archiving_a_cat_frees_its_auto_visits_but_not_the_ones_the_user_chose(
    store: Store, tracker: CatTracker
):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    auto = visit(store, tracker, 10.0, ago=timedelta(days=1))
    chosen = visit(store, tracker, 14.0, ago=timedelta(days=1, hours=1))
    tracker.label_visit(chosen, "cat", miso)

    tracker.update_cat(miso, archived=True)

    assert label_of(store, auto) == ("unknown", None, "auto")
    assert label_of(store, chosen) == ("cat", miso, "user")
    assert tracker.cats() == []
    assert [c["id"] for c in tracker.cats(include_archived=True)] == [miso]


def test_restoring_a_cat_picks_its_visits_up_again(store: Store, tracker: CatTracker):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    tracker.update_cat(miso, archived=True)
    event = visit(store, tracker, 10.0)
    assert label_of(store, event)[0] == "unknown"

    tracker.update_cat(miso, archived=False)

    assert label_of(store, event) == ("cat", miso, "auto")


def test_a_new_cat_claims_recent_unlabelled_visits(store: Store, tracker: CatTracker):
    event = visit(store, tracker, 9.0, ago=timedelta(days=3))
    assert label_of(store, event)[0] == "unknown"
    cat = tracker.create_cat("Late", 9.1)
    assert label_of(store, event) == ("cat", cat["id"], "auto")


def test_rerun_leaves_history_older_than_the_window_alone(store: Store, tracker: CatTracker):
    old = visit(store, tracker, 10.0, ago=timedelta(days=cats_module.RECENT_DAYS + 5))
    tracker.create_cat("Miso", 10.0)
    assert label_of(store, old)[0] == "unknown"


def test_a_cat_gaining_weight_over_months_is_followed(store: Store):
    clock = {"now": NOW - timedelta(days=130)}
    tracker = CatTracker(store, clock=lambda: clock["now"])
    rng = random.Random(7)
    cat = tracker.create_cat("Heavy", 10.0)["id"]

    ids = []
    for day in range(120):
        for hour in (7, 13, 19):
            at = clock["now"] + timedelta(days=day, hours=hour - 12)
            weight = 10.0 + 0.02 * day + rng.uniform(-0.25, 0.25)
            event_id = store.add_event(at, "visit", {"duration_s": 40, "weight_lb": weight})
            tracker.on_visit_saved(event_id)
            ids.append(event_id)

    assert {label_of(store, i)[1] for i in ids} == {cat}
    assert {label_of(store, i)[2] for i in ids} == {"auto"}
    clock["now"] = NOW - timedelta(days=9)
    summary = tracker.cat(cat)["summary"]
    # Labels keep following the cat, while what it is measured against stays where it began.
    assert summary["anchor_weight_lb"] == pytest.approx(10.1, abs=0.3)
    assert summary["recent_weight_lb"] == pytest.approx(12.3, abs=0.4)
    assert summary["weight_change_pct"] > 15


def test_a_replay_from_scratch_agrees_with_the_labels_made_as_visits_arrived(store: Store):
    clock = {"now": NOW - timedelta(days=80)}
    tracker = CatTracker(store, clock=lambda: clock["now"])
    rng = random.Random(3)
    cat = tracker.create_cat("Heavy", 10.0)["id"]
    ids = []
    for day in range(70):
        at = clock["now"] + timedelta(days=day)
        weight = 10.0 + 0.03 * day + rng.uniform(-0.2, 0.2)
        event_id = store.add_event(at, "visit", {"duration_s": 40, "weight_lb": weight})
        tracker.on_visit_saved(event_id)
        ids.append(event_id)
    before = [label_of(store, i) for i in ids]

    tracker.relabel(clock["now"])

    assert [label_of(store, i) for i in ids] == before
    assert {label[1] for label in before} == {cat}


def test_color_indexes_follow_creation_order_and_are_never_reused(
    store: Store, tracker: CatTracker
):
    a = tracker.create_cat("A", None)
    b = tracker.create_cat("B", None)
    tracker.update_cat(a["id"], archived=True)
    c = tracker.create_cat("C", None)
    assert [a["color_index"], b["color_index"], c["color_index"]] == [0, 1, 2]
    assert tracker.cat(a["id"])["color_index"] == 0


def test_cat_names_are_unique_among_active_cats_ignoring_case(store: Store, tracker: CatTracker):
    miso = tracker.create_cat("Miso", None)["id"]
    with pytest.raises(CatNameTaken):
        tracker.create_cat("miso", None)
    other = tracker.create_cat("Pip", None)["id"]
    with pytest.raises(CatNameTaken):
        tracker.update_cat(other, name="MISO")
    assert tracker.update_cat(miso, name="Miso")["name"] == "Miso"
    assert tracker.update_cat(miso, name="miso")["name"] == "miso"

    tracker.update_cat(miso, archived=True)
    tracker.create_cat("Miso", None)
    with pytest.raises(CatNameTaken):
        tracker.update_cat(miso, archived=False)


def test_unknown_cat_and_visit_and_archived_target(store: Store, tracker: CatTracker):
    event = visit(store, tracker, 10.0)
    with pytest.raises(CatNotFound):
        tracker.cat(99)
    with pytest.raises(CatNotFound):
        tracker.label_visit(event, "cat", 99)
    with pytest.raises(VisitNotFound):
        tracker.label_visit(12345, "not_cat", None)
    gone = tracker.create_cat("Gone", None)["id"]
    tracker.update_cat(gone, archived=True)
    with pytest.raises(CatArchived):
        tracker.label_visit(event, "cat", gone)


def test_a_clean_cycle_event_is_not_a_visit(store: Store, tracker: CatTracker):
    event = store.add_event(NOW, "clean_cycle", {"count": 1})
    with pytest.raises(VisitNotFound):
        tracker.label_visit(event, "not_cat", None)


def test_summary_numbers(store: Store):
    clock = {"now": NOW}
    tracker = CatTracker(store, ZoneInfo("America/Los_Angeles"), clock=lambda: clock["now"])
    cat = tracker.create_cat("Miso", 10.0)["id"]
    # 18:00 UTC is 10:00 in Los Angeles, so "today" starts at 08:00 UTC and has two visits.
    for ago_h, weight, duration in [(2, 10.0, 30), (9, 10.2, 60), (30, 9.8, 90), (200, 10.0, 40)]:
        event = store.add_event(
            NOW - timedelta(hours=ago_h), "visit", {"duration_s": duration, "weight_lb": weight}
        )
        tracker.label_visit(event, "cat", cat)
    store.add_event(NOW - timedelta(hours=3), "visit", {"duration_s": 99, "weight_lb": 20.0})

    summary = tracker.cat(cat)["summary"]

    assert summary["latest_weight_lb"] == 10.0
    assert summary["usual_weight_lb"] == 10.0
    assert summary["visits_today"] == 2
    # Today is still going, so only the seven finished days count.
    assert summary["visits_per_day"] == pytest.approx(1 / 7, abs=0.01)
    assert summary["avg_visit_s"] == pytest.approx(60.0)
    # Three readings in two weeks say nothing about a change.
    assert summary["weight_change_pct"] is None
    assert summary["last_visit_at"] == (NOW - timedelta(hours=2)).isoformat(timespec="milliseconds")


def test_percent_change_needs_an_anchor_and_enough_readings(store: Store):
    tracker = CatTracker(store, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for index in range(14):
        weight = 10.0 if index < 12 else 10.6
        event = store.add_event(
            NOW - timedelta(days=20 - index, hours=index % 3),
            "visit",
            {"weight_lb": weight if index != 13 else 10.6},
        )
        tracker.label_visit(event, "cat", cat)
    for index in range(10):
        event = store.add_event(NOW - timedelta(hours=index + 1), "visit", {"weight_lb": 10.6})
        tracker.label_visit(event, "cat", cat)

    summary = tracker.cat(cat)["summary"]

    assert summary["anchor_weight_lb"] == pytest.approx(10.0, abs=0.1)
    assert summary["recent_readings"] >= 10
    assert summary["weight_change_pct"] == pytest.approx(6.0, abs=0.5)


def test_visits_per_day_is_unknown_until_a_whole_day_has_been_heard(tmp_path: Path):
    fresh = Store(tmp_path / "fresh.db")
    fresh.add_presence(NOW - timedelta(hours=5), True)
    tracker = CatTracker(fresh, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for ago_h in (1, 2, 3):
        event = fresh.add_event(NOW - timedelta(hours=ago_h), "visit", {"weight_lb": 10.0})
        tracker.label_visit(event, "cat", cat)
    assert tracker.cat(cat)["summary"]["visits_per_day"] is None


def test_visits_per_day_needs_three_days_it_can_be_taken_over(store: Store):
    tracker = CatTracker(store, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for days_ago in (3, 5, 6):
        event = store.add_event(NOW - timedelta(days=days_ago), "visit", {"weight_lb": 10.0})
        tracker.label_visit(event, "cat", cat)
    store.add_unreliable_range(
        NOW.date() - timedelta(days=5), NOW.date() - timedelta(days=1), "x", NOW
    )
    assert tracker.cat(cat)["summary"]["visits_per_day"] is None

    store.delete_unreliable_range(store.unreliable_ranges()[0]["id"])
    assert tracker.cat(cat)["summary"]["visits_per_day"] == pytest.approx(0.5, abs=0.01)


def test_series_groups_days_in_the_configured_timezone(store: Store):
    zone = ZoneInfo("America/Los_Angeles")
    tracker = CatTracker(store, zone, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    # 03:30 UTC on 2 Nov is 20:30 on 1 Nov in Los Angeles.
    for at, weight, duration in [
        (datetime(2026, 11, 2, 3, 30, tzinfo=UTC), 10.0, 30),
        (datetime(2026, 11, 2, 9, 0, tzinfo=UTC), 10.4, 50),
        (datetime(2026, 11, 2, 20, 0, tzinfo=UTC), 10.2, 70),
        (datetime(2026, 11, 4, 20, 0, tzinfo=UTC), 10.0, 20),
    ]:
        event = store.add_event(at, "visit", {"duration_s": duration, "weight_lb": weight})
        tracker.label_visit(event, "cat", cat)

    series = tracker.series(cat, date(2026, 11, 1), date(2026, 11, 4))

    assert series["timezone"] == "America/Los_Angeles"
    assert [
        {key: day[key] for key in ("date", "visits", "median_weight_lb", "avg_visit_s")}
        for day in series["days"]
    ] == [
        {"date": "2026-11-01", "visits": 1, "median_weight_lb": 10.0, "avg_visit_s": 30.0},
        {"date": "2026-11-02", "visits": 2, "median_weight_lb": 10.3, "avg_visit_s": 60.0},
        {"date": "2026-11-03", "visits": 0, "median_weight_lb": None, "avg_visit_s": None},
        {"date": "2026-11-04", "visits": 1, "median_weight_lb": 10.0, "avg_visit_s": 20.0},
    ]
    assert {day["coverage"] for day in series["days"]} == {"full"}


def test_series_leaves_a_day_with_no_data_null(tmp_path: Path):
    bare = Store(tmp_path / "bare.db")
    tracker = CatTracker(bare, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in (1, 5):
        event = bare.add_event(datetime(2026, 11, day, 9, tzinfo=UTC), "visit", {"weight_lb": 10.0})
        tracker.label_visit(event, "cat", cat)

    days = tracker.series(cat, date(2026, 11, 1), date(2026, 11, 5))["days"]

    assert [(d["visits"], d["coverage"]) for d in days] == [
        (1, "partial"),
        (None, "none"),
        (None, "none"),
        (None, "none"),
        (1, "partial"),
    ]


def test_series_only_counts_the_one_cat(store: Store, tracker: CatTracker):
    a = tracker.create_cat("A", 10.0)["id"]
    b = tracker.create_cat("B", 14.0)["id"]
    visit(store, tracker, 10.0)
    visit(store, tracker, 14.0)
    today = tracker.today()
    assert tracker.series(a, today, today)["days"][0]["visits"] == 1
    assert tracker.series(b, today, today)["days"][0]["median_weight_lb"] == 14.0
    with pytest.raises(CatNotFound):
        tracker.series(99, today, today)


def test_series_across_a_daylight_saving_change_keeps_whole_days(store: Store):
    zone = ZoneInfo("America/Los_Angeles")
    tracker = CatTracker(store, zone, clock=lambda: NOW)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    # Clocks go back on 1 Nov 2026, so that local day is 25 hours long.
    for at in [
        datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 7, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 8, 30, tzinfo=UTC),
    ]:
        event = store.add_event(at, "visit", {"weight_lb": 10.0})
        tracker.label_visit(event, "cat", cat)
    days = tracker.series(cat, date(2026, 11, 1), date(2026, 11, 2))["days"]
    assert [d["visits"] for d in days] == [2, 1]


def three_cats_worth(store: Store, tracker: CatTracker, centers: list[float], days: float = 5):
    rng = random.Random(1)
    for hour in range(int(days * 24)):
        for center in centers:
            if rng.random() < 0.3:
                visit(
                    store,
                    tracker,
                    center + rng.uniform(-0.2, 0.2),
                    ago=timedelta(hours=hour, minutes=rng.randint(0, 59)),
                )


def test_suggestions_find_each_cat_once_there_is_enough_data(store: Store, tracker: CatTracker):
    three_cats_worth(store, tracker, [8.0, 10.5, 14.0])

    suggestions = tracker.suggestions()

    assert suggestions["enough_data"] is True
    centers = [c["center_lb"] for c in suggestions["clusters"]]
    assert centers == pytest.approx([8.0, 10.5, 14.0], abs=0.15)
    first = suggestions["clusters"][0]
    assert first["low_lb"] <= first["center_lb"] <= first["high_lb"]
    assert sum(c["visits"] for c in suggestions["clusters"]) == suggestions["visits_considered"]


def test_suggestions_need_enough_visits(store: Store, tracker: CatTracker):
    for i in range(cats_module.SUGGESTION_MIN_VISITS - 1):
        visit(store, tracker, 10.0, ago=timedelta(days=4, hours=i))
    assert tracker.suggestions()["enough_data"] is False
    visit(store, tracker, 10.0, ago=timedelta(hours=1))
    assert tracker.suggestions()["enough_data"] is True


def test_suggestions_need_a_few_days_not_one_busy_afternoon(store: Store, tracker: CatTracker):
    for i in range(30):
        visit(store, tracker, 10.0, ago=timedelta(hours=i))
    assert tracker.suggestions()["enough_data"] is False


def test_suggestions_skip_visits_that_already_belong_to_a_cat(store: Store, tracker: CatTracker):
    three_cats_worth(store, tracker, [8.0, 14.0])
    assert len(tracker.suggestions()["clusters"]) == 2

    tracker.create_cat("Small", 8.0)

    assert [c["center_lb"] for c in tracker.suggestions()["clusters"]] == pytest.approx(
        [14.0], abs=0.15
    )


def test_suggestions_ignore_visits_the_owner_called_not_a_cat(store: Store, tracker: CatTracker):
    three_cats_worth(store, tracker, [10.0])
    for found in store.visits(needs_cat=True):
        tracker.label_visit(found["id"], "not_cat", None)
    assert tracker.suggestions()["clusters"] == []


def test_clusters_split_where_the_gap_is_and_drop_strays():
    clusters = find_clusters([8.0, 8.1, 8.2, 12.0, 12.1, 12.3, 12.2, 20.0])
    assert [(c.low, c.high, c.visits) for c in clusters] == [(8.0, 8.2, 3), (12.0, 12.3, 4)]
    assert find_clusters([]) == []


def test_a_chain_of_close_weights_is_not_one_giant_cluster():
    chain = [8.0 + 0.3 * i for i in range(30)]
    clusters = find_clusters(chain)
    assert len(clusters) > 1
    assert all(c.high - c.low <= cats_module.CLUSTER_MAX_SPAN_LB for c in clusters)
    assert sum(c.visits for c in clusters) == len(chain)


def test_older_databases_gain_the_new_tables_without_losing_anything(tmp_path: Path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE messages (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
            body TEXT NOT NULL);
        CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
            detail TEXT NOT NULL);
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO events (at, kind, detail)
            VALUES ('2026-11-08T10:00:00.000+00:00', 'visit', '{"weight_lb": 9.5}');
        INSERT INTO messages (at, kind, body)
            VALUES ('2026-11-08T10:00:00.000+00:00', 'state', '{}');
        """
    )
    old.commit()
    old.close()

    store = Store(path)
    tracker = CatTracker(store, clock=lambda: NOW)

    [found] = store.visits()
    assert (found["detail"], found["label"], found["label_source"]) == (
        {"weight_lb": 9.5},
        "unknown",
        "auto",
    )
    assert tracker.needing_cat() == 1
    cat = tracker.create_cat("Miso", 9.5)
    assert label_of(store, found["id"]) == ("cat", cat["id"], "auto")
    Store(path).close()


def test_the_newest_visit_waiting_for_a_cat_names_the_closest_cat(
    store: Store, tracker: CatTracker
):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    tracker.create_cat("Pip", 14.0)
    visit(store, tracker, 12.6, ago=timedelta(hours=3))
    newest = visit(store, tracker, 11.4, ago=timedelta(hours=1), duration_s=35)

    found = tracker.latest_unassigned_visit()

    assert found == {
        "id": newest,
        "at": (NOW - timedelta(hours=1)).isoformat(timespec="milliseconds"),
        "weight_lb": 11.4,
        "duration_s": 35,
        "closest_cat_id": miso,
    }


def test_a_visit_without_a_weight_is_not_offered_to_the_owner(store: Store, tracker: CatTracker):
    tracker.create_cat("Miso", 10.0)
    visit(store, tracker, None)
    assert tracker.latest_unassigned_visit() is None


def test_nothing_waiting_gives_none(store: Store, tracker: CatTracker):
    tracker.create_cat("Miso", 10.0)
    visit(store, tracker, 10.0)
    assert tracker.latest_unassigned_visit() is None


def test_the_summary_has_what_the_home_and_insights_tabs_show(store: Store, tracker: CatTracker):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    visit(store, tracker, 10.2, ago=timedelta(days=1), duration_s=120)
    visit(store, tracker, 10.4, ago=timedelta(days=1, minutes=-30), duration_s=60)
    visit(store, tracker, 10.0, ago=timedelta(hours=2), duration_s=30)

    summary = tracker.cat(miso)["summary"]

    assert summary["yesterday_visits"] == 2
    assert summary["yesterday_weight_lb"] == 10.3
    assert summary["yesterday_minutes"] == 3.0
    assert summary["latest_weight_lb"] == 10.0
    assert summary["last_visit_at"] is not None
    assert summary["common_visit_hour"] is None


def test_an_archived_cat_keeps_its_profile(store: Store, tracker: CatTracker):
    cat = tracker.create_cat("Miso", 10.0, breed="Tabby", sex="male")["id"]
    tracker.update_cat(cat, archived=True)
    assert tracker.cat(cat)["breed"] == "Tabby"


def test_a_replay_that_changes_nothing_writes_nothing(
    store: Store, tracker: CatTracker, monkeypatch: pytest.MonkeyPatch
):
    tracker.create_cat("Ada", 10.0)
    for hours in range(1, 8):
        visit(store, tracker, 10.0, ago=timedelta(hours=hours))
    writes: list[tuple] = []
    original = store.apply_auto_labels
    monkeypatch.setattr(
        store, "apply_auto_labels", lambda *args: writes.append(args) or original(*args)
    )

    tracker.rebuild()

    assert writes == []

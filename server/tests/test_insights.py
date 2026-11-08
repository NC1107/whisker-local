from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local.insights import (
    MAX_BUCKETS,
    Insights,
    InvalidRange,
    bucket_count,
    bucket_start,
    pick_granularity,
)
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
TODAY = date(2026, 11, 8)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def insights(store: Store) -> Insights:
    return Insights(store, clock=lambda: NOW)


def visit(store: Store, at: datetime, duration_s: int | None = 60, cat_id: int | None = None):
    event_id = store.add_event(at, "visit", {"duration_s": duration_s, "weight_lb": 9.0})
    if cat_id is not None:
        store.set_user_label(event_id, "cat", cat_id)
    return event_id


@contextmanager
def one_transaction(store: Store):
    store._db.execute("BEGIN")
    try:
        yield
    finally:
        store._db.execute("COMMIT")


def cycle(store: Store, at: datetime):
    store.add_event(at, "clean_cycle", {"count": 1})


def at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def point(report: dict, day: date) -> dict:
    return next(p for p in report["overall"]["series"] if p["date"] == day.isoformat())


def test_an_empty_history_is_all_zeros_with_a_full_series(insights: Insights):
    report = insights.report(TODAY - timedelta(days=6), TODAY, None)

    assert report["days"] == 7
    assert len(report["overall"]["series"]) == 7
    assert report["overall"]["visits"] == 0
    assert report["overall"]["avg_visit_s"] is None
    assert report["overall"]["hours"] == [0] * 24
    assert report["overall"]["change"] == {
        "visits_pct": None,
        "avg_visit_pct": None,
        "clean_cycles_pct": None,
    }
    assert report["cats"] == []


def test_the_default_range_is_the_last_thirty_days(insights: Insights):
    report = insights.report(None, None, None)
    assert (report["start"], report["end"], report["days"]) == ("2026-10-10", "2026-11-08", 30)
    assert (report["previous_start"], report["previous_end"]) == ("2026-09-10", "2026-10-09")


def test_visits_and_cycles_per_day_and_the_average_length(store: Store, insights: Insights):
    visit(store, at(TODAY, 8), 30)
    visit(store, at(TODAY, 9), 90)
    visit(store, at(TODAY - timedelta(days=1), 9), 120)
    cycle(store, at(TODAY, 8))
    cycle(store, at(TODAY, 9))
    cycle(store, at(TODAY - timedelta(days=2)))

    report = insights.report(TODAY - timedelta(days=2), TODAY, None)

    assert point(report, TODAY) == {
        "date": "2026-11-08",
        "visits": 2,
        "avg_visit_s": 60.0,
        "clean_cycles": 2,
    }
    assert point(report, TODAY - timedelta(days=1))["visits"] == 1
    assert point(report, TODAY - timedelta(days=2))["clean_cycles"] == 1
    overall = report["overall"]
    assert overall["visits"] == 3
    assert overall["visits_per_day"] == 1.0
    assert overall["avg_visit_s"] == 80.0
    assert overall["clean_cycles"] == 3
    assert overall["clean_cycles_per_day"] == 1.0


def test_visits_without_a_duration_are_counted_but_not_averaged(store: Store, insights: Insights):
    visit(store, at(TODAY), 100)
    visit(store, at(TODAY), None)

    overall = insights.report(TODAY, TODAY, None)["overall"]

    assert overall["visits"] == 2
    assert overall["avg_visit_s"] == 100.0


def test_time_of_day_histogram_has_one_bucket_per_hour(store: Store, insights: Insights):
    for hour in (0, 6, 6, 23):
        visit(store, at(TODAY, hour, 30))

    hours = insights.report(TODAY, TODAY, None)["overall"]["hours"]

    assert len(hours) == 24
    assert (hours[0], hours[6], hours[23], sum(hours)) == (1, 2, 1, 4)


def test_this_period_is_set_against_the_one_before(store: Store, insights: Insights):
    for offset in range(4):
        visit(store, at(TODAY - timedelta(days=offset)), 60)
    for offset in range(7, 9):
        visit(store, at(TODAY - timedelta(days=offset)), 120)
    cycle(store, at(TODAY))
    cycle(store, at(TODAY - timedelta(days=8)))
    cycle(store, at(TODAY - timedelta(days=9)))
    cycle(store, at(TODAY - timedelta(days=10)))

    report = insights.report(TODAY - timedelta(days=6), TODAY, None)

    assert (report["previous_start"], report["previous_end"]) == ("2026-10-26", "2026-11-01")
    overall = report["overall"]
    assert overall["previous"]["visits"] == 2
    assert overall["previous"]["avg_visit_s"] == 120.0
    assert overall["previous"]["clean_cycles"] == 3
    assert overall["change"]["visits_pct"] == 100.0
    assert overall["change"]["avg_visit_pct"] == -50.0
    assert overall["change"]["clean_cycles_pct"] == pytest.approx(-66.7)
    assert overall["visits"] == 4


def test_the_previous_period_does_not_leak_into_the_current_series(
    store: Store, insights: Insights
):
    visit(store, at(TODAY - timedelta(days=10)))

    report = insights.report(TODAY - timedelta(days=6), TODAY, None)

    assert report["overall"]["visits"] == 0
    assert sum(p["visits"] for p in report["overall"]["series"]) == 0
    assert report["overall"]["previous"]["visits"] == 1


def test_per_cat_blocks_split_the_same_visits(store: Store, insights: Insights):
    miso = store.add_cat("Miso", None, NOW)
    pip = store.add_cat("Pip", None, NOW)
    visit(store, at(TODAY, 7), 30, cat_id=miso)
    visit(store, at(TODAY, 7), 50, cat_id=miso)
    visit(store, at(TODAY, 20), 200, cat_id=pip)
    visit(store, at(TODAY, 21), 10)

    report = insights.report(TODAY, TODAY, None)

    by_name = {c["name"]: c for c in report["cats"]}
    assert by_name["Miso"]["visits"] == 2
    assert by_name["Miso"]["avg_visit_s"] == 40.0
    assert by_name["Miso"]["hours"][7] == 2
    assert by_name["Pip"]["avg_visit_s"] == 200.0
    assert by_name["Pip"]["hours"][20] == 1
    assert report["unassigned"]["visits"] == 1
    assert report["overall"]["visits"] == 4
    assert "clean_cycles" not in by_name["Miso"]
    assert [c["cat_id"] for c in report["cats"]] == [miso, pip]


def test_each_cat_has_its_own_comparison(store: Store, insights: Insights):
    miso = store.add_cat("Miso", None, NOW)
    visit(store, at(TODAY), 60, cat_id=miso)
    visit(store, at(TODAY - timedelta(days=1)), 60, cat_id=miso)
    visit(store, at(TODAY - timedelta(days=2)), 60, cat_id=miso)

    [cat] = insights.report(TODAY - timedelta(days=1), TODAY, None)["cats"]

    assert cat["previous"]["visits"] == 1
    assert cat["change"]["visits_pct"] == 100.0


def test_a_cat_with_no_visits_is_listed_but_an_idle_archived_cat_is_not(
    store: Store, insights: Insights
):
    store.add_cat("Miso", None, NOW)
    gone = store.add_cat("Gone", None, NOW)
    store.set_cat_archived(gone, NOW)
    retired = store.add_cat("Retired", None, NOW)
    visit(store, at(TODAY), 60, cat_id=retired)
    store.set_cat_archived(retired, NOW)

    names = [c["name"] for c in insights.report(TODAY, TODAY, None)["cats"]]

    assert names == ["Miso", "Retired"]


def test_visits_marked_not_a_cat_are_not_counted_anywhere(store: Store, insights: Insights):
    event_id = visit(store, at(TODAY))
    store.set_user_label(event_id, "not_cat", None)
    visit(store, at(TODAY))

    report = insights.report(TODAY, TODAY, None)

    assert report["overall"]["visits"] == 1
    assert report["unassigned"]["visits"] == 1


def test_days_and_hours_are_the_owners_not_utc(store: Store):
    tokyo = Insights(store, ZoneInfo("Asia/Tokyo"), clock=lambda: NOW)
    visit(store, datetime(2026, 11, 7, 16, 30, tzinfo=UTC))  # 01:30 on the 8th in Tokyo

    report = tokyo.report(date(2026, 11, 8), date(2026, 11, 8), None)

    assert report["timezone"] == "Asia/Tokyo"
    assert report["overall"]["visits"] == 1
    assert report["overall"]["hours"][1] == 1
    assert tokyo.report(date(2026, 11, 7), date(2026, 11, 7), None)["overall"]["visits"] == 0


def test_the_local_day_edges_are_exact(store: Store):
    chicago = Insights(store, ZoneInfo("America/Chicago"), clock=lambda: NOW)
    visit(store, datetime(2026, 11, 8, 5, 59, 59, tzinfo=UTC))  # 23:59:59 on the 7th
    visit(store, datetime(2026, 11, 8, 6, 0, 0, tzinfo=UTC))  # midnight on the 8th, CST

    seventh = chicago.report(date(2026, 11, 7), date(2026, 11, 7), None)
    eighth = chicago.report(date(2026, 11, 8), date(2026, 11, 8), None)

    assert (seventh["overall"]["visits"], eighth["overall"]["visits"]) == (1, 1)


def test_a_daylight_saving_day_still_counts_each_visit_once(store: Store):
    chicago = Insights(store, ZoneInfo("America/Chicago"), clock=lambda: NOW)
    # 2026-11-01 is 25 hours long in Chicago.
    for hour in range(5, 31):
        visit(store, datetime(2026, 11, 1, 5, tzinfo=UTC) + timedelta(hours=hour - 5))

    report = chicago.report(date(2026, 11, 1), date(2026, 11, 1), None)

    assert report["overall"]["visits"] == 25
    assert sum(report["overall"]["hours"]) == 25


def test_weeks_start_on_monday_and_months_on_the_first(store: Store, insights: Insights):
    assert bucket_start(date(2026, 11, 8), "week") == date(2026, 11, 2)
    assert bucket_start(date(2026, 11, 8), "month") == date(2026, 11, 1)
    visit(store, at(date(2026, 11, 8)))
    visit(store, at(date(2026, 11, 2)))
    visit(store, at(date(2026, 11, 1)))

    weekly = insights.report(date(2026, 10, 26), TODAY, "week")
    assert [(p["date"], p["visits"]) for p in weekly["overall"]["series"]] == [
        ("2026-10-26", 1),
        ("2026-11-02", 2),
    ]
    monthly = insights.report(date(2026, 10, 1), TODAY, "month")
    assert [(p["date"], p["visits"]) for p in monthly["overall"]["series"]] == [
        ("2026-10-01", 0),
        ("2026-11-01", 3),
    ]


def test_buckets_across_a_year_boundary_and_leap_day():
    assert bucket_count(date(2023, 12, 15), date(2024, 3, 2), "month") == 4
    assert bucket_count(date(2024, 2, 1), date(2024, 3, 1), "day") == 30


def test_the_automatic_step_widens_with_the_range():
    assert [pick_granularity(d) for d in (1, 92, 93, 730, 731, 3660)] == [
        "day",
        "day",
        "week",
        "week",
        "month",
        "month",
    ]


def test_years_of_history_come_back_as_a_few_points(store: Store, insights: Insights):
    start = date(2023, 1, 1)
    with one_transaction(store):
        for offset in range(0, 1400, 2):
            visit(store, at(start + timedelta(days=offset), 8 + offset % 12), 45)

    report = insights.report(start, TODAY, None)

    assert report["granularity"] == "month"
    assert len(report["overall"]["series"]) == 47
    assert report["overall"]["visits"] == 700
    assert sum(p["visits"] for p in report["overall"]["series"]) == 700
    assert sum(report["overall"]["hours"]) == 700


def test_asking_for_too_much_is_refused(insights: Insights):
    with pytest.raises(InvalidRange):
        insights.report(TODAY, TODAY - timedelta(days=1), None)
    with pytest.raises(InvalidRange):
        insights.report(TODAY - timedelta(days=3660), TODAY, None)
    with pytest.raises(InvalidRange):
        insights.report(TODAY - timedelta(days=MAX_BUCKETS + 1), TODAY, "day")
    assert insights.report(TODAY - timedelta(days=MAX_BUCKETS - 1), TODAY, "day")["days"] == (
        MAX_BUCKETS
    )


def test_insights_api(client: TestClient, parts):
    _, store, _ = parts
    now = datetime.now(UTC)
    cat_id = store.add_cat("Miso", None, now)
    visit(store, now - timedelta(hours=1), 60, cat_id=cat_id)
    cycle(store, now - timedelta(hours=1))

    body = client.get("/api/insights?days=7", headers=AUTH).json()

    assert body["days"] == 7
    assert body["granularity"] == "day"
    assert body["overall"]["visits"] == 1
    assert body["overall"]["clean_cycles"] == 1
    assert body["cats"][0]["name"] == "Miso"
    assert len(body["overall"]["series"]) == 7


def test_insights_api_range_and_errors(client: TestClient):
    ok = client.get("/api/insights?start=2024-01-01&end=2026-01-01&granularity=week", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["granularity"] == "week"

    for query in (
        "start=2026-02-01&end=2026-01-01",
        "days=0",
        "days=3661",
        "days=7&start=2026-01-01",
        "granularity=hour",
        "start=2000-01-01&end=2026-01-01",
        "start=2020-01-01&end=2026-01-01&granularity=day",
    ):
        assert client.get(f"/api/insights?{query}", headers=AUTH).status_code == 422, query


def test_ten_years_of_visits_still_come_back_as_a_short_reply(client: TestClient, parts):
    _, store, _ = parts
    now = datetime.now(UTC)
    with one_transaction(store):
        for offset in range(3000):
            store.add_event(now - timedelta(hours=offset * 3), "visit", {"duration_s": 30})

    body = client.get("/api/insights?days=3660", headers=AUTH).json()

    assert body["overall"]["visits"] == 3000
    assert len(body["overall"]["series"]) <= 130

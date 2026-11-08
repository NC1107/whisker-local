from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local.cat_insights import CatInsights
from whisker_local.cats import CatNotFound, CatTracker
from whisker_local.store import Store

# A Sunday, when a calendar week would hold only today.
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer right-key"}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "h.db")


@pytest.fixture
def tracker(store: Store) -> CatTracker:
    return CatTracker(store, clock=lambda: NOW)


@pytest.fixture
def insights(store: Store, tracker: CatTracker) -> CatInsights:
    return CatInsights(store, tracker)


def add_visit(
    store: Store,
    tracker: CatTracker,
    at: datetime,
    weight: float | None = 10.0,
    duration_s: int = 60,
    *,
    started: datetime | None = None,
) -> int:
    began = started or at - timedelta(seconds=duration_s)
    event_id = store.add_event(
        at,
        "visit",
        {"started": began.isoformat(), "duration_s": duration_s, "weight_lb": weight},
    )
    tracker.on_visit_saved(event_id)
    return event_id


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def test_the_week_is_the_seven_days_ending_yesterday(insights, tracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    report = insights.report(cat, "week", 0)
    assert (report["start"], report["end"]) == ("2026-11-01", "2026-11-07")
    assert report["points"][0] == "2026-11-01" and len(report["points"]) == 7
    assert report["range_label"] == "November 1 to November 7"


def test_offset_one_is_the_seven_days_before(insights, tracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    report = insights.report(cat, "week", 1)
    assert (report["start"], report["end"]) == ("2026-10-25", "2026-10-31")
    assert report["offset"] == 1


def test_a_sunday_still_shows_a_full_week_of_data(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in (2, 4, 7):
        add_visit(store, tracker, at(date(2026, 11, day), 9))

    report = insights.report(cat, "week", 0)

    assert [n for n in report["visits"]["series"] if n] == [1, 1, 1]
    assert report["overview"]["week_visits"] == 3


def test_the_month_is_the_last_thirty_days_and_steps_back_by_thirty(insights, tracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    now = insights.report(cat, "month", 0)
    before = insights.report(cat, "month", 1)
    assert (now["start"], now["end"]) == ("2026-10-10", "2026-11-08")
    assert len(now["points"]) == 30
    assert (before["start"], before["end"]) == ("2026-09-10", "2026-10-09")


def test_the_year_is_twelve_calendar_months_ending_with_this_one(insights, tracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    report = insights.report(cat, "year", 0)
    assert report["points"][0] == "2025-12-01" and report["points"][-1] == "2026-11-01"
    assert len(report["points"]) == 12
    assert report["range_label"] == "December 2025 to November 2026"
    earlier = insights.report(cat, "year", 1)
    assert earlier["points"][0] == "2024-12-01"


def test_labels_name_the_years_when_they_are_not_this_one(insights, tracker):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    label = insights.report(cat, "week", 50)["range_label"]
    assert label == "November 16, 2025 to November 22, 2025"
    crossing = insights.report(cat, "week", 44)["range_label"]
    assert crossing == "December 28, 2025 to January 3, 2026"


def test_series_hold_one_point_per_day_with_nulls_where_there_is_no_data(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 11, 3), 8), 10.0, 120)
    add_visit(store, tracker, at(date(2026, 11, 3), 20), 10.4, 60)
    add_visit(store, tracker, at(date(2026, 11, 5), 9), 9.8, 180)
    unweighed = add_visit(store, tracker, at(date(2026, 11, 7), 7), None, 30)
    tracker.label_visit(unweighed, "cat", cat)

    report = insights.report(cat, "week", 0)

    # Sun Mon Tue Wed Thu Fri Sat; the cat is first seen on Tuesday the 3rd.
    assert report["weight"]["series"] == [None, None, 10.2, None, 9.8, None, None]
    assert report["visits"]["series"] == [None, None, 2, 0, 1, 0, 1]
    assert report["minutes"]["series"] == [None, None, 3.0, 0, 3.0, 0, 0.5]
    assert report["weight"]["average"] == 10.0
    assert report["visits"]["average"] == 0.8
    assert report["minutes"]["average"] == 1.3


def test_a_cat_with_no_visits_has_all_null_series_and_no_averages(tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    report = insights.report(cat, "month", 0)
    for key in ("weight", "visits", "minutes"):
        assert set(report[key]["series"]) == {None}
        assert report[key]["average"] is None
    assert report["time_of_visit"] == []
    assert report["summary"] == ["No visits yet.", "No weight recorded yet."]
    assert report["has_earlier"] is False


def test_other_cats_visits_are_not_counted(store, tracker, insights):
    miso = tracker.create_cat("Miso", 10.0)["id"]
    tracker.create_cat("Pip", 14.0)
    add_visit(store, tracker, at(date(2026, 11, 8), 9), 10.0)
    add_visit(store, tracker, at(date(2026, 11, 8), 10), 14.0)
    assert insights.report(miso, "month", 0)["visits"]["series"][-1] == 1


def test_the_year_gives_a_median_weight_and_visits_per_day_for_each_month(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in (1, 2, 3):
        add_visit(store, tracker, at(date(2026, 9, day), 8), 9.8 + day / 10, 120)
        add_visit(store, tracker, at(date(2026, 9, day), 20), 9.8 + day / 10, 120)

    report = insights.report(cat, "year", 0)

    september = report["points"].index("2026-09-01")
    assert report["weight"]["series"][september] == 10.0
    # Six visits over the 30 days from the first visit to the end of the month.
    assert report["visits"]["series"][september] == pytest.approx(6 / 30, abs=0.01)
    assert report["minutes"]["series"][september] == pytest.approx(12 / 30, abs=0.1)
    assert report["weight"]["series"][0] is None
    assert report["time_of_visit"] == []
    assert report["visits"]["series"][report["points"].index("2026-11-01")] == 0


def test_time_of_visit_gives_day_column_decimal_hour_and_seconds(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 11, 3), 19, 45), duration_s=90)
    add_visit(store, tracker, at(date(2026, 11, 1), 0, 20), duration_s=40)

    points = insights.report(cat, "week", 0)["time_of_visit"]

    assert {"day": 2, "hour": 19.72, "duration_s": 90} in points
    assert {"day": 0, "hour": 0.32, "duration_s": 40} in points


def test_a_visit_that_began_the_day_before_counts_at_its_end(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    ended = at(date(2026, 11, 3), 0, 10)
    add_visit(store, tracker, ended, duration_s=1200, started=ended - timedelta(minutes=20))

    [point] = insights.report(cat, "week", 0)["time_of_visit"]

    assert point["day"] == 2
    assert point["hour"] == 0.17


def test_time_of_visit_for_the_month_uses_the_day_index_in_the_range(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 10, 10), 12))
    add_visit(store, tracker, at(date(2026, 11, 8), 12))

    points = insights.report(cat, "month", 0)["time_of_visit"]

    assert sorted(p["day"] for p in points) == [0, 29]


def test_has_earlier_tells_the_app_when_to_stop_the_back_arrow(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 10, 20), 12))
    assert insights.report(cat, "week", 0)["has_earlier"] is True
    assert insights.report(cat, "month", 0)["has_earlier"] is False


def test_the_overview_has_yesterday_this_week_and_the_habit(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    # Today's visit is not in the week, which ends yesterday.
    add_visit(store, tracker, at(date(2026, 11, 7), 19), 10.2, 120)
    add_visit(store, tracker, at(date(2026, 11, 7), 19, 30), 10.4, 60)
    add_visit(store, tracker, at(date(2026, 11, 8), 9), 10.0, 30)
    add_visit(store, tracker, at(date(2026, 11, 2), 19), 10.0, 30)

    overview = insights.report(cat, "week", 0)["overview"]

    assert overview == {
        "yesterday_weight_lb": 10.3,
        "yesterday_visits": 2,
        "yesterday_minutes": 3.0,
        "week_visits": 3,
        "common_visit_hour": 19,
    }


def test_the_most_common_hour_breaks_ties_toward_the_earlier_one(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 11, 5), 21))
    add_visit(store, tracker, at(date(2026, 11, 6), 7))
    assert insights.report(cat, "week", 0)["overview"]["common_visit_hour"] == 7


def test_the_summary_says_average_when_yesterday_matches_the_cats_days(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in range(2, 12):
        for hour in (8, 20):
            add_visit(store, tracker, at(date(2026, 11, 8) - timedelta(days=day), hour))
    for hour in (8, 20):
        add_visit(store, tracker, at(date(2026, 11, 7), hour))

    summary = insights.report(cat, "week", 0)["summary"]

    assert summary == ["2 visits yesterday, about average.", "Weight is in range."]


def test_the_summary_notices_a_busy_or_a_quiet_day(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in range(2, 12):
        for hour in (8, 20):
            add_visit(store, tracker, at(date(2026, 11, 8) - timedelta(days=day), hour))
    assert insights.report(cat, "week", 0)["summary"][0] == "No visits yesterday, fewer than usual."
    for hour in range(6, 13):
        add_visit(store, tracker, at(date(2026, 11, 7), hour))
    assert insights.report(cat, "week", 0)["summary"][0] == "7 visits yesterday, more than usual."


def test_the_summary_does_not_compare_before_there_is_a_baseline(store, tracker, insights):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    add_visit(store, tracker, at(date(2026, 11, 7), 8))
    assert insights.report(cat, "week", 0)["summary"][0] == "1 visit yesterday."


@pytest.mark.parametrize(
    ("weight", "sentence"),
    [
        (10.5, "Weight is in range."),
        (11.5, "Weight is above the usual range."),
        (8.5, "Weight is below the usual range."),
    ],
)
def test_the_weight_sentence_follows_the_usual_weight(store, tracker, insights, weight, sentence):
    cat = tracker.create_cat("Miso", 10.0)["id"]
    for day in range(2, 6):
        add_visit(store, tracker, at(date(2026, 11, 8) - timedelta(days=day), 9), 10.0)
    odd = add_visit(store, tracker, at(date(2026, 11, 7), 9), weight)
    tracker.label_visit(odd, "cat", cat)
    # Twenty readings make the median the cat's own weight, not the one new reading.
    for hour in range(10, 20):
        add_visit(store, tracker, at(date(2026, 11, 6), hour), 10.0)
    assert insights.report(cat, "week", 0)["summary"][1] == sentence


def test_days_follow_the_servers_timezone(store):
    zone = ZoneInfo("America/Los_Angeles")
    clock = lambda: datetime(2026, 11, 8, 20, 0, tzinfo=zone)  # noqa: E731
    tracker = CatTracker(store, zone, clock=clock)
    cat = tracker.create_cat("Miso", 10.0)["id"]
    # 23:30 on Saturday in Los Angeles is already Sunday in UTC.
    add_visit(store, tracker, datetime(2026, 11, 7, 23, 30, tzinfo=zone))

    report = CatInsights(store, tracker).report(cat, "week", 0)

    assert report["visits"]["series"][6] == 1
    assert report["timezone"] == "America/Los_Angeles"
    assert report["overview"]["yesterday_visits"] == 1


def test_an_unknown_cat_is_not_found(insights):
    with pytest.raises(CatNotFound):
        insights.report(99, "week", 0)


def test_the_endpoint_returns_the_report(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    response = client.get(
        f"/api/cats/{cat['id']}/insights", params={"period": "month", "offset": 1}, headers=AUTH
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["period"], body["offset"], len(body["points"])) == ("month", 1, 30)


def test_the_endpoint_defaults_to_this_week(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    body = client.get(f"/api/cats/{cat['id']}/insights", headers=AUTH).json()
    assert (body["period"], body["offset"], len(body["points"])) == ("week", 0, 7)


@pytest.mark.parametrize("query", ["period=decade", "offset=-1", "offset=100000", "offset=x"])
def test_the_endpoint_refuses_bad_queries(client: TestClient, query: str):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    response = client.get(f"/api/cats/{cat['id']}/insights?{query}", headers=AUTH)
    assert response.status_code == 422


def test_the_endpoint_needs_the_key_and_a_real_cat(client: TestClient):
    assert client.get("/api/cats/1/insights").status_code == 401
    assert client.get("/api/cats/99/insights", headers=AUTH).status_code == 404

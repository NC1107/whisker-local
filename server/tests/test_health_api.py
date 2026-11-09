from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from health_data import CatPlan, build

AUTH = {"Authorization": "Bearer right-key"}


@pytest.fixture
def world(parts, tmp_path):
    _, store, _ = parts
    return build(
        tmp_path,
        [CatPlan("Ada", 10.0), CatPlan("Bo", 9.0)],
        days=120,
        store=store,
        end=datetime.now(UTC),
    )


def get(client, path, **params):
    return client.get(path, params=params, headers=AUTH)


def test_worth_a_look_lists_notes_and_what_is_still_missing(client, world):
    body = get(client, "/api/worth-a-look").json()

    assert body["notes"] == []
    kinds = {item["kind"] for item in body["waiting"]}
    assert {"household_rate", "silence", "cat_rate", "burst", "weight_change"} <= kinds


def test_worth_a_look_for_one_cat_leaves_other_cats_out(client, world):
    body = get(client, "/api/worth-a-look", cat_id=world.cat_ids["Ada"]).json()

    assert {item["cat_id"] for item in body["waiting"]} <= {None, world.cat_ids["Ada"]}
    assert not any("Ada" in item["text"] for item in body["waiting"])


def test_worth_a_look_for_an_unknown_cat_is_404(client, world):
    assert get(client, "/api/worth-a-look", cat_id=999).status_code == 404


def test_data_quality_gives_a_day_row_with_the_unnamed_share(client, world):
    end = date.today()
    body = get(
        client, "/api/data-quality", start=str(end - timedelta(days=30)), end=str(end)
    ).json()

    assert len(body["days"]) == 31
    assert body["summary"]["visits"] == body["summary"]["named"] + body["summary"]["unnamed"]
    assert body["summary"]["unnamed_share"] == 0
    first = body["days"][0]
    assert {"date", "state", "unreliable", "scale_disturbed", "visits", "named", "unnamed"} <= set(
        first
    )


def test_data_quality_marks_unreliable_days_as_a_gap_run(client, world):
    end = date.today()
    client.post(
        "/api/coverage/unreliable",
        json={
            "start": str(end - timedelta(days=10)),
            "end": str(end - timedelta(days=8)),
            "note": "moved",
        },
        headers=AUTH,
    )

    body = get(
        client, "/api/data-quality", start=str(end - timedelta(days=30)), end=str(end)
    ).json()

    assert body["gaps"] == [
        {
            "start": str(end - timedelta(days=10)),
            "end": str(end - timedelta(days=8)),
            "kind": "unreliable",
        }
    ]
    assert body["summary"]["unreliable"] == 3


def test_data_quality_reaches_back_years_in_one_call_but_not_without_limit(client, world):
    end = date.today()

    assert (
        get(
            client, "/api/data-quality", start=str(end - timedelta(days=1500)), end=str(end)
        ).status_code
        == 200
    )
    assert (
        get(
            client, "/api/data-quality", start=str(end - timedelta(days=4000)), end=str(end)
        ).status_code
        == 422
    )
    assert (
        get(
            client, "/api/data-quality", start=str(end), end=str(end - timedelta(days=1))
        ).status_code
        == 422
    )


def test_the_weight_chart_has_dots_a_line_the_anchor_and_gaps_as_bands(client, world):
    cat = world.cat_ids["Ada"]
    end = date.today()

    body = get(
        client, f"/api/cats/{cat}/weight-chart", start=str(end - timedelta(days=60)), end=str(end)
    ).json()

    assert body["anchor"]["weight_lb"] == 10.0
    assert body["readings"] and {"date", "hour", "lb", "hollow", "why"} <= set(body["readings"][0])
    assert (
        body["line"]
        and body["line"][0]["low"] < body["line"][0]["median"] < body["line"][0]["high"]
    )
    assert body["latest"]["stage"] == "change"
    assert body["status"] == {"line": True, "text": None}


def test_between_three_and_six_weeks_the_line_shows_and_the_change_waits(client, parts, tmp_path):
    _, store, _ = parts
    world = build(tmp_path, [CatPlan("Ada", 10.0)], days=30, store=store, end=datetime.now(UTC))

    body = get(client, f"/api/cats/{world.cat_ids['Ada']}/weight-chart").json()

    assert body["latest"]["stage"] == "trend" and body["latest"]["change_pct"] is None
    assert body["status"]["line"] is True
    assert "Change from usual needs about" in body["status"]["text"]


def test_the_weight_chart_before_the_minimum_history_says_how_much_is_missing(
    client, parts, tmp_path
):
    _, store, _ = parts
    world = build(tmp_path, [CatPlan("Ada", 10.0)], days=8, store=store, end=datetime.now(UTC))

    body = get(client, f"/api/cats/{world.cat_ids['Ada']}/weight-chart").json()

    assert body["line"] == []
    assert body["readings"], "dots are shown from the first day"
    assert body["status"]["line"] is False
    assert "more days of readings" in body["status"]["text"]


def test_a_reading_on_an_unreliable_day_is_drawn_hollow_not_dropped(client, world):
    cat = world.cat_ids["Ada"]
    end = date.today()
    client.post(
        "/api/coverage/unreliable",
        json={
            "start": str(end - timedelta(days=5)),
            "end": str(end - timedelta(days=3)),
            "note": "moved",
        },
        headers=AUTH,
    )

    body = get(
        client, f"/api/cats/{cat}/weight-chart", start=str(end - timedelta(days=10)), end=str(end)
    ).json()

    marked = {str(end - timedelta(days=n)) for n in (3, 4, 5)}
    on_marked_days = [r for r in body["readings"] if r["date"] in marked]
    assert on_marked_days
    assert all(r["hollow"] and r["why"] == "unreliable" for r in on_marked_days)


def test_the_rhythm_has_one_actogram_row_per_day_and_waits_for_the_heatmap(client, world):
    body = get(client, "/api/rhythm", cat_id=world.cat_ids["Ada"]).json()

    assert len(body["actogram"]) == 90
    assert body["profile"]["recent_visits"] > 0
    assert len(body["profile"]["blocks"]) == 8
    assert body["heatmap"]["ready"] is True


def test_the_heatmap_is_withheld_until_eight_weeks(client, parts, tmp_path):
    _, store, _ = parts
    build(tmp_path, [CatPlan("Ada", 10.0)], days=30, store=store, end=datetime.now(UTC))

    body = get(client, "/api/rhythm").json()

    assert body["heatmap"]["ready"] is False
    assert body["heatmap"]["need_days"] > 0


def test_every_cat_insights_period_pages_back_past_a_year(client, world):
    cat = world.cat_ids["Ada"]

    everything = get(client, f"/api/cats/{cat}/insights", period="all").json()
    far = get(client, f"/api/cats/{cat}/insights", period="week", offset=2000)

    assert len(everything["points"]) >= 4
    assert everything["has_earlier"] is False
    assert everything["tracked_from"] is not None
    assert far.status_code == 200 and far.json()["has_earlier"] is False


def test_paging_past_year_one_is_refused_not_a_server_error(client, world):
    cat = world.cat_ids["Ada"]

    assert get(client, f"/api/cats/{cat}/insights", period="year", offset=5200).status_code in (
        400,
        422,
    )


def test_the_earliest_visit_is_reachable_by_paging_events(client, world):
    first = get(client, "/api/events", limit=500, kind="visit").json()
    seen = {event["id"] for event in first}
    cursor = first[-1]["cursor"]
    while True:
        page = get(client, "/api/events", limit=500, kind="visit", before=cursor).json()
        if not page:
            break
        seen |= {event["id"] for event in page}
        cursor = page[-1]["cursor"]

    assert seen == set(world.visit_ids)

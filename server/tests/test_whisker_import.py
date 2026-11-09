from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local.cats import CatTracker
from whisker_local.insights import Insights
from whisker_local.store import Store
from whisker_local.whisker_import import BatchNotFound, Importer, NotWhiskerExport, parse

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
REFERENCE = date(2026, 11, 8)
AUTH = {"Authorization": "Bearer right-key"}
HEADER = "Activity,Timestamp,Value\n"


def rows(*lines: str) -> str:
    return HEADER + "\n".join(lines) + "\n"


# Invented rows, newest first like the real export. The 1 AM rows fall in the hour that
# America/New_York lives through twice on 1 November 2026.
SAMPLE = rows(
    "Weight recorded,11/8 at 6:51 PM,10.1 lbs",
    "Cat detected,11/8 at 6:49 PM,-",
    "Clean Cycle Complete,11/8 at 3:00 PM,-",
    "Clean Cycle In Progress,11/8 at 2:45 PM,-",
    "Cycle interrupted,11/8 at 2:44 PM,-",
    "Weight recorded,11/8 at 8:00 AM,8.0 lbs",
    "Cat detected,11/8 at 7:58 AM,-",
    "Cat detected,11/7 at 9:00 PM,-",
    "Weight recorded,11/7 at 5:00 PM,17.5 lbs",
    "Clean Cycle Complete,11/1 at 1:10 AM,-",
    "Cycle interrupted,11/1 at 1:08 AM,-",
    "Clean Cycle In Progress,11/1 at 1:07 AM,-",
    "Cat detected,11/1 at 1:00 AM,-",
    "Weight recorded,10/31 at 11:30 PM,10.0 lbs",
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


@pytest.fixture
def cats(store: Store) -> CatTracker:
    return CatTracker(store, NEW_YORK, clock=lambda: NOW)


@pytest.fixture
def importer(store: Store, cats: CatTracker) -> Importer:
    return Importer(store, cats, NEW_YORK, clock=lambda: NOW)


def two_cats(cats: CatTracker) -> tuple[int, int]:
    return cats.create_cat("Alpha", 10.0)["id"], cats.create_cat("Beta", 8.0)["id"]


def instant(local: str) -> datetime:
    return datetime.fromisoformat(local).replace(tzinfo=NEW_YORK).astimezone(UTC)


def stored_times(store: Store) -> list[str]:
    return [e["at"] for e in store.events(limit=500)]


def label_count(store: Store) -> int:
    return store._db.execute("SELECT COUNT(*) FROM visit_labels").fetchone()[0]


def test_rows_are_mapped_and_unusable_ones_are_counted(importer: Importer):
    summary = importer.run(SAMPLE, reference=REFERENCE, dry_run=True)
    assert summary["rows_read"] == 14
    assert summary["visits"] == 6
    assert summary["clean_cycles"] == 2
    assert summary["cycles_interrupted"] == 2
    assert summary["cycles_in_progress"] == 2
    assert summary["unreadable_rows"] == 0


def test_a_detection_counts_only_when_no_weight_follows_it(importer: Importer):
    summary = importer.run(SAMPLE, reference=REFERENCE, dry_run=True)
    # 6:49 PM and 7:58 AM have a weight within minutes; 9:00 PM and 1:00 AM do not.
    assert summary["cat_detections_without_weight"] == 2


def test_dry_run_writes_nothing_and_has_the_same_summary_as_the_real_run(
    importer: Importer, store: Store, cats: CatTracker
):
    two_cats(cats)
    dry = importer.run(SAMPLE, reference=REFERENCE, dry_run=True)
    assert store.events(limit=100) == []
    assert store.import_batches() == []
    real = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    assert dry.keys() == real.keys()
    assert {key for key in dry if dry[key] != real[key]} == {
        "dry_run",
        "batch_id",
        "visits_matched_to_cat",
    }
    assert real["batch_id"]
    assert real["visits_matched_to_cat"] == 3


def test_a_visit_is_stored_like_a_live_one(importer: Importer, store: Store, cats: CatTracker):
    alpha, beta = two_cats(cats)
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    visits = {v["at"]: v for v in store.visits()}
    first = visits["2026-11-08T23:51:00.000+00:00"]
    assert first["detail"]["weight_lb"] == 10.1
    assert (first["label"], first["cat_id"], first["label_source"]) == ("cat", alpha, "auto")
    assert visits["2026-11-08T13:00:00.000+00:00"]["cat_id"] == beta
    outlier = visits["2026-11-07T22:00:00.000+00:00"]
    assert (outlier["label"], outlier["cat_id"]) == ("unknown", None)
    cycles = store.events(limit=10, kinds=["clean_cycle"])
    assert [c["at"] for c in cycles] == [
        "2026-11-08T20:00:00.000+00:00",
        "2026-11-01T06:10:00.000+00:00",
    ]


def test_imported_history_reaches_insights_and_cat_summaries(
    importer: Importer, store: Store, cats: CatTracker
):
    alpha, _ = two_cats(cats)
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    report = Insights(store, NEW_YORK, clock=lambda: NOW).report(
        date(2026, 10, 31), REFERENCE, None
    )
    assert report["overall"]["visits"] == 6
    assert report["overall"]["clean_cycles"] == 2
    assert cats.cat(alpha)["summary"]["latest_weight_lb"] == 10.1
    assert store.latest_event_at("clean_cycle") == "2026-11-08T20:00:00.000+00:00"


def test_imported_visits_without_a_cat_stay_visible_but_do_not_prompt(
    importer: Importer, store: Store, cats: CatTracker
):
    two_cats(cats)
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    shown = [
        e for e in store.events(limit=100, kinds=["visit"]) if e["detail"]["weight_lb"] == 17.5
    ]
    assert len(shown) == 1
    assert cats.needing_cat() == 0
    assert cats.latest_unassigned_visit() is None
    weighed = [v for v in cats.visits(days=30, needs_cat=True) if v["detail"]["weight_lb"]]
    assert [v["detail"]["weight_lb"] for v in weighed] == [17.5]


def test_a_live_visit_with_no_cat_still_prompts(importer: Importer, store: Store, cats: CatTracker):
    two_cats(cats)
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    store.add_event(NOW - timedelta(minutes=1), "visit", {"duration_s": 40, "weight_lb": 17.5})
    assert cats.needing_cat() == 1


def test_the_repeated_hour_keeps_every_row_in_order(importer: Importer, store: Store):
    text = rows(
        "Clean Cycle Complete,11/1 at 3:00 AM,-",
        "Clean Cycle Complete,11/1 at 1:10 AM,-",
        "Clean Cycle Complete,11/1 at 1:07 AM,-",
        "Clean Cycle Complete,11/1 at 1:00 AM,-",
        "Clean Cycle Complete,10/31 at 11:00 PM,-",
    )
    summary = importer.run(text, reference=REFERENCE, dry_run=False)
    assert summary["clean_cycles"] == 5
    # After the clock went back the 1 AM rows are in standard time, which keeps the order.
    assert stored_times(store) == [
        "2026-11-01T08:00:00.000+00:00",
        "2026-11-01T06:10:00.000+00:00",
        "2026-11-01T06:07:00.000+00:00",
        "2026-11-01T06:00:00.000+00:00",
        "2026-11-01T03:00:00.000+00:00",
    ]


def test_rows_on_both_sides_of_the_clock_change_are_told_apart():
    text = rows(
        "Cat detected,11/1 at 3:00 AM,-",
        "Cat detected,11/1 at 1:10 AM,-",
        "Cat detected,11/1 at 1:50 AM,-",
        "Cat detected,11/1 at 1:20 AM,-",
    )
    found = [at.isoformat() for at in parse(text, NEW_YORK, REFERENCE).detections]
    assert found == [
        "2026-11-01T08:00:00+00:00",
        "2026-11-01T06:10:00+00:00",
        "2026-11-01T05:50:00+00:00",
        "2026-11-01T05:20:00+00:00",
    ]


def test_a_time_in_the_repeated_hour_with_nothing_above_it_is_the_first_pass():
    text = rows("Clean Cycle Complete,11/1 at 1:30 AM,-")
    results = {parse(text, NEW_YORK, REFERENCE).completes[0] for _ in range(3)}
    assert results == {instant("2026-11-01T01:30")}
    assert instant("2026-11-01T01:30").isoformat() == "2026-11-01T05:30:00+00:00"


def test_a_date_after_the_reference_belongs_to_the_previous_year():
    text = rows(
        "Clean Cycle Complete,1/11 at 9:00 AM,-",
        "Clean Cycle Complete,1/10 at 9:00 AM,-",
        "Clean Cycle Complete,1/9 at 9:00 AM,-",
        "Clean Cycle Complete,12/30 at 9:00 AM,-",
    )
    completes = parse(text, NEW_YORK, date(2026, 1, 10)).completes
    assert [at.astimezone(NEW_YORK).year for at in completes] == [2025, 2026, 2026, 2025]


def test_the_reference_defaults_to_today_in_the_server_timezone(importer: Importer):
    summary = importer.run(SAMPLE, reference=None, dry_run=True)
    assert summary["reference_date"] == "2026-11-08"


def test_leap_day_lands_on_a_year_that_has_one():
    found = parse(rows("Clean Cycle Complete,2/29 at 9:00 AM,-"), NEW_YORK, date(2026, 3, 1))
    assert found.completes[0].astimezone(NEW_YORK).date() == date(2024, 2, 29)


def test_importing_the_same_file_twice_adds_nothing(importer: Importer, store: Store):
    first = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    before = stored_times(store)
    second = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    assert stored_times(store) == before
    assert (second["visits"], second["clean_cycles"], second["batch_id"]) == (0, 0, None)
    assert second["already_imported"] == first["visits"] + first["clean_cycles"]
    assert len(store.import_batches()) == 1


def test_a_second_overlapping_file_adds_only_what_is_new(importer: Importer):
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    more = SAMPLE + "Weight recorded,10/30 at 9:00 AM,8.1 lbs\n"
    summary = importer.run(more, reference=REFERENCE, dry_run=True)
    assert summary["visits"] == 1
    assert summary["already_imported"] == 8


def test_two_rows_in_one_minute_are_one_visit_in_a_dry_and_a_real_run(importer: Importer):
    text = rows(
        "Weight recorded,11/8 at 9:00 AM,9.0 lbs", "Weight recorded,11/8 at 9:00 AM,9.1 lbs"
    )
    dry = importer.run(text, reference=REFERENCE, dry_run=True)
    real = importer.run(text, reference=REFERENCE, dry_run=False)
    assert dry["visits"] == real["visits"] == 1


def test_nothing_at_or_after_the_first_live_record_is_imported(importer: Importer, store: Store):
    store.add_event(instant("2026-11-08T08:00"), "fault", {"code": 1})
    summary = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    # The 8:00 AM weight sits exactly on it, so it goes too, with the 3 PM cycle and 6:51 PM.
    assert summary["after_live_data"] == 3
    assert summary["live_data_starts_at"] == "2026-11-08T13:00:00.000+00:00"
    imported = [e["at"] for e in store.events(limit=100) if e["detail"].get("imported")]
    assert imported
    assert all(at < "2026-11-08T13:00:00.000+00:00" for at in imported)


def test_a_raw_message_also_marks_where_live_data_starts(importer: Importer, store: Store):
    store.add_message(instant("2026-11-08T08:00"), "status", {})
    summary = importer.run(SAMPLE, reference=REFERENCE, dry_run=True)
    assert summary["live_data_starts_at"] == "2026-11-08T13:00:00.000+00:00"


def test_an_import_does_not_move_the_start_of_live_data(importer: Importer, store: Store):
    importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    assert store.first_live_at() is None


def test_undo_removes_the_batch_and_nothing_else(
    importer: Importer, store: Store, cats: CatTracker
):
    alpha, _ = two_cats(cats)
    live = store.add_event(NOW + timedelta(hours=8), "visit", {"duration_s": 40, "weight_lb": 10.2})
    store.add_event(NOW + timedelta(hours=9), "clean_cycle", {"count": 3, "drawer_percent": 20})
    cats.on_visit_saved(live)
    before = store.events(limit=500)
    batch = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)
    removed = importer.undo(batch["batch_id"])
    assert removed == {"batch_id": batch["batch_id"], "removed": 8}
    assert store.events(limit=500) == before
    assert store.import_batches() == []
    assert store.visit(live)["cat_id"] == alpha
    assert label_count(store) == 1


def test_undo_takes_a_hand_made_label_on_an_imported_visit_with_it(
    importer: Importer, store: Store, cats: CatTracker
):
    alpha, _ = two_cats(cats)
    batch = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)["batch_id"]
    outlier = next(v for v in store.visits() if v["detail"]["weight_lb"] == 17.5)
    cats.label_visit(outlier["id"], "cat", alpha)
    importer.undo(batch)
    assert label_count(store) == 0


def test_undo_only_removes_the_named_batch(importer: Importer, store: Store):
    first = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)["batch_id"]
    later = importer.run(
        rows("Weight recorded,10/1 at 9:00 AM,9.0 lbs"), reference=REFERENCE, dry_run=False
    )["batch_id"]
    importer.undo(later)
    assert [b["batch_id"] for b in store.import_batches()] == [first]
    with pytest.raises(BatchNotFound):
        importer.undo(later)


def test_batches_list_newest_first(store: Store, cats: CatTracker):
    clock = [NOW]
    importer = Importer(store, cats, NEW_YORK, clock=lambda: clock[0])
    old = importer.run(SAMPLE, reference=REFERENCE, dry_run=False)["batch_id"]
    clock[0] = NOW + timedelta(hours=1)
    text = rows("Weight recorded,10/1 at 9:00 AM,9.0 lbs")
    new = importer.run(text, reference=REFERENCE, dry_run=False)["batch_id"]
    listed = importer.batches()
    assert [b["batch_id"] for b in listed] == [new, old]
    assert (listed[1]["visits"], listed[1]["clean_cycles"]) == (6, 2)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "Date,Cat,Weight\n1,2,3\n",
        "id,started_at,ended_at\n1,2,3\n",
        HEADER,
        rows("Something else,11/8 at 6:51 PM,-"),
    ],
)
def test_other_files_are_refused(text: str):
    with pytest.raises(NotWhiskerExport):
        parse(text, NEW_YORK, REFERENCE)


def test_a_byte_order_mark_and_odd_rows_are_tolerated():
    text = "﻿" + rows(
        "Weight recorded,11/8 at 6:51 PM,10.1 lbs",
        "Weight recorded,11/8 at 6:50 PM,heavy",
        "Weight recorded,13/40 at 6:50 PM,9 lbs",
        "Weight recorded,11/8 at 13:50 PM,9 lbs",
        "Weight recorded,11/8 at 6:50 PM",
    )
    parsed = parse(text, NEW_YORK, REFERENCE)
    assert len(parsed.weights) == 1
    assert parsed.unreadable == 4


def test_12_am_and_12_pm_are_midnight_and_noon():
    text = rows(
        "Clean Cycle Complete,11/8 at 12:05 AM,-", "Clean Cycle Complete,11/8 at 12:05 PM,-"
    )
    completes = parse(text, NEW_YORK, REFERENCE).completes
    assert [at.astimezone(NEW_YORK).hour for at in completes] == [0, 12]


def test_routes_need_the_key(client: TestClient):
    assert client.post("/api/import/whisker-activity", content=SAMPLE).status_code == 401
    assert client.get("/api/import/batches").status_code == 401
    assert client.delete("/api/import/batches/aaaaaaaaaaaa").status_code == 401


def test_api_dry_run_then_import_then_undo(client: TestClient):
    url = "/api/import/whisker-activity"
    params = {"reference_date": "2026-11-08"}
    dry = client.post(url, params={**params, "dry_run": "true"}, content=SAMPLE, headers=AUTH)
    assert dry.status_code == 200
    assert dry.json()["visits"] == 6
    assert client.get("/api/import/batches", headers=AUTH).json() == []

    real = client.post(url, params=params, content=SAMPLE, headers=AUTH).json()
    batches = client.get("/api/import/batches", headers=AUTH).json()
    assert [b["batch_id"] for b in batches] == [real["batch_id"]]
    events = client.get("/api/events", params={"limit": 100}, headers=AUTH).json()
    assert len(events) == 8
    assert all(e["detail"]["imported"] is True for e in events)

    path = f"/api/import/batches/{real['batch_id']}"
    assert client.delete(path, headers=AUTH).json()["removed"] == 8
    assert client.get("/api/events", headers=AUTH).json() == []
    assert client.delete(path, headers=AUTH).status_code == 404
    assert client.delete("/api/import/batches/not-a-batch", headers=AUTH).status_code == 404


def test_api_gives_a_plain_message_for_a_file_that_is_not_an_export(client: TestClient):
    response = client.post("/api/import/whisker-activity", content=b"a,b\n1,2\n", headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Not a Whisker activity export")


def test_api_gives_the_same_message_for_binary_content(client: TestClient):
    response = client.post("/api/import/whisker-activity", content=b"\xff\xfe\x00", headers=AUTH)
    assert response.status_code == 422
    assert "Whisker activity export" in response.json()["detail"]


def test_api_refuses_a_reference_date_in_the_future(client: TestClient):
    response = client.post(
        "/api/import/whisker-activity",
        params={"reference_date": "2999-01-01"},
        content=SAMPLE,
        headers=AUTH,
    )
    assert response.status_code == 422


def test_api_takes_a_body_bigger_than_a_json_document_but_not_a_huge_one(client: TestClient):
    body = HEADER + "Cat detected,11/8 at 6:51 PM,-\n" * 8000
    assert len(body) > 64 * 1024
    ok = client.post(
        "/api/import/whisker-activity",
        params={"dry_run": "true", "reference_date": "2026-11-08"},
        content=body,
        headers=AUTH,
    )
    assert ok.status_code == 200
    huge = client.post(
        "/api/import/whisker-activity", content=b"x" * (2 * 1024 * 1024 + 1), headers=AUTH
    )
    assert huge.status_code == 413

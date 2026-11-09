"""History and live visits count the same way: one visit per detection, not per weight row."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local.cats import CatTracker
from whisker_local.store import Store
from whisker_local.whisker_import import Importer, parse

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
REFERENCE = date(2026, 11, 8)
AUTH = {"Authorization": "Bearer right-key"}
HEADER = "Activity,Timestamp,Value\n"
BATCH = "aaaaaaaaaaaa"


def rows(*lines: str) -> str:
    return HEADER + "\n".join(lines) + "\n"


# Newest first, like the real export. Detections and weights are invented.
EPISODES = rows(
    "Weight recorded,11/8 at 9:12 AM,8.4 lbs",
    "Weight recorded,11/8 at 9:03 AM,8.9 lbs",
    "Cat detected,11/8 at 9:01 AM,-",
    "Cat detected,11/8 at 7:00 AM,-",
    "Weight recorded,11/8 at 6:04 AM,10.2 lbs",
    "Weight recorded,11/8 at 6:02 AM,8.7 lbs",
    "Cat detected,11/8 at 6:00 AM,-",
    "Weight recorded,11/7 at 5:00 PM,9.1 lbs",
    "Cat detected,11/7 at 4:58 PM,-",
    "Weight recorded,11/7 at 11:00 AM,8.8 lbs",
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
    return cats.create_cat("Alpha", 10.2)["id"], cats.create_cat("Beta", 8.7)["id"]


def weights(store: Store) -> list[float | None]:
    return [v["detail"]["weight_lb"] for v in store.visits()]


def legacy_import(store: Store, text: str, cats: CatTracker, zone: tzinfo = NEW_YORK) -> None:
    """The way earlier versions stored it: one visit per weight row, nothing else."""
    parsed = parse(text, zone, REFERENCE)
    mark = {"imported": True, "import_batch": BATCH, "imported_at": "2026-11-08T18:00:00Z"}
    for at, weight in parsed.weights:
        store.add_event(at, "visit", {"weight_lb": weight, **mark})
    cats.relabel(since=min(at for at, _ in parsed.weights))


def test_a_visit_is_a_detection_not_a_weight_row(importer: Importer, store: Store):
    summary = importer.run(EPISODES, reference=REFERENCE, dry_run=False)

    assert summary["visits"] == 6
    assert summary["visits_without_weight"] == 1
    # A weight with no detection is a visit, two readings of one cat are one visit, a detection
    # with no weight is a visit of unknown weight, and two cats in one stretch are two visits.
    assert weights(store) == [8.8, 9.1, 8.7, 10.2, None, 8.65]


def test_two_readings_of_one_detection_are_averaged_and_counted(importer: Importer, store: Store):
    importer.run(EPISODES, reference=REFERENCE, dry_run=False)

    readings = {v["detail"]["weight_lb"]: v["detail"]["readings"] for v in store.visits()}
    assert readings[8.65] == 2
    assert readings[10.2] == 1


def test_a_detection_with_no_weight_has_no_cat(importer: Importer, store: Store, cats: CatTracker):
    two_cats(cats)
    importer.run(EPISODES, reference=REFERENCE, dry_run=False)

    [bare] = [v for v in store.visits() if v["detail"]["weight_lb"] is None]
    assert bare["label"] == "unknown"
    assert bare["detail"]["detected_at"] is not None


def test_the_summary_counts_episodes_in_a_dry_run_too(importer: Importer):
    summary = importer.run(EPISODES, reference=REFERENCE, dry_run=True)
    assert (summary["visits"], summary["visits_without_weight"]) == (6, 1)


def test_importing_the_same_file_twice_adds_nothing(importer: Importer, store: Store):
    importer.run(EPISODES, reference=REFERENCE, dry_run=False)
    second = importer.run(EPISODES, reference=REFERENCE, dry_run=False)

    assert second["visits"] == 0
    assert len(store.visits()) == 6


def test_reprocessing_old_history_gives_what_a_fresh_import_gives(
    importer: Importer, store: Store, cats: CatTracker, tmp_path: Path
):
    two_cats(cats)
    legacy_import(store, EPISODES, cats)
    assert len(store.visits()) == 6

    dry = importer.reprocess(EPISODES, reference=REFERENCE, dry_run=True)
    assert (dry["visits_before"], dry["visits_after"]) == (6, 6)
    assert len(store.visits()) == 6

    real = importer.reprocess(EPISODES, reference=REFERENCE, dry_run=False)
    assert (real["visits_after"], real["merged"], real["added_without_weight"]) == (6, 1, 1)
    assert (tmp_path / "backups" / real["backup"]).is_file()

    fresh = Store(tmp_path / "fresh.db")
    fresh_importer = Importer(fresh, CatTracker(fresh, NEW_YORK, clock=lambda: NOW), NEW_YORK)
    fresh_importer.run(EPISODES, reference=REFERENCE, dry_run=False)
    assert weights(store) == weights(fresh)
    assert [v["at"] for v in store.visits()] == [v["at"] for v in fresh.visits()]


def test_reprocessing_twice_changes_nothing_the_second_time(
    importer: Importer, store: Store, cats: CatTracker
):
    legacy_import(store, EPISODES, cats)
    importer.reprocess(EPISODES, reference=REFERENCE, dry_run=False)
    first = [(v["id"], v["at"], v["detail"]) for v in store.visits()]

    again = importer.reprocess(EPISODES, reference=REFERENCE, dry_run=False)

    assert [(v["id"], v["at"], v["detail"]) for v in store.visits()] == first
    assert (again["merged"], again["added_without_weight"]) == (0, 0)


def test_reprocessing_carries_a_hand_made_label_from_a_merged_away_reading(
    importer: Importer, store: Store, cats: CatTracker
):
    _, beta = two_cats(cats)
    legacy_import(store, EPISODES, cats)
    second_reading = next(v for v in store.visits() if v["detail"]["weight_lb"] == 8.4)
    cats.label_visit(second_reading["id"], "cat", beta)

    importer.reprocess(EPISODES, reference=REFERENCE, dry_run=False)

    merged = next(v for v in store.visits() if v["detail"]["weight_lb"] == 8.65)
    assert (merged["cat_id"], merged["label_source"]) == (beta, "user")


def test_reprocessing_leaves_live_visits_alone(importer: Importer, store: Store, cats: CatTracker):
    legacy_import(store, EPISODES, cats)
    live = store.add_event(NOW - timedelta(days=30), "visit", {"weight_lb": 9.0, "origin": "live"})

    importer.reprocess(EPISODES, reference=REFERENCE, dry_run=False)

    assert store.visit(live)["detail"] == {"weight_lb": 9.0, "origin": "live"}


def test_api_reprocess_dry_run_then_apply(client: TestClient, parts):
    _, store, _ = parts
    legacy_import(store, EPISODES, CatTracker(store, UTC, clock=lambda: NOW), UTC)
    url = "/api/import/whisker-activity"
    params = {"reference_date": "2026-11-08", "reprocess": "true"}

    dry = client.post(url, params={**params, "dry_run": "true"}, content=EPISODES, headers=AUTH)
    assert dry.status_code == 200
    assert (dry.json()["visits_before"], dry.json()["visits_after"]) == (6, 6)

    real = client.post(url, params=params, content=EPISODES, headers=AUTH).json()
    assert real["visits_after"] == 6
    assert len(store.visits()) == 6

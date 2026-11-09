from __future__ import annotations

import io
import itertools
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from whisker_local import demo
from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.cat_insights import CatInsights
from whisker_local.cats import CatTracker
from whisker_local.config import Config
from whisker_local.demo import CatSpec, DemoModeMismatch, ensure_mode, seed
from whisker_local.photos import CatPhotos, NotAnImage
from whisker_local.simulator import STARTING_DRAWER_PERCENT
from whisker_local.store import Store

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer right-key"}
SPECS = [CatSpec("Miso", 10.1), CatSpec("Pip", 8.7)]


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db" / "h.db")


@pytest.fixture
def photos(tmp_path: Path) -> CatPhotos:
    return CatPhotos(tmp_path / "db" / "cat-photos")


def seeded(store: Store, photos: CatPhotos, **options) -> dict:
    return seed(store, photos, SPECS, days=60, now=NOW, rng=random.Random(3), **options)


def kinds(store: Store) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in store.events(limit=500):
        counts[event["kind"]] = counts.get(event["kind"], 0) + 1
    return counts


def test_the_spec_takes_a_name_a_weight_and_an_optional_photo():
    assert CatSpec.parse("Miso:10.1") == CatSpec("Miso", 10.1, None)
    assert CatSpec.parse("Miso:10.1:photos/a.jpg").photo == Path("photos/a.jpg")


@pytest.mark.parametrize(
    "text", ["Miso", "Miso:heavy", ":10", "Miso:1", "Miso:99", "x" * 41 + ":9"]
)
def test_a_bad_spec_is_refused(text: str):
    with pytest.raises(ValueError, match=r"NAME:WEIGHT|name|weight"):
        CatSpec.parse(text)


def test_seeding_fills_an_empty_database_with_every_kind_of_history(store, photos):
    summary = seeded(store, photos)

    assert summary["cats"] == 2
    assert summary["visits"] > 200
    counts = kinds(store)
    assert counts["visit"] >= 200
    assert counts["clean_cycle"] > 100
    assert counts["drawer_moved"] >= 1
    assert counts["alert"] >= 2
    assert {cat["name"] for cat in store.cats()} == {"Miso", "Pip"}
    assert set(store.maintenance_last_done()) == {"filter", "deep_clean", "globe_clean"}
    assert demo.is_marked(store)


def test_seeded_visits_are_labelled_and_a_few_are_left_for_the_owner(store, photos):
    summary = seeded(store, photos)

    visits = store.visits()
    labelled = [v for v in visits if v["label"] == "cat"]
    assert len(labelled) > 0.9 * len(visits)
    assert 1 <= summary["needing_a_cat"] <= 4
    assert any(v["label"] == "not_cat" and v["label_source"] == "user" for v in visits)
    assert {v["cat_id"] for v in labelled} == {cat["id"] for cat in store.cats()}


def test_each_cat_gets_visits_near_its_own_weight(store, photos):
    seeded(store, photos)
    for cat, expected in zip(store.cats(), (10.1, 8.7), strict=True):
        weights = [v["detail"]["weight_lb"] for v in store.visits(cat_id=cat["id"])]
        assert abs(sum(weights) / len(weights) - expected) < 0.3


def test_the_history_reaches_back_as_far_as_asked_and_not_into_the_future(store, photos):
    seeded(store, photos)
    visits = store.visits()
    assert (NOW - datetime.fromisoformat(visits[0]["at"])).days >= 58
    assert datetime.fromisoformat(visits[-1]["at"]) < NOW


def test_the_drawer_is_emptied_now_and_then(store, photos):
    seeded(store, photos)
    levels = [
        e["detail"]["drawer_percent"]
        for e in reversed(store.events(limit=500, kinds=["clean_cycle"]))
    ]
    assert max(levels) < 100
    assert any(b < a - 40 for a, b in itertools.pairwise(levels))


def test_seeded_data_feeds_the_insights_screen(store, photos):
    seeded(store, photos)
    tracker = CatTracker(store, clock=lambda: NOW)
    miso = store.cats()[0]["id"]

    report = CatInsights(store, tracker).report(miso, "month", 0)

    assert report["visits"]["average"] > 1
    assert report["weight"]["average"] == pytest.approx(10.1, abs=0.4)
    assert report["has_earlier"] is True
    # Demo visits spread over the day, so no single hour stands out as a habit.
    assert report["overview"]["common_visit_hour"] is None


def test_the_same_seed_gives_the_same_history(tmp_path: Path):
    def run(name: str) -> list:
        local = Store(tmp_path / name / "h.db")
        seed(local, CatPhotos(tmp_path / name / "p"), SPECS, days=20, now=NOW, rng=random.Random(5))
        return [(v["at"], v["detail"]["weight_lb"]) for v in local.visits()]

    assert run("a") == run("b")


def test_seeding_refuses_a_database_that_has_any_history(store, photos):
    store.add_event(NOW, "visit", {"weight_lb": 9.0})
    with pytest.raises(DemoModeMismatch, match="already holds history"):
        seeded(store, photos)
    assert not demo.is_marked(store)
    assert store.cats() == []


@pytest.mark.parametrize("fill", ["cat", "message", "maintenance"])
def test_every_kind_of_real_data_blocks_seeding(store, photos, fill: str):
    if fill == "cat":
        store.add_cat("Real", None, NOW)
    elif fill == "message":
        store.add_message(NOW, "state", {})
    else:
        store.add_maintenance("filter", NOW.date(), NOW)
    with pytest.raises(DemoModeMismatch):
        seeded(store, photos)


def test_seeding_twice_is_refused(store, photos):
    seeded(store, photos)
    with pytest.raises(DemoModeMismatch):
        seeded(store, photos)


def test_a_photo_is_stored_as_a_clean_jpeg(store, photos, tmp_path: Path):
    source = tmp_path / "cat.png"
    Image.new("RGB", (3000, 2000), "gray").save(source)

    seed(store, photos, [CatSpec("Miso", 10.1, source)], days=5, now=NOW, rng=random.Random(1))

    stored = Image.open(photos.path(store.cats()[0]["id"]))
    assert stored.format == "JPEG"
    assert max(stored.size) == 1600


def test_a_bad_photo_stops_before_anything_is_written(store, photos, tmp_path: Path):
    source = tmp_path / "cat.jpg"
    source.write_text("not a picture")
    with pytest.raises(NotAnImage, match="not a usable image"):
        seed(store, photos, [CatSpec("Miso", 10.1, source)], days=5, now=NOW)
    with pytest.raises(NotAnImage, match="cannot read"):
        seed(store, photos, [CatSpec("Miso", 10.1, tmp_path / "none.jpg")], days=5, now=NOW)
    assert store.is_empty()
    assert not demo.is_marked(store)


@pytest.mark.parametrize(
    ("specs", "days"),
    [([], 10), (SPECS, 0), (SPECS, 5000), ([CatSpec("Miso", 9), CatSpec("MISO", 10)], 10)],
)
def test_bad_arguments_are_refused(store, photos, specs, days):
    with pytest.raises(ValueError):
        seed(store, photos, specs, days=days, now=NOW)
    assert store.is_empty()


def test_demo_mode_on_a_fresh_database_marks_it(store):
    ensure_mode(store, demo_mode=True)
    assert demo.is_marked(store)
    ensure_mode(store, demo_mode=True)


def test_demo_mode_refuses_a_database_with_real_history(store):
    store.add_event(NOW, "visit", {"weight_lb": 9.0})
    with pytest.raises(DemoModeMismatch, match="real history"):
        ensure_mode(store, demo_mode=True)
    assert not demo.is_marked(store)


def test_a_sample_database_refuses_to_run_as_real(store, photos):
    seeded(store, photos)
    with pytest.raises(DemoModeMismatch, match=r"end-demo\.sh"):
        ensure_mode(store, demo_mode=False)
    ensure_mode(store, demo_mode=True)


def test_a_real_database_runs_without_demo_mode(store):
    store.add_event(NOW, "visit", {"weight_lb": 9.0})
    ensure_mode(store, demo_mode=False)


def app_client(tmp_path: Path, store: Store, *, demo_mode: bool) -> TestClient:
    config = Config(
        "LR4C000001",
        "right-key",
        "broker",
        1883,
        tmp_path / "db" / "h.db",
        tmp_path,
        demo=demo_mode,
    )
    return TestClient(create_app(config, store, RobotBridge(config, store), run_bridge=False))


def test_status_says_when_the_data_is_sample_data(tmp_path: Path, store):
    with app_client(tmp_path, store, demo_mode=True) as client:
        assert client.get("/api/status", headers=AUTH).json()["demo"] is True


def test_status_says_it_is_not_a_demo_otherwise(tmp_path: Path, store):
    with app_client(tmp_path, store, demo_mode=False) as client:
        assert client.get("/api/status", headers=AUTH).json()["demo"] is False


def test_the_demo_setting_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", "k")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert Config.from_env().demo is False
    monkeypatch.setenv("DEMO_MODE", "1")
    assert Config.from_env().demo is True
    monkeypatch.setenv("DEMO_MODE", "yes")
    assert Config.from_env().demo is False


def test_the_cli_seeds_and_then_refuses_a_second_run(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "h.db"))
    monkeypatch.setenv("TZ", "UTC")
    arguments = ["seed", "--cat", "Miso:10.1", "--cat", "Pip:8.7", "--days", "30"]

    assert demo.main(arguments) == 0
    assert "Seeded 2 cats" in capsys.readouterr().out

    assert demo.main(arguments) == 1
    assert "already holds history" in capsys.readouterr().err


def test_the_cli_reports_a_bad_cat_without_a_traceback(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "h.db"))
    assert demo.main(["seed", "--cat", "Miso"]) == 1
    assert "NAME:WEIGHT" in capsys.readouterr().err
    assert not (tmp_path / "h.db").exists() or Store(tmp_path / "h.db").is_empty()


def test_the_photo_bytes_a_seed_stores_are_what_the_api_serves(tmp_path: Path):
    path = tmp_path / "db" / "h.db"
    local = Store(path)
    out = io.BytesIO()
    Image.new("RGB", (80, 60), "orange").save(out, "PNG")
    source = tmp_path / "c.png"
    source.write_bytes(out.getvalue())
    seed(
        local,
        CatPhotos(path.parent / "cat-photos"),
        [CatSpec("Miso", 10.1, source)],
        days=3,
        now=NOW,
        rng=random.Random(1),
    )
    config = Config("LR4C000001", "right-key", "broker", 1883, path, tmp_path)
    with TestClient(create_app(config, local, RobotBridge(config, local), run_bridge=False)) as c:
        cat_id = c.get("/api/cats", headers=AUTH).json()[0]["id"]
        served = c.get(f"/api/cats/{cat_id}/photo", headers=AUTH)
    assert served.status_code == 200
    assert Image.open(io.BytesIO(served.content)).format == "JPEG"


def test_the_history_ends_at_the_level_the_simulator_starts_from(store, photos):
    seeded(store, photos)
    newest = store.events(limit=1, kinds=["clean_cycle"])[0]
    assert newest["detail"]["drawer_percent"] == STARTING_DRAWER_PERCENT


@pytest.mark.parametrize("days", [1, 2, 3])
@pytest.mark.parametrize("rng_seed", range(6))
def test_a_short_history_ends_at_the_simulator_level_too(store, photos, days, rng_seed):
    seed(store, photos, SPECS, days=days, now=NOW, rng=random.Random(rng_seed))
    newest = store.events(limit=1, kinds=["clean_cycle"])[0]
    assert newest["detail"]["drawer_percent"] == STARTING_DRAWER_PERCENT

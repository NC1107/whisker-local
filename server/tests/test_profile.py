from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}

_VERSION_ONE_CATS = """
CREATE TABLE cats (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color_index INTEGER NOT NULL,
    estimated_weight_lb REAL,
    created_at TEXT NOT NULL,
    archived_at TEXT
);
INSERT INTO cats (name, color_index, estimated_weight_lb, created_at)
VALUES ('Miso', 0, 10.2, '2026-10-01T00:00:00.000+00:00'),
       ('Pip', 1, NULL, '2026-10-02T00:00:00.000+00:00');
"""


def test_a_database_from_before_the_profile_keeps_its_cats(tmp_path: Path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(_VERSION_ONE_CATS)
    old.commit()
    old.close()

    store = Store(path)
    miso, pip = store.cats()

    assert (miso["name"], miso["reference_weight_lb"], miso["sex"]) == ("Miso", 10.2, "unknown")
    assert (miso["breed"], miso["birthday"]) == (None, None)
    assert pip["reference_weight_lb"] is None
    store.close()


def test_migrating_twice_changes_nothing(tmp_path: Path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(_VERSION_ONE_CATS)
    old.commit()
    old.close()
    Store(path).update_cat_profile(1, {"reference_weight_lb": 11.0})

    again = Store(path)

    assert again.cat(1)["reference_weight_lb"] == 11.0


def test_a_cat_can_be_made_with_a_full_profile(client: TestClient):
    response = client.post(
        "/api/cats",
        json={
            "name": "Miso",
            "breed": "  American Shorthair ",
            "birthday": "2022-03-14",
            "sex": "spayed_female",
            "reference_weight_lb": 10.2,
        },
        headers=AUTH,
    )

    cat = response.json()
    assert response.status_code == 201
    assert cat["breed"] == "American Shorthair"
    assert cat["birthday"] == "2022-03-14"
    assert cat["sex"] == "spayed_female"
    assert cat["reference_weight_lb"] == 10.2
    assert cat["has_photo"] is False
    assert cat["photo_version"] is None


def test_a_bare_name_still_makes_a_cat_with_unknowns(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    assert (cat["breed"], cat["birthday"], cat["sex"]) == (None, None, "unknown")
    assert cat["reference_weight_lb"] is None


def test_the_old_estimate_field_still_sets_the_reference_weight(client: TestClient):
    cat = client.post(
        "/api/cats", json={"name": "Miso", "estimated_weight_lb": 9.5}, headers=AUTH
    ).json()
    assert cat["reference_weight_lb"] == 9.5
    assert cat["estimated_weight_lb"] == 9.5


def test_two_different_reference_weights_are_refused(client: TestClient):
    response = client.post(
        "/api/cats",
        json={"name": "Miso", "estimated_weight_lb": 9.5, "reference_weight_lb": 10},
        headers=AUTH,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"sex": "robot"},
        {"birthday": "2999-01-01"},
        {"birthday": "1950-01-01"},
        {"birthday": "not a date"},
        {"breed": ""},
        {"breed": "x" * 61},
        {"reference_weight_lb": 1},
        {"reference_weight_lb": 41},
    ],
)
def test_bad_profile_values_are_refused(client: TestClient, bad: dict):
    assert client.post("/api/cats", json={"name": "Miso", **bad}, headers=AUTH).status_code == 422


def test_patching_changes_only_what_was_sent(client: TestClient):
    cat = client.post(
        "/api/cats",
        json={"name": "Miso", "breed": "Tabby", "sex": "male", "birthday": "2021-01-01"},
        headers=AUTH,
    ).json()

    response = client.patch(
        f"/api/cats/{cat['id']}", json={"breed": "Siamese", "sex": "neutered_male"}, headers=AUTH
    )

    body = response.json()
    assert (body["breed"], body["sex"], body["birthday"]) == (
        "Siamese",
        "neutered_male",
        "2021-01-01",
    )
    assert body["name"] == "Miso"


def test_patching_null_clears_an_optional_field(client: TestClient):
    cat = client.post(
        "/api/cats", json={"name": "Miso", "breed": "Tabby", "birthday": "2021-01-01"}, headers=AUTH
    ).json()

    body = client.patch(
        f"/api/cats/{cat['id']}", json={"breed": None, "birthday": None}, headers=AUTH
    ).json()

    assert (body["breed"], body["birthday"]) == (None, None)


@pytest.mark.parametrize("field", ["name", "archived", "sex"])
def test_required_fields_cannot_be_cleared(client: TestClient, field: str):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    response = client.patch(f"/api/cats/{cat['id']}", json={field: None}, headers=AUTH)
    assert response.status_code == 422


def test_an_empty_patch_is_refused(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    assert client.patch(f"/api/cats/{cat['id']}", json={}, headers=AUTH).status_code == 422


def test_a_future_birthday_is_refused_on_patch(client: TestClient):
    cat = client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()
    response = client.patch(f"/api/cats/{cat['id']}", json={"birthday": "2999-01-01"}, headers=AUTH)
    assert response.status_code == 422


def test_changing_the_reference_weight_relabels_recent_visits(client: TestClient, parts):
    _, store, _ = parts
    cat = client.post(
        "/api/cats", json={"name": "Miso", "reference_weight_lb": 14}, headers=AUTH
    ).json()
    now = datetime.now(UTC)
    for _ in range(3):
        store.add_event(now, "visit", {"duration_s": 40, "weight_lb": 9.0})
    assert client.get("/api/status", headers=AUTH).json()["visits_needing_cat"] == 3

    client.patch(f"/api/cats/{cat['id']}", json={"reference_weight_lb": 9.0}, headers=AUTH)

    assert client.get("/api/status", headers=AUTH).json()["visits_needing_cat"] == 0


def test_store_refuses_to_set_a_column_that_is_not_profile(tmp_path: Path):
    store = Store(tmp_path / "h.db")
    cat_id = store.add_cat(
        "Miso", None, datetime(2026, 11, 8, tzinfo=UTC), birthday=date(2020, 1, 1)
    )
    with pytest.raises(ValueError, match="not profile fields"):
        store.update_cat_profile(cat_id, {"name": "x"})

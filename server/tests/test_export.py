from __future__ import annotations

import csv
import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from whisker_local.export import visits_csv
from whisker_local.store import Store

AUTH = {"Authorization": "Bearer right-key"}
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "history.db")


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def add_visit(store: Store, at: datetime, weight: float | None = 9.4, duration: int | None = 40):
    return store.add_event(
        at,
        "visit",
        {
            "started": (at - timedelta(seconds=duration or 0)).isoformat(),
            "duration_s": duration,
            "weight_lb": weight,
        },
    )


def test_csv_has_a_header_and_one_row_per_visit_oldest_first(store: Store):
    miso = store.add_cat("Miso", None, NOW)
    first = add_visit(store, NOW - timedelta(days=1), 9.4, 40)
    second = add_visit(store, NOW, None, None)
    store.add_event(NOW, "clean_cycle", {"count": 1})
    store.set_user_label(first, "cat", miso)

    text = visits_csv(store, UTC, None, None)

    assert text.splitlines()[0] == (
        "id,started_at,ended_at,local_date,local_time,duration_s,weight_lb,cat,cat_id,"
        "label,label_source"
    )
    [a, b] = rows(text)
    assert a == {
        "id": str(first),
        "started_at": (NOW - timedelta(days=1, seconds=40)).isoformat(),
        "ended_at": "2026-11-07T18:00:00.000+00:00",
        "local_date": "2026-11-07",
        "local_time": "18:00:00",
        "duration_s": "40",
        "weight_lb": "9.4",
        "cat": "Miso",
        "cat_id": str(miso),
        "label": "cat",
        "label_source": "user",
    }
    assert (b["id"], b["duration_s"], b["weight_lb"], b["cat"], b["label"]) == (
        str(second),
        "",
        "",
        "",
        "unknown",
    )


def test_csv_with_no_visits_is_just_the_header(store: Store):
    assert len(visits_csv(store, UTC, None, None).splitlines()) == 1


def test_csv_range_is_whole_local_days_inclusive(store: Store):
    tokyo = ZoneInfo("Asia/Tokyo")
    add_visit(store, datetime(2026, 11, 7, 14, 59, tzinfo=UTC))  # 23:59 on the 7th in Tokyo
    inside = add_visit(store, datetime(2026, 11, 7, 15, 0, tzinfo=UTC))  # midnight on the 8th
    last = add_visit(store, datetime(2026, 11, 8, 14, 59, tzinfo=UTC))  # 23:59 on the 8th
    add_visit(store, datetime(2026, 11, 8, 15, 0, tzinfo=UTC))  # the 9th

    found = rows(visits_csv(store, tokyo, NOW.date(), NOW.date()))

    assert [r["id"] for r in found] == [str(inside), str(last)]
    assert found[0]["local_date"] == "2026-11-08"
    assert found[0]["local_time"] == "00:00:00"


def test_csv_open_ended_ranges(store: Store):
    add_visit(store, NOW - timedelta(days=3))
    add_visit(store, NOW)
    day = NOW.date()
    assert len(rows(visits_csv(store, UTC, day, None))) == 1
    assert len(rows(visits_csv(store, UTC, None, day - timedelta(days=1)))) == 1


@pytest.mark.parametrize("name", ["=HYPERLINK(1)", "+1", "-1", "@SUM(A1)"])
def test_a_cat_name_cannot_become_a_spreadsheet_formula(store: Store, name: str):
    cat = store.add_cat(name, None, NOW)
    event = add_visit(store, NOW)
    store.set_user_label(event, "cat", cat)

    [row] = rows(visits_csv(store, UTC, None, None))

    assert row["cat"] == f"'{name}"


def test_a_name_with_commas_and_quotes_survives(store: Store):
    cat = store.add_cat('Mr. "Biscuit", Esq.', None, NOW)
    event = add_visit(store, NOW)
    store.set_user_label(event, "cat", cat)

    [row] = rows(visits_csv(store, UTC, None, None))

    assert row["cat"] == 'Mr. "Biscuit", Esq.'


def test_csv_endpoint(client: TestClient, parts):
    _, store, _ = parts
    add_visit(store, datetime.now(UTC))

    response = client.get("/api/export/visits.csv", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="whisker-visits-')
    assert disposition.endswith('.csv"')
    assert len(rows(response.text)) == 1


def test_csv_endpoint_range(client: TestClient, parts):
    _, store, _ = parts
    add_visit(store, datetime(2026, 1, 5, 12, tzinfo=UTC))
    add_visit(store, datetime(2026, 1, 9, 12, tzinfo=UTC))

    found = client.get("/api/export/visits.csv?start=2026-01-08&end=2026-01-09", headers=AUTH).text
    assert [r["local_date"] for r in rows(found)] == ["2026-01-09"]

    bad = client.get("/api/export/visits.csv?start=2026-02-01&end=2026-01-01", headers=AUTH)
    assert bad.status_code == 422


def test_exports_need_the_key(client: TestClient):
    for path in ("/api/export/visits.csv", "/api/export/database"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def open_download(tmp_path: Path, content: bytes) -> sqlite3.Connection:
    target = tmp_path / "downloaded.db"
    target.write_bytes(content)
    return sqlite3.connect(target)


def test_database_backup_is_a_complete_consistent_copy(client: TestClient, parts, tmp_path: Path):
    _, store, _ = parts
    cat = store.add_cat("Miso", 9.4, NOW)
    event = add_visit(store, NOW)
    store.set_user_label(event, "cat", cat)
    store.add_message(NOW, "state", {"robotStatus": 4})
    store.put_json("litter_calibration", {"full_mm": 400})

    response = client.get("/api/export/database", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    assert response.headers["content-disposition"].endswith('.db"')
    copy = open_download(tmp_path, response.content)
    assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert copy.execute("SELECT name FROM cats").fetchall() == [("Miso",)]
    assert copy.execute("SELECT count(*) FROM events").fetchone() == (1,)
    assert copy.execute("SELECT count(*) FROM messages").fetchone() == (1,)
    assert copy.execute("SELECT label FROM visit_labels").fetchall() == [("cat",)]
    kept = copy.execute("SELECT value FROM kv WHERE key != 'identity_version'").fetchall()
    assert kept == [('{"full_mm": 400}',)]
    copy.close()


def test_backup_includes_writes_still_in_the_write_ahead_log(store: Store, tmp_path: Path):
    store.add_event(NOW, "visit", {"duration_s": 1})
    wal = Path(f"{tmp_path / 'history.db'}-wal")
    assert wal.exists() and wal.stat().st_size > 0, "the write has to still be in the log"

    copy_path = store.backup_copy()
    try:
        copy = sqlite3.connect(copy_path)
        assert copy.execute("SELECT count(*) FROM events").fetchone() == (1,)
        copy.close()
    finally:
        copy_path.unlink()
        copy_path.parent.rmdir()


def test_backup_is_one_self_contained_file(store: Store):
    store.add_event(NOW, "visit", {})
    path = store.backup_copy()
    try:
        assert sorted(p.name for p in path.parent.iterdir()) == ["whisker-local.db"]
        copy = sqlite3.connect(path)
        assert copy.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        copy.close()
    finally:
        path.unlink()
        path.parent.rmdir()


def test_backup_ignores_a_transaction_that_has_not_committed(store: Store):
    store.add_event(NOW, "visit", {})
    store._db.execute("BEGIN")
    store._db.execute("INSERT INTO events (at, kind, detail) VALUES ('x', 'visit', '{}')")
    path = store.backup_copy()
    store._db.execute("ROLLBACK")
    try:
        copy = sqlite3.connect(path)
        assert copy.execute("SELECT count(*) FROM events").fetchone() == (1,)
        copy.close()
    finally:
        path.unlink()
        path.parent.rmdir()


def test_the_download_leaves_no_temporary_copy_behind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    client.get("/api/export/database", headers=AUTH)
    assert list(tmp_path.glob("whisker-local-backup-*")) == []


def test_backup_still_works_while_the_live_store_keeps_writing(client: TestClient, parts):
    _, store, _ = parts
    for _ in range(50):
        store.add_event(NOW, "visit", {})

    assert client.get("/api/export/database", headers=AUTH).status_code == 200
    store.add_event(NOW, "visit", {})
    assert store.events(limit=100)[0]["kind"] == "visit"

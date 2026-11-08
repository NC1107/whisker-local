from __future__ import annotations

import base64
import binascii
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (at);
CREATE INDEX IF NOT EXISTS events_by_kind_time ON events (kind, at);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cats (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color_index INTEGER NOT NULL,
    estimated_weight_lb REAL,
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS visit_labels (
    event_id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    cat_id INTEGER,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS visit_labels_by_cat ON visit_labels (cat_id);
CREATE TABLE IF NOT EXISTS maintenance_log (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    done_on TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (kind, done_on)
);
"""


def stamp(at: datetime) -> str:
    """UTC with a fixed width, so that text order in SQLite is time order."""
    return at.astimezone(UTC).isoformat(timespec="milliseconds")


class InvalidCursor(ValueError):
    """A paging cursor that this store did not hand out."""


def encode_cursor(at: str, event_id: int) -> str:
    """Opaque position of one event in the newest-first order, unique even among equal times."""
    return base64.urlsafe_b64encode(f"{at}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, _, raw_id = base64.urlsafe_b64decode(padded).decode().rpartition("|")
        return at, int(raw_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as err:
        raise InvalidCursor(cursor) from err


class Store:
    """SQLite history, kept as one file so that a backup is one file to keep.

    ``messages`` keeps every raw robot message forever. Interpretation of the protocol is
    still changing upstream, and the raw stream is what lets old visits be re-derived later.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Every call comes from the event loop, but it is not always the thread that built us.
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def add_message(self, at: datetime, kind: str, body: Any) -> None:
        self._db.execute(
            "INSERT INTO messages (at, kind, body) VALUES (?, ?, ?)",
            (stamp(at), kind, json.dumps(body)),
        )

    def add_event(self, at: datetime, kind: str, detail: dict[str, Any]) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
            (stamp(at), kind, json.dumps(detail)),
        )
        assert cursor.lastrowid is not None  # noqa: S101
        return cursor.lastrowid

    def events(
        self, *, limit: int, before: str | None = None, kinds: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if before is not None:
            before_at, before_id = decode_cursor(before)
            clauses.append("(at < ? OR (at = ? AND id < ?))")
            params.extend((before_at, before_at, before_id))
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # The only interpolated text is the fixed clause list above; every value is bound.
        query = f"SELECT id, at, kind, detail FROM events {where} ORDER BY at DESC, id DESC LIMIT ?"  # noqa: S608
        rows = self._db.execute(query, (*params, limit))
        return [_event(row) for row in rows]

    def visits_since(self, since: datetime) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, at, kind, detail FROM events "
            "WHERE kind = 'visit' AND at >= ? ORDER BY at, id",
            (stamp(since),),
        )
        return [_event(row) for row in rows]

    def visits(
        self,
        *,
        since: datetime | None = None,
        before: datetime | None = None,
        cat_id: int | None = None,
        needs_cat: bool = False,
        newest_first: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Visits with their labels; a visit nobody has labelled reads as unknown and auto."""
        clauses = ["e.kind = 'visit'"]
        params: list[Any] = []
        if since is not None:
            clauses.append("e.at >= ?")
            params.append(stamp(since))
        if before is not None:
            clauses.append("e.at < ?")
            params.append(stamp(before))
        if cat_id is not None:
            clauses.append("l.label = 'cat' AND l.cat_id = ?")
            params.append(cat_id)
        if needs_cat:
            clauses.append("l.event_id IS NULL")
        direction = "DESC" if newest_first else "ASC"
        # Only fixed clause text and a fixed direction are interpolated; every value is bound.
        query = (
            "SELECT e.id, e.at, e.kind, e.detail, l.label, l.cat_id, l.source "  # noqa: S608
            "FROM events e LEFT JOIN visit_labels l ON l.event_id = e.id "
            f"WHERE {' AND '.join(clauses)} ORDER BY e.at {direction}, e.id {direction}"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [_visit(row) for row in self._db.execute(query, params)]

    def visit(self, event_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT e.id, e.at, e.kind, e.detail, l.label, l.cat_id, l.source "
            "FROM events e LEFT JOIN visit_labels l ON l.event_id = e.id "
            "WHERE e.kind = 'visit' AND e.id = ?",
            (event_id,),
        ).fetchone()
        return None if row is None else _visit(row)

    def set_user_label(self, event_id: int, label: str, cat_id: int | None) -> None:
        self._db.execute(
            "INSERT INTO visit_labels (event_id, label, cat_id, source) VALUES (?, ?, ?, 'user') "
            "ON CONFLICT (event_id) DO UPDATE SET "
            "label = excluded.label, cat_id = excluded.cat_id, source = 'user'",
            (event_id, label, cat_id),
        )

    def clear_label(self, event_id: int) -> None:
        self._db.execute("DELETE FROM visit_labels WHERE event_id = ?", (event_id,))

    def apply_auto_labels(self, decisions: dict[int, int | None]) -> None:
        """Set or drop the automatic label of each visit, and never touch a user's label."""
        self._db.execute("BEGIN")
        try:
            for event_id, cat_id in decisions.items():
                if cat_id is None:
                    self._db.execute(
                        "DELETE FROM visit_labels WHERE event_id = ? AND source = 'auto'",
                        (event_id,),
                    )
                else:
                    self._db.execute(
                        "INSERT INTO visit_labels (event_id, label, cat_id, source) "
                        "VALUES (?, 'cat', ?, 'auto') ON CONFLICT (event_id) DO UPDATE SET "
                        "label = 'cat', cat_id = excluded.cat_id WHERE source = 'auto'",
                        (event_id, cat_id),
                    )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def add_cat(self, name: str, estimated_weight_lb: float | None, now: datetime) -> int:
        """The color index is the count of cats ever made, so an archived cat keeps its color."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            (taken,) = self._db.execute("SELECT COUNT(*) FROM cats").fetchone()
            cursor = self._db.execute(
                "INSERT INTO cats (name, color_index, estimated_weight_lb, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, taken, estimated_weight_lb, stamp(now)),
            )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        assert cursor.lastrowid is not None  # noqa: S101
        return cursor.lastrowid

    def cats(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE archived_at IS NULL"
        query = f"SELECT * FROM cats {where} ORDER BY id"  # noqa: S608
        return [dict(row) for row in self._db.execute(query)]

    def cat(self, cat_id: int) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM cats WHERE id = ?", (cat_id,)).fetchone()
        return None if row is None else dict(row)

    def rename_cat(self, cat_id: int, name: str) -> None:
        self._db.execute("UPDATE cats SET name = ? WHERE id = ?", (name, cat_id))

    def set_cat_archived(self, cat_id: int, archived_at: datetime | None) -> None:
        self._db.execute(
            "UPDATE cats SET archived_at = ? WHERE id = ?",
            (None if archived_at is None else stamp(archived_at), cat_id),
        )

    def add_maintenance(
        self, kind: str, done_on: date, now: datetime
    ) -> tuple[dict[str, Any], bool]:
        """Log one job as done, and say whether it was new; a job is logged once per day."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO maintenance_log (kind, done_on, recorded_at) VALUES (?, ?, ?)",
            (kind, done_on.isoformat(), stamp(now)),
        )
        row = self._db.execute(
            "SELECT * FROM maintenance_log WHERE kind = ? AND done_on = ?",
            (kind, done_on.isoformat()),
        ).fetchone()
        return dict(row), cursor.rowcount == 1

    def maintenance_entries(self, *, limit: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM maintenance_log ORDER BY done_on DESC, id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    def delete_maintenance(self, entry_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM maintenance_log WHERE id = ?", (entry_id,))
        return cursor.rowcount == 1

    def maintenance_last_done(self) -> dict[str, date]:
        """The latest day each job was done, whatever order the entries were logged in."""
        rows = self._db.execute(
            "SELECT kind, MAX(done_on) AS last FROM maintenance_log GROUP BY kind"
        )
        return {row["kind"]: date.fromisoformat(row["last"]) for row in rows}

    def insight_rows(self, since: datetime, before: datetime) -> Iterator[sqlite3.Row]:
        """Visits and clean cycles in a window, reduced to the few columns that get counted.

        Rows are read one at a time and must be consumed before the next database call.
        """
        return iter(
            self._db.execute(
                "SELECT e.kind, e.at, json_extract(e.detail, '$.duration_s') AS duration_s, "
                "l.label, l.cat_id FROM events e "
                "LEFT JOIN visit_labels l ON l.event_id = e.id AND e.kind = 'visit' "
                "WHERE e.kind IN ('visit', 'clean_cycle') AND e.at >= ? AND e.at < ? "
                "ORDER BY e.at, e.id",
                (stamp(since), stamp(before)),
            )
        )

    def backup_copy(self) -> Path:
        """A consistent single-file copy of the database, in a fresh temporary directory.

        SQLite's backup API reads a snapshot through its own connection, so writes that are
        still in the write-ahead log are included and the live connection is not held up.
        """
        folder = Path(tempfile.mkdtemp(prefix="whisker-local-backup-"))
        target = folder / "whisker-local.db"
        source = sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)
                # A restored copy should open as one file, not ask for a -wal file beside it.
                copy.execute("PRAGMA journal_mode=DELETE")
            finally:
                copy.close()
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            source.close()
        return target

    def get_json(self, key: str) -> Any:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value"])

    def put_json(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def _event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "cursor": encode_cursor(row["at"], row["id"]),
        "at": row["at"],
        "kind": row["kind"],
        "detail": json.loads(row["detail"]),
    }


def _visit(row: sqlite3.Row) -> dict[str, Any]:
    return {
        **_event(row),
        "label": row["label"] or "unknown",
        "cat_id": row["cat_id"],
        "label_source": row["source"] or "auto",
    }

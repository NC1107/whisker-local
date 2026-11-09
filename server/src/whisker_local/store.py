from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any

from .identity import Anchor

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
CREATE INDEX IF NOT EXISTS events_by_import ON events (json_extract(detail, '$.import_batch'))
    WHERE json_extract(detail, '$.import_batch') IS NOT NULL;
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

# Added after the first release, all of them new tables, so that an older database gains them
# without any existing row being touched. Dropping them returns it to the earlier shape.
_UPGRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS robot_presence (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    online INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS robot_presence_by_time ON robot_presence (at);
CREATE TABLE IF NOT EXISTS unreliable_ranges (
    id INTEGER PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visit_identity (
    event_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    cat_id INTEGER,
    confidence REAL
);
CREATE TABLE IF NOT EXISTS cat_anchors (
    cat_id INTEGER PRIMARY KEY,
    weight_lb REAL NOT NULL,
    spread_lb REAL NOT NULL,
    visits INTEGER NOT NULL,
    visits_per_day REAL,
    set_at TEXT NOT NULL,
    source TEXT NOT NULL
);
"""
_UPGRADE_TABLES = ("robot_presence", "unreliable_ranges", "visit_identity", "cat_anchors")

# Robot activity that proves the robot was reporting; the app's own commands and the server's
# alerts do not.
_ROBOT_EVENT_KINDS = ("visit", "clean_cycle", "fault", "drawer_moved", "firmware")


_CAT_PROFILE_COLUMNS = {
    "breed": "TEXT",
    "birthday": "TEXT",
    "sex": "TEXT NOT NULL DEFAULT 'unknown'",
}


_CAT_PROFILE_FIELDS = frozenset({"breed", "birthday", "sex", "reference_weight_lb"})


def stamp(at: datetime) -> str:
    """UTC with a fixed width, so that text order in SQLite is time order."""
    return at.astimezone(UTC).isoformat(timespec="milliseconds")


_SQLITE_MIN_INT = -(2**63)
_SQLITE_MAX_INT = 2**63 - 1


class InvalidCursor(ValueError):
    """A paging cursor that this store did not hand out."""


def encode_cursor(at: str, event_id: int) -> str:
    """Opaque position of one event in the newest-first order, unique even among equal times."""
    return base64.urlsafe_b64encode(f"{at}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, _, raw_id = base64.urlsafe_b64decode(padded).decode().rpartition("|")
        event_id = int(raw_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as err:
        raise InvalidCursor(cursor) from err
    # SQLite binds integers as signed 64-bit and raises OverflowError beyond that.
    if not _SQLITE_MIN_INT <= event_id <= _SQLITE_MAX_INT:
        raise InvalidCursor(cursor)
    return at, event_id


class Store:
    """SQLite history, kept as one file so that a backup is one file to keep.

    ``messages`` keeps the raw robot messages for as long as the retention budget allows.
    Interpretation of the protocol is still changing upstream, and the raw stream is what lets
    old visits be re-derived later. Events are the record the app shows and are never pruned.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Every call comes from the event loop, but it is not always the thread that built us.
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._back_up_before_upgrade()
        self._db.executescript(_UPGRADE_SCHEMA)
        self._migrate()

    @property
    def path(self) -> Path:
        return self._path

    def _back_up_before_upgrade(self) -> None:
        """Copy a database that already holds history before it gains the newer tables."""
        names = {
            row["name"]
            for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        holds_history = self._db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is not None
        if holds_history and not set(_UPGRADE_TABLES) <= names:
            self.save_backup("before-upgrade")

    def _migrate(self) -> None:
        """Add the columns that databases made by an earlier version lack; nothing is dropped."""
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(cats)")}
        if "reference_weight_lb" not in columns:
            self._db.execute("ALTER TABLE cats ADD COLUMN reference_weight_lb REAL")
            # The owner's earlier estimate is the same idea under its old name.
            self._db.execute("UPDATE cats SET reference_weight_lb = estimated_weight_lb")
        for name, definition in _CAT_PROFILE_COLUMNS.items():
            if name not in columns:
                self._db.execute(f"ALTER TABLE cats ADD COLUMN {name} {definition}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the writes inside as one commit, which also makes a long run of them fast."""
        self._db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def is_empty(self) -> bool:
        """True when nothing a person or a robot recorded is here yet."""
        return all(
            self._db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None  # noqa: S608
            for table in ("events", "messages", "cats", "maintenance_log")
        )

    def close(self) -> None:
        self._db.close()

    def add_message(self, at: datetime, kind: str, body: Any) -> None:
        self._db.execute(
            "INSERT INTO messages (at, kind, body) VALUES (?, ?, ?)",
            (stamp(at), kind, json.dumps(body)),
        )

    def prune_messages(self, *, older_than: datetime, max_bytes: int) -> int:
        """Delete raw messages past the age limit, then the oldest ones past the size budget.

        Only the raw stream is touched; visits and every other event stay. Returns the number of
        rows removed.
        """
        removed = self._db.execute(
            "DELETE FROM messages WHERE at < ?", (stamp(older_than),)
        ).rowcount
        # Newest first, so the running total is how much the rows from here to now would weigh.
        over = self._db.execute(
            "SELECT id FROM (SELECT id, SUM(LENGTH(body)) OVER (ORDER BY id DESC) AS kept "
            "FROM messages) WHERE kept > ? ORDER BY id DESC LIMIT 1",
            (max_bytes,),
        ).fetchone()
        if over is not None:
            removed += self._db.execute(
                "DELETE FROM messages WHERE id <= ?", (over["id"],)
            ).rowcount
        return removed

    def has_event(self, at: datetime, kind: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM events WHERE kind = ? AND at = ? LIMIT 1", (kind, stamp(at))
        ).fetchone()
        return row is not None

    def latest_event_at(self, kind: str) -> str | None:
        row = self._db.execute(
            "SELECT at FROM events WHERE kind = ? ORDER BY at DESC LIMIT 1", (kind,)
        ).fetchone()
        return None if row is None else row["at"]

    def add_event(self, at: datetime, kind: str, detail: dict[str, Any]) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
            (stamp(at), kind, json.dumps(detail)),
        )
        assert cursor.lastrowid is not None  # noqa: S101
        if kind == "visit":
            # Ids are reused after a delete, and a database that was rolled back and forward can
            # still hold rows for an id that now means another visit.
            for table in ("visit_identity", "visit_labels"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE event_id = ?",  # noqa: S608
                    (cursor.lastrowid,),
                )
        return cursor.lastrowid

    def merge_event_detail(self, event_id: int, fields: dict[str, Any]) -> None:
        """Set the given keys in an event's detail and keep every other key as it was."""
        row = self._db.execute("SELECT detail FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return
        self._db.execute(
            "UPDATE events SET detail = ? WHERE id = ?",
            (json.dumps({**json.loads(row["detail"]), **fields}), event_id),
        )

    def has_visit_detection(self, detected_at: datetime) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM events WHERE kind = 'visit' "
            "AND json_extract(detail, '$.detected_at') = ? LIMIT 1",
            (stamp(detected_at),),
        ).fetchone()
        return row is not None

    def set_event_time(self, event_id: int, at: datetime) -> None:
        self._db.execute("UPDATE events SET at = ? WHERE id = ?", (stamp(at), event_id))

    def delete_visits(self, event_ids: Sequence[int]) -> None:
        """Remove visits together with the labels on them."""
        ids = json.dumps(list(event_ids))
        for table in ("visit_labels", "visit_identity"):
            self._db.execute(
                f"DELETE FROM {table} WHERE event_id IN (SELECT value FROM json_each(?))",  # noqa: S608
                (ids,),
            )
        self._db.execute(
            "DELETE FROM events WHERE kind = 'visit' AND id IN (SELECT value FROM json_each(?))",
            (ids,),
        )

    def save_backup(self, label: str) -> Path:
        """A consistent copy of the database next to it, kept until the owner removes it.

        It is written under a name that is not a backup's, checked, and only then renamed, so
        a failed or killed copy never leaves a truncated file that looks like a real one.
        """
        folder = self._path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamped = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = folder / f"whisker-local-{label}-{stamped}.db"
        partial = target.with_name(target.name + ".partial")
        try:
            partial.unlink(missing_ok=True)
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            source = sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                copy = sqlite3.connect(partial)
                try:
                    source.backup(copy)
                    copy.execute("PRAGMA journal_mode=DELETE")
                    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("the backup failed its integrity check")
                finally:
                    copy.close()
            finally:
                source.close()
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target

    def latest_message_at(self) -> datetime | None:
        row = self._db.execute("SELECT MAX(at) AS last FROM messages").fetchone()
        return None if row["last"] is None else datetime.fromisoformat(row["last"])

    def add_presence(self, at: datetime, online: bool) -> None:
        self._db.execute(
            "INSERT INTO robot_presence (at, online) VALUES (?, ?)", (stamp(at), int(online))
        )

    def presence(self) -> list[tuple[datetime, bool]]:
        """When the robot came online and went offline, oldest first."""
        rows = self._db.execute("SELECT at, online FROM robot_presence ORDER BY at, id")
        return [(datetime.fromisoformat(row["at"]), bool(row["online"])) for row in rows]

    def robot_activity_times(self, *, before: datetime | None) -> list[datetime]:
        """Times that show the robot was reporting, at most one per hour for raw messages."""
        limit = "" if before is None else " AND at < ?"
        bound = () if before is None else (stamp(before),)
        kinds = ",".join("?" * len(_ROBOT_EVENT_KINDS))
        # Only fixed text is interpolated; every value is bound.
        found = [
            row["at"]
            for row in self._db.execute(
                f"SELECT at FROM events WHERE kind IN ({kinds}){limit}",  # noqa: S608
                (*_ROBOT_EVENT_KINDS, *bound),
            )
        ]
        found.extend(
            row["at"]
            for row in self._db.execute(
                f"SELECT MIN(at) AS at FROM messages WHERE 1 = 1{limit} "  # noqa: S608
                "GROUP BY substr(at, 1, 13)",
                bound,
            )
        )
        return [datetime.fromisoformat(at) for at in found]

    def robot_event_counts(self, since: datetime, before: datetime, tz: tzinfo) -> dict[date, int]:
        """Visits and cycles per local day."""
        kinds = ",".join("?" * len(_ROBOT_EVENT_KINDS))
        counts: dict[date, int] = {}
        rows = self._db.execute(
            f"SELECT at FROM events WHERE kind IN ({kinds}) AND at >= ? AND at < ?",  # noqa: S608
            (*_ROBOT_EVENT_KINDS, stamp(since), stamp(before)),
        )
        for row in rows:
            day = datetime.fromisoformat(row["at"]).astimezone(tz).date()
            counts[day] = counts.get(day, 0) + 1
        return counts

    def unreliable_ranges(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM unreliable_ranges ORDER BY start_date, id")
        return [dict(row) for row in rows]

    def add_unreliable_range(
        self, start: date, end: date, note: str, now: datetime
    ) -> dict[str, Any]:
        cursor = self._db.execute(
            "INSERT INTO unreliable_ranges (start_date, end_date, note, created_at) "
            "VALUES (?, ?, ?, ?)",
            (start.isoformat(), end.isoformat(), note, stamp(now)),
        )
        row = self._db.execute(
            "SELECT * FROM unreliable_ranges WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)

    def delete_unreliable_range(self, range_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM unreliable_ranges WHERE id = ?", (range_id,))
        return cursor.rowcount == 1

    def first_live_at(self) -> datetime | None:
        """When the robot first reported anything, whether as a raw message or a saved event."""
        row = self._db.execute(
            "SELECT MIN(at) AS first FROM ("
            "SELECT MIN(at) AS at FROM messages UNION ALL "
            "SELECT MIN(at) FROM events WHERE json_extract(detail, '$.import_batch') IS NULL)"
        ).fetchone()
        return None if row["first"] is None else datetime.fromisoformat(row["first"])

    def import_batches(self) -> list[dict[str, Any]]:
        """Each import with what it added, newest first."""
        rows = self._db.execute(
            "SELECT json_extract(detail, '$.import_batch') AS batch_id, "
            "MAX(json_extract(detail, '$.imported_at')) AS imported_at, "
            "SUM(kind = 'visit') AS visits, SUM(kind = 'clean_cycle') AS clean_cycles, "
            "MIN(at) AS first_at, MAX(at) AS last_at FROM events "
            "WHERE json_extract(detail, '$.import_batch') IS NOT NULL "
            "GROUP BY batch_id ORDER BY imported_at DESC, batch_id DESC"
        )
        return [dict(row) for row in rows]

    def delete_import_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Remove every event of one import and the labels on its visits; None if none exist."""
        with self.transaction():
            ids = [
                row["id"]
                for row in self._db.execute(
                    "SELECT id FROM events WHERE json_extract(detail, '$.import_batch') = ?",
                    (batch_id,),
                )
            ]
            if not ids:
                return None
            first = self._db.execute(
                "SELECT MIN(at) AS first FROM events WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            ).fetchone()["first"]
            for table in ("visit_labels", "visit_identity"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE event_id IN (SELECT value FROM json_each(?))",  # noqa: S608
                    (json.dumps(ids),),
                )
            self._db.execute(
                "DELETE FROM events WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            )
        return {"batch_id": batch_id, "removed": len(ids), "first_at": first}

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
        exclude_imported: bool = False,
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
        if exclude_imported:
            clauses.append("json_extract(e.detail, '$.import_batch') IS NULL")
        direction = "DESC" if newest_first else "ASC"
        # Only fixed clause text and a fixed direction are interpolated; every value is bound.
        query = (
            "SELECT e.id, e.at, e.kind, e.detail, l.label, l.cat_id, l.source, "  # noqa: S608
            "i.status AS identity_status, i.cat_id AS candidate_id, i.confidence "
            "FROM events e LEFT JOIN visit_labels l ON l.event_id = e.id "
            "LEFT JOIN visit_identity i ON i.event_id = e.id "
            f"WHERE {' AND '.join(clauses)} ORDER BY e.at {direction}, e.id {direction}"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [_visit(row) for row in self._db.execute(query, params)]

    def visit(self, event_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT e.id, e.at, e.kind, e.detail, l.label, l.cat_id, l.source, "
            "i.status AS identity_status, i.cat_id AS candidate_id, i.confidence "
            "FROM events e LEFT JOIN visit_labels l ON l.event_id = e.id "
            "LEFT JOIN visit_identity i ON i.event_id = e.id "
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

    def apply_auto_labels(
        self,
        decisions: dict[int, int | None],
        identities: dict[int, tuple[str, int | None, float | None]] | None = None,
    ) -> None:
        """Set or drop the automatic label of each visit, and never touch a user's label.

        ``identities`` records how sure the decision was, so a visit left unnamed can be told
        apart from one that was never looked at.
        """
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
            for event_id, (status, candidate, confidence) in (identities or {}).items():
                self._db.execute(
                    "INSERT INTO visit_identity (event_id, status, cat_id, confidence) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (event_id) DO UPDATE SET "
                    "status = excluded.status, cat_id = excluded.cat_id, "
                    "confidence = excluded.confidence",
                    (event_id, status, candidate, confidence),
                )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def drop_orphan_identities(self) -> None:
        """Forget identities of visits that no longer exist, so a reused id starts clean."""
        self._db.execute("DELETE FROM visit_identity WHERE event_id NOT IN (SELECT id FROM events)")
        self._db.execute("DELETE FROM visit_labels WHERE event_id NOT IN (SELECT id FROM events)")

    def anchors(self) -> dict[int, Anchor]:
        rows = self._db.execute("SELECT * FROM cat_anchors")
        return {
            row["cat_id"]: Anchor(
                row["weight_lb"],
                row["spread_lb"],
                row["visits"],
                row["visits_per_day"],
                datetime.fromisoformat(row["set_at"]),
                row["source"],
            )
            for row in rows
        }

    def put_anchor(self, cat_id: int, anchor: Anchor) -> None:
        self._db.execute(
            "INSERT INTO cat_anchors "
            "(cat_id, weight_lb, spread_lb, visits, visits_per_day, set_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (cat_id) DO UPDATE SET "
            "weight_lb = excluded.weight_lb, spread_lb = excluded.spread_lb, "
            "visits = excluded.visits, visits_per_day = excluded.visits_per_day, "
            "set_at = excluded.set_at, source = excluded.source",
            (
                cat_id,
                anchor.weight_lb,
                anchor.spread_lb,
                anchor.visits,
                anchor.visits_per_day,
                stamp(anchor.set_at),
                anchor.source,
            ),
        )

    def delete_anchor(self, cat_id: int) -> None:
        self._db.execute("DELETE FROM cat_anchors WHERE cat_id = ?", (cat_id,))

    def add_cat(
        self,
        name: str,
        reference_weight_lb: float | None,
        now: datetime,
        *,
        breed: str | None = None,
        birthday: date | None = None,
        sex: str = "unknown",
    ) -> int:
        """The color index is the count of cats ever made, so an archived cat keeps its color."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            (taken,) = self._db.execute("SELECT COUNT(*) FROM cats").fetchone()
            cursor = self._db.execute(
                "INSERT INTO cats "
                "(name, color_index, reference_weight_lb, created_at, breed, birthday, sex) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    taken,
                    reference_weight_lb,
                    stamp(now),
                    breed,
                    None if birthday is None else birthday.isoformat(),
                    sex,
                ),
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

    def update_cat_profile(self, cat_id: int, fields: dict[str, Any]) -> None:
        """Set the profile columns in ``fields``; the names are checked against a fixed set."""
        unknown = fields.keys() - _CAT_PROFILE_FIELDS
        if unknown:
            raise ValueError(f"not profile fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._db.execute(
            f"UPDATE cats SET {assignments} WHERE id = ?",  # noqa: S608 - names checked above
            (*fields.values(), cat_id),
        )

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
    identity = (
        None
        if row["identity_status"] is None
        else {
            "status": row["identity_status"],
            "candidate_cat_id": row["candidate_id"],
            "confidence": row["confidence"],
        }
    )
    return {
        **_event(row),
        "label": row["label"] or "unknown",
        "cat_id": row["cat_id"],
        "label_source": row["source"] or "auto",
        "identity": identity,
    }

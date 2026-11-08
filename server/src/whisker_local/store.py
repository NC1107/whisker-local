from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
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
"""


def stamp(at: datetime) -> str:
    """UTC with a fixed width, so that text order in SQLite is time order."""
    return at.astimezone(UTC).isoformat(timespec="milliseconds")


class Store:
    """SQLite history, kept as one file so that a backup is a file copy.

    ``messages`` keeps every raw robot message forever. Interpretation of the protocol is
    still changing upstream, and the raw stream is what lets old visits be re-derived later.
    """

    def __init__(self, path: Path) -> None:
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

    def add_event(self, at: datetime, kind: str, detail: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
            (stamp(at), kind, json.dumps(detail)),
        )

    def events(
        self, *, limit: int, before: str | None = None, kinds: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if before is not None:
            clauses.append("at < ?")
            params.append(before)
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # The only interpolated text is the fixed clause list above; every value is bound.
        query = f"SELECT at, kind, detail FROM events {where} ORDER BY at DESC, id DESC LIMIT ?"  # noqa: S608
        rows = self._db.execute(query, (*params, limit))
        return [_event(row) for row in rows]

    def visits_since(self, since: datetime) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT at, kind, detail FROM events WHERE kind = 'visit' AND at >= ? ORDER BY at, id",
            (stamp(since),),
        )
        return [_event(row) for row in rows]

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
    return {"at": row["at"], "kind": row["kind"], "detail": json.loads(row["detail"])}

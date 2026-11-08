from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, tzinfo

from .store import Store

COLUMNS = (
    "id",
    "started_at",
    "ended_at",
    "local_date",
    "local_time",
    "duration_s",
    "weight_lb",
    "cat",
    "cat_id",
    "label",
    "label_source",
)

# A spreadsheet runs a cell that starts with one of these as a formula, and a cat's name is
# typed by a person.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(text: str) -> str:
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def visits_csv(store: Store, tz: tzinfo, start: date | None, end: date | None) -> str:
    """Every visit in a range of local days, oldest first, with the cat it was labelled as."""
    since = None if start is None else datetime.combine(start, time.min, tzinfo=tz)
    before = None if end is None else datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    names = {cat["id"]: cat["name"] for cat in store.cats(include_archived=True)}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for visit in store.visits(since=since, before=before):
        detail = visit["detail"]
        local = datetime.fromisoformat(visit["at"]).astimezone(tz)
        cat_id = visit["cat_id"]
        writer.writerow(
            [
                visit["id"],
                detail.get("started", ""),
                visit["at"],
                local.date().isoformat(),
                local.time().isoformat(timespec="seconds"),
                _blank(detail.get("duration_s")),
                _blank(detail.get("weight_lb")),
                _safe_text(names.get(cat_id, "")),
                _blank(cat_id),
                visit["label"],
                visit["label_source"],
            ]
        )
    return out.getvalue()


def _blank(value: object) -> object:
    return "" if value is None else value

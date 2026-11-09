from __future__ import annotations

import csv
import io
import logging
import re
import secrets
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from .cats import CatTracker
from .store import Store, stamp

log = logging.getLogger(__name__)

# A year of the export is about 20 KB a day of rows at most, so this is far past any real file
# and still small enough to hold in memory while it is read.
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_ROWS = 50_000

# The Whisker app logs "Cat detected" when the cat steps in and "Weight recorded" a minute or
# three later. A detection with no weight this near it is a visit that was never weighed.
DETECTION_WINDOW = timedelta(minutes=5)

# The scale reads cat weights, so a figure outside this is a typo in the file, not a visit.
_WEIGHT_RANGE_LB = (0.5, 60.0)

_HEADER = ("activity", "timestamp", "value")
_TIMESTAMP = re.compile(r"(\d{1,2})/(\d{1,2}) at (\d{1,2}):(\d{2}) (AM|PM)", re.IGNORECASE)
_WEIGHT = re.compile(r"(\d{1,3}(?:\.\d+)?) lbs?", re.IGNORECASE)
_BATCH_ID = re.compile(r"[0-9a-f]{12}")

NOT_A_WHISKER_EXPORT = "Not a Whisker activity export, choose the CSV exported from the Whisker app"


class NotWhiskerExport(ValueError):
    def __init__(self, message: str = NOT_A_WHISKER_EXPORT) -> None:
        super().__init__(message)


class BatchNotFound(LookupError):
    pass


@dataclass(slots=True)
class Parsed:
    rows: int = 0
    weights: list[tuple[datetime, float]] = field(default_factory=list)
    completes: list[datetime] = field(default_factory=list)
    detections: list[datetime] = field(default_factory=list)
    interrupted: int = 0
    in_progress: int = 0
    unreadable: int = 0


def is_valid_batch_id(batch_id: str) -> bool:
    return _BATCH_ID.fullmatch(batch_id) is not None


def _local_to_utc(naive: datetime, tz: tzinfo, newer: datetime | None) -> datetime:
    """A wall clock time as an instant; the repeated hour of a clock change is told apart by order.

    The export is newest first, so a time in the repeated hour belongs to the second pass (after
    the clock went back) only if that does not put it after the row above it, else to the first.
    """
    first = naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
    second = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
    if first == second:
        return first
    return second if newer is not None and second <= newer else first


def _date_in_year(month: int, day: int, reference: date) -> date | None:
    """The latest date with this month and day that is not after ``reference``."""
    for year in range(reference.year, reference.year - 8, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= reference:
            return candidate
    return None


def _read_time(text: str, tz: tzinfo, reference: date, newer: datetime | None) -> datetime | None:
    match = _TIMESTAMP.fullmatch(text.strip())
    if match is None:
        return None
    month, day, hour, minute = (int(match[i]) for i in (1, 2, 3, 4))
    if not (1 <= hour <= 12 and minute < 60):
        return None
    when = _date_in_year(month, day, reference)
    if when is None:
        return None
    hour = hour % 12 + (12 if match[5].upper() == "PM" else 0)
    return _local_to_utc(datetime(when.year, when.month, when.day, hour, minute), tz, newer)


def parse(text: str, tz: tzinfo, reference: date) -> Parsed:
    """Read a Whisker activity export, raising ``NotWhiskerExport`` for anything else."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise NotWhiskerExport from None
    if tuple(cell.strip().lower() for cell in header) != _HEADER:
        raise NotWhiskerExport

    parsed = Parsed()
    newer: datetime | None = None
    recognised = 0
    try:
        for row in reader:
            if not row:
                continue
            parsed.rows += 1
            if parsed.rows > MAX_ROWS:
                raise NotWhiskerExport("Too many rows for a Whisker export")
            if len(row) != 3:
                parsed.unreadable += 1
                continue
            activity, timestamp, value = (cell.strip() for cell in row)
            at = _read_time(timestamp, tz, reference, newer)
            kind = activity.lower()
            if at is None or kind not in _ACTIVITIES:
                parsed.unreadable += 1
                continue
            newer = at
            if kind == "weight recorded":
                weight = _read_weight(value)
                if weight is None:
                    parsed.unreadable += 1
                    continue
                parsed.weights.append((at, weight))
            elif kind == "clean cycle complete":
                parsed.completes.append(at)
            elif kind == "cat detected":
                parsed.detections.append(at)
            elif kind == "cycle interrupted":
                parsed.interrupted += 1
            else:
                parsed.in_progress += 1
            recognised += 1
    except csv.Error:
        raise NotWhiskerExport from None
    if recognised == 0:
        raise NotWhiskerExport
    return parsed


_ACTIVITIES = frozenset(
    {
        "weight recorded",
        "cat detected",
        "clean cycle in progress",
        "clean cycle complete",
        "cycle interrupted",
    }
)


def _read_weight(value: str) -> float | None:
    match = _WEIGHT.fullmatch(value)
    if match is None:
        return None
    weight = float(match[1])
    low, high = _WEIGHT_RANGE_LB
    return weight if low <= weight <= high else None


def unweighed_detections(parsed: Parsed) -> int:
    weighed = sorted(at for at, _ in parsed.weights)
    count = 0
    for seen in parsed.detections:
        if not any(abs(at - seen) <= DETECTION_WINDOW for at in _near(weighed, seen)):
            count += 1
    return count


def _near(sorted_times: list[datetime], target: datetime) -> list[datetime]:
    # Only the times inside the window matter, and the list is sorted, so a bisect keeps this
    # linear in the file instead of quadratic.
    low = bisect_left(sorted_times, target - DETECTION_WINDOW)
    high = bisect_right(sorted_times, target + DETECTION_WINDOW)
    return sorted_times[low:high]


class Importer:
    def __init__(
        self,
        store: Store,
        cats: CatTracker,
        timezone: tzinfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._cats = cats
        self._tz = timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def run(self, text: str, *, reference: date | None, dry_run: bool) -> dict[str, Any]:
        reference = reference or self.today()
        parsed = parse(text, self._tz, reference)
        cutoff = self._store.first_live_at()

        new_weights, weights_seen, weights_late = self._triage(parsed.weights, "visit", cutoff)
        cycles = [(at, None) for at in parsed.completes]
        new_cycle_rows, cycles_seen, cycles_late = self._triage(cycles, "clean_cycle", cutoff)
        new_cycles = [at for at, _ in new_cycle_rows]
        already = weights_seen + cycles_seen
        after_live = weights_late + cycles_late

        times = [at for at, _ in new_weights] + new_cycles
        summary: dict[str, Any] = {
            "dry_run": dry_run,
            "batch_id": None,
            "reference_date": reference.isoformat(),
            "rows_read": parsed.rows,
            "visits": len(new_weights),
            "visits_matched_to_cat": None,
            "clean_cycles": len(new_cycles),
            "first_at": stamp(min(times)) if times else None,
            "last_at": stamp(max(times)) if times else None,
            "already_imported": already,
            "after_live_data": after_live,
            "live_data_starts_at": None if cutoff is None else stamp(cutoff),
            "cat_detections_without_weight": unweighed_detections(parsed),
            "cycles_interrupted": parsed.interrupted,
            "cycles_in_progress": parsed.in_progress,
            "unreadable_rows": parsed.unreadable,
        }
        if dry_run or not times:
            return summary

        batch_id = secrets.token_hex(6)
        imported_at = stamp(self._clock())
        mark = {"imported": True, "import_batch": batch_id, "imported_at": imported_at}
        with self._store.transaction():
            for at, weight in new_weights:
                self._store.add_event(at, "visit", {"weight_lb": weight, **mark})
            for at in new_cycles:
                self._store.add_event(at, "clean_cycle", dict(mark))
        summary["batch_id"] = batch_id
        if new_weights:
            summary["visits_matched_to_cat"] = self._label(
                min(at for at, _ in new_weights), batch_id
            )
        return summary

    def _triage[T](
        self, items: list[tuple[datetime, T]], kind: str, cutoff: datetime | None
    ) -> tuple[list[tuple[datetime, T]], int, int]:
        """Split rows into new ones, ones already stored, and ones at or after live data."""
        new: list[tuple[datetime, T]] = []
        seen: set[datetime] = set()
        already = late = 0
        for at, value in items:
            if cutoff is not None and at >= cutoff:
                late += 1
            elif at in seen or self._store.has_event(at, kind):
                already += 1
            else:
                seen.add(at)
                new.append((at, value))
        return new, already, late

    def _label(self, since: datetime, batch_id: str) -> int | None:
        """Match the new visits to cats, and say how many found one."""
        try:
            self._cats.relabel(since=since)
        except Exception:
            # The visits are saved, and any later relabel picks them up.
            log.exception("could not match imported visits to cats")
            return None
        return sum(
            1
            for visit in self._store.visits(since=since)
            if visit["detail"].get("import_batch") == batch_id and visit["label"] == "cat"
        )

    def batches(self) -> list[dict[str, Any]]:
        return self._store.import_batches()

    def undo(self, batch_id: str) -> dict[str, Any]:
        removed = self._store.delete_import_batch(batch_id)
        if removed is None:
            raise BatchNotFound(batch_id)
        # Live visits were matched with the imported ones as history, so they are matched again
        # without it.
        self._cats.relabel(since=datetime.fromisoformat(removed.pop("first_at")))
        return removed

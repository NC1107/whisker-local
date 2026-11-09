from __future__ import annotations

import csv
import io
import logging
import re
import secrets
import statistics
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from .cats import CatTracker
from .store import Store, stamp
from .visit_capture import SAME_CAT_LB

log = logging.getLogger(__name__)

# A year of the export is about 20 KB a day of rows at most, so this is far past any real file
# and still small enough to hold in memory while it is read.
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_ROWS = 50_000

# The Whisker app logs "Cat detected" when the cat steps in and "Weight recorded" a minute or
# three later. A detection with no weight this near it is a visit that was never weighed.
DETECTION_WINDOW = timedelta(minutes=5)

# The Whisker app logs a second weight for the same detection up to about ten minutes later when
# another animal steps in during the countdown, so a weight belongs to the latest detection that
# recent. Timestamps have minute resolution, so a weight stamped a minute before its detection
# still belongs to it.
ASSIGN_WINDOW = timedelta(minutes=15)
ASSIGN_SLACK = timedelta(minutes=1)

# Marks a visit stored as an episode, so that reprocessing can tell old history from new.
EPISODE_DEFINITION = 2

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


@dataclass(frozen=True, slots=True)
class PlannedVisit:
    """One visit as the export describes it: a detection with zero or more weights."""

    at: datetime
    weight_lb: float | None
    weight_times: tuple[datetime, ...]
    detected_at: datetime | None

    @property
    def readings(self) -> int:
        return len(self.weight_times)

    def detail(self) -> dict[str, Any]:
        return {
            "weight_lb": self.weight_lb,
            "readings": self.readings,
            "detected_at": None if self.detected_at is None else stamp(self.detected_at),
            "definition": EPISODE_DEFINITION,
        }


def _split_cats(readings: list[tuple[datetime, float]]) -> list[list[tuple[datetime, float]]]:
    groups: list[list[tuple[datetime, float]]] = []
    for at, weight in sorted(readings):
        if groups and abs(weight - statistics.median(w for _, w in groups[-1])) < SAME_CAT_LB:
            groups[-1].append((at, weight))
        else:
            groups.append([(at, weight)])
    return groups


def episodes(parsed: Parsed) -> list[PlannedVisit]:
    """Visits as episodes: a detection with its weights, a lone weight, or a lone detection.

    Several weights for one detection are one visit unless they are clearly two cats.
    """
    detections = sorted(parsed.detections)
    attached: dict[int | None, list[tuple[datetime, float]]] = {}
    for at, weight in parsed.weights:
        index = bisect_right(detections, at + ASSIGN_SLACK) - 1
        near = index >= 0 and at - detections[index] <= ASSIGN_WINDOW
        attached.setdefault(index if near else None, []).append((at, weight))

    planned: list[PlannedVisit] = []
    for index, seen in enumerate(detections):
        found = attached.get(index, [])
        if not found:
            planned.append(PlannedVisit(seen, None, (), seen))
        for group in _split_cats(found):
            planned.append(_planned(group, seen))
    for group in _split_cats(attached.get(None, [])):
        planned.append(_planned(group, None))
    return sorted(planned, key=lambda visit: visit.at)


def _planned(group: list[tuple[datetime, float]], detected_at: datetime | None) -> PlannedVisit:
    times = tuple(at for at, _ in group)
    return PlannedVisit(
        times[0], round(statistics.fmean(w for _, w in group), 2), times, detected_at
    )


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
        planned = episodes(parsed)

        new_visits, visits_seen, visits_late = self._triage_visits(planned, cutoff)
        cycles = [(at, None) for at in parsed.completes]
        new_cycle_rows, cycles_seen, cycles_late = self._triage(cycles, "clean_cycle", cutoff)
        new_cycles = [at for at, _ in new_cycle_rows]
        already = visits_seen + cycles_seen
        after_live = visits_late + cycles_late

        times = [visit.at for visit in new_visits] + new_cycles
        summary: dict[str, Any] = {
            "dry_run": dry_run,
            "batch_id": None,
            "reference_date": reference.isoformat(),
            "rows_read": parsed.rows,
            "visits": len(new_visits),
            "visits_without_weight": sum(1 for visit in new_visits if visit.weight_lb is None),
            "visits_matched_to_cat": None,
            "clean_cycles": len(new_cycles),
            "first_at": stamp(min(times)) if times else None,
            "last_at": stamp(max(times)) if times else None,
            "already_imported": already,
            "after_live_data": after_live,
            "live_data_starts_at": None if cutoff is None else stamp(cutoff),
            "cat_detections_without_weight": sum(1 for visit in planned if visit.weight_lb is None),
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
            for visit in new_visits:
                self._store.add_event(visit.at, "visit", {**visit.detail(), **mark})
            for at in new_cycles:
                self._store.add_event(at, "clean_cycle", dict(mark))
        summary["batch_id"] = batch_id
        if new_visits:
            summary["visits_matched_to_cat"] = self._label(
                min(visit.at for visit in new_visits), batch_id
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

    def _triage_visits(
        self, planned: list[PlannedVisit], cutoff: datetime | None
    ) -> tuple[list[PlannedVisit], int, int]:
        """Split visits into new ones, ones already stored, and ones at or after live data."""
        new: list[PlannedVisit] = []
        seen: set[tuple[datetime, bool]] = set()
        already = late = 0
        for visit in planned:
            key = (visit.at, visit.weight_lb is None)
            if cutoff is not None and visit.at >= cutoff:
                late += 1
            elif key in seen or self._stored(visit):
                already += 1
            else:
                seen.add(key)
                new.append(visit)
        return new, already, late

    def _stored(self, visit: PlannedVisit) -> bool:
        if visit.weight_times:
            # Earlier versions stored one visit per weight row, at that row's time.
            return any(self._store.has_event(at, "visit") for at in visit.weight_times)
        assert visit.detected_at is not None  # noqa: S101
        return self._store.has_visit_detection(visit.detected_at)

    def reprocess(self, text: str, *, reference: date | None, dry_run: bool) -> dict[str, Any]:
        """Bring history imported as one visit per weight row to one visit per detection.

        The export is needed again because the detections were never stored. Visits are matched
        by the time of their weight rows, so labels, ids and everything else on them survive,
        and a hand-made label on a reading that merges into another moves to the visit that
        keeps it. A backup of the whole database is written first.
        """
        reference = reference or self.today()
        parsed = parse(text, self._tz, reference)
        cutoff = self._store.first_live_at()
        planned = [v for v in episodes(parsed) if cutoff is None or v.at < cutoff]
        summary: dict[str, Any] = {
            "dry_run": dry_run,
            "batch_id": None,
            "reference_date": reference.isoformat(),
            "visits_before": 0,
            "visits_after": 0,
            "merged": 0,
            "added_without_weight": 0,
            "added_with_weight": 0,
            "updated": 0,
            "unchanged": 0,
            "labels_moved": 0,
            "backup": None,
            "first_at": None,
            "last_at": None,
        }
        if not planned:
            return summary

        first = min(v.at for v in planned)
        last = max([v.at for v in planned] + [t for v in planned for t in v.weight_times])
        stored = [
            visit
            for visit in self._store.visits(since=first, before=last + timedelta(seconds=1))
            if visit["detail"].get("imported")
        ]
        by_time: dict[str, dict[str, Any]] = {}
        for visit in stored:
            by_time.setdefault(visit["at"], visit)

        updates: list[tuple[dict[str, Any], PlannedVisit, list[dict[str, Any]]]] = []
        additions: list[PlannedVisit] = []
        for visit in planned:
            if not visit.weight_times:
                assert visit.detected_at is not None  # noqa: S101
                if self._store.has_visit_detection(visit.detected_at):
                    summary["unchanged"] += 1
                else:
                    additions.append(visit)
                continue
            found = [by_time[stamp(t)] for t in visit.weight_times if stamp(t) in by_time]
            if not found:
                additions.append(visit)
                continue
            keeper, *extra = found
            current = keeper["detail"]
            differs = any(current.get(key) != value for key, value in visit.detail().items())
            if extra or keeper["at"] != stamp(visit.at) or differs:
                updates.append((keeper, visit, extra))
            else:
                summary["unchanged"] += 1

        merged = sum(len(extra) for _, _, extra in updates)
        summary.update(
            visits_before=len(stored),
            visits_after=len(stored) - merged + len(additions),
            merged=merged,
            added_without_weight=sum(1 for v in additions if v.weight_lb is None),
            added_with_weight=sum(1 for v in additions if v.weight_lb is not None),
            updated=len(updates),
            first_at=stamp(first),
            last_at=stamp(last),
        )
        if dry_run or not (updates or additions):
            return summary

        summary["backup"] = self._store.save_backup("before-reprocess").name
        batch_id = secrets.token_hex(6)
        mark = {"imported": True, "import_batch": batch_id, "imported_at": stamp(self._clock())}
        with self._store.transaction():
            for keeper, visit, extra in updates:
                summary["labels_moved"] += self._merge_visits(keeper, visit, extra)
            for visit in additions:
                self._store.add_event(visit.at, "visit", {**visit.detail(), **mark})
        if additions:
            summary["batch_id"] = batch_id
        self._cats.relabel(since=first)
        return summary

    def _merge_visits(
        self, keeper: dict[str, Any], visit: PlannedVisit, extra: list[dict[str, Any]]
    ) -> int:
        moved = 0
        if keeper["label_source"] != "user":
            hand_made = next((e for e in extra if e["label_source"] == "user"), None)
            if hand_made is not None:
                self._store.set_user_label(keeper["id"], hand_made["label"], hand_made["cat_id"])
                moved = 1
        self._store.merge_event_detail(keeper["id"], visit.detail())
        if keeper["at"] != stamp(visit.at):
            self._store.set_event_time(keeper["id"], visit.at)
        self._store.delete_visits([e["id"] for e in extra])
        return moved

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

"""Which days the robot was actually listened to, so that a quiet day is not mistaken for no data.

A day is full, partial or none. With a record of when the robot was online that is read from the
record. History imported from Whisker has no such record, so a long stretch with no event of any
kind (no visit, no cycle) is taken to be a gap rather than a quiet stretch.
"""

from __future__ import annotations

import bisect
import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Literal

from .store import Store

State = Literal["full", "partial", "none"]

# The longest silence between two events in a month of real history was under 18 hours, and the
# one real outage was 60. A silence twice the first is far likelier to be a robot that was not
# reporting than two cats that never came.
SILENT_GAP = timedelta(hours=36)

# A day counts as full when the robot was heard for this share of it, and as none when it was
# heard for less than the second share and nothing happened.
FULL_SHARE = 0.9
NONE_SHARE = 0.05

Span = tuple[datetime, datetime]


class InvalidRange(ValueError):
    pass


class RangeNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Day:
    date: date
    state: State
    source: Literal["online_record", "inferred"]
    share: float
    events: int
    unreliable: dict[str, Any] | None

    @property
    def counts(self) -> bool:
        """Whether the day belongs in an average: fully heard and not marked unreliable."""
        return self.state == "full" and self.unreliable is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "state": self.state,
            "source": self.source,
            "heard_share": round(self.share, 2),
            "events": self.events,
            "unreliable": self.unreliable,
        }


def _overlap(spans: list[Span], starts: list[datetime], low: datetime, high: datetime) -> float:
    total = 0.0
    index = max(0, bisect.bisect_right(starts, low) - 1)
    while index < len(spans) and spans[index][0] < high:
        begin, end = spans[index]
        total += max(0.0, (min(end, high) - max(begin, low)).total_seconds())
        index += 1
    return total


def inferred_spans(stamps: list[datetime], end: datetime) -> list[Span]:
    """Time that events vouch for: between two events no further apart than a silent gap."""
    spans: list[Span] = []
    ordered = sorted(set(stamps))
    for before, after in itertools.pairwise(ordered):
        if after - before <= SILENT_GAP:
            spans.append((before, after))
    if ordered and end - ordered[-1] <= SILENT_GAP:
        spans.append((ordered[-1], max(end, ordered[-1])))
    return spans


def online_spans(rows: list[tuple[datetime, bool]], now: datetime) -> list[Span]:
    spans: list[Span] = []
    opened: datetime | None = None
    for at, online in rows:
        if online and opened is None:
            opened = at
        elif not online and opened is not None:
            spans.append((opened, at))
            opened = None
    if opened is not None:
        spans.append((opened, max(now, opened)))
    return spans


class Coverage:
    def __init__(
        self, store: Store, timezone: tzinfo = UTC, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._tz = timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def _day_start(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self._tz).astimezone(UTC)

    def _spans(self, now: datetime) -> tuple[list[Span], datetime | None]:
        presence = self._store.presence()
        first = presence[0][0] if presence else None
        before = self._store.robot_activity_times(before=first)
        spans = inferred_spans(before, first or now)
        spans.extend(online_spans(presence, now))
        return spans, first

    def days(self, start: date, end: date) -> list[Day]:
        if start > end:
            raise InvalidRange("start is after end")
        now = self._clock()
        spans, first = self._spans(now)
        starts = [begin for begin, _ in spans]
        events = self._store.robot_event_counts(
            self._day_start(start), self._day_start(end + timedelta(days=1)), self._tz
        )
        ranges = self._store.unreliable_ranges()
        found: list[Day] = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            low, high = self._day_start(day), self._day_start(day + timedelta(days=1))
            heard = _overlap(spans, starts, low, min(high, now)) if low < now else 0.0
            share = heard / (high - low).total_seconds()
            count = events.get(day, 0)
            if share >= FULL_SHARE and high <= now:
                state: State = "full"
            elif share < NONE_SHARE and count == 0:
                state = "none"
            else:
                state = "partial"
            found.append(
                Day(
                    day,
                    state,
                    "online_record" if first is not None and high > first else "inferred",
                    min(share, 1.0),
                    count,
                    self._unreliable(ranges, day),
                )
            )
        return found

    @staticmethod
    def _unreliable(ranges: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
        for row in ranges:
            if date.fromisoformat(row["start_date"]) <= day <= date.fromisoformat(row["end_date"]):
                return {"id": row["id"], "note": row["note"]}
        return None

    def heard_share(self) -> Callable[[datetime, datetime], float]:
        """A function giving the share of any stretch of time that the robot was listened to.

        The spans are read once, so it can be asked about thousands of stretches cheaply.
        """
        now = self._clock()
        spans, _ = self._spans(now)
        starts = [begin for begin, _ in spans]

        def share(low: datetime, high: datetime) -> float:
            high = min(high, now)
            if high <= low:
                return 0.0
            return min(1.0, _overlap(spans, starts, low, high) / (high - low).total_seconds())

        return share

    def by_day(self, start: date, end: date) -> dict[date, Day]:
        return {day.date: day for day in self.days(start, end)}

    def view(self, start: date, end: date) -> dict[str, Any]:
        days = self.days(start, end)
        return {
            "timezone": str(self._tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": [day.as_dict() for day in days],
            "full": sum(1 for day in days if day.state == "full"),
            "partial": sum(1 for day in days if day.state == "partial"),
            "none": sum(1 for day in days if day.state == "none"),
            "unreliable_ranges": self.unreliable_ranges(),
        }

    def unreliable_ranges(self) -> list[dict[str, Any]]:
        return self._store.unreliable_ranges()

    def mark_unreliable(self, start: date, end: date, note: str) -> dict[str, Any]:
        if start > end:
            raise InvalidRange("The end of the range is before its start.")
        return self._store.add_unreliable_range(start, end, note.strip(), self._clock())

    def unmark_unreliable(self, range_id: int) -> None:
        if not self._store.delete_unreliable_range(range_id):
            raise RangeNotFound(range_id)

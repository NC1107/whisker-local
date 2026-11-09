"""The visit history laid out by day, once, so that every health check reads the same arrays.

Building it costs one pass over the visits and one over the days. The checks then look at
windows through prefix sums and bisection, so evaluating every day of a long history (the
backtest) costs about the same as evaluating one.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .cats import CatTracker
from .coverage import Day
from .store import Store

# A day on which this share of the weighed visits fit no cat, with enough of them to say so,
# is a day the scale or the box was disturbed (a litter change, something on the box).
DISTURBED_SHARE = 0.3
DISTURBED_MIN_READINGS = 5


@dataclass(frozen=True, slots=True)
class Visit:
    at: datetime
    local: datetime
    day: int
    cat_id: int | None
    weight: float | None
    duration_s: int | None
    status: str
    started: datetime


@dataclass(slots=True)
class DaySeries:
    """Counts per day with prefix sums over the days that can be counted."""

    counts: list[int]
    counted: list[bool]
    total: list[int] = field(init=False)
    days: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.total, self.days = [0], [0]
        for count, ok in zip(self.counts, self.counted, strict=True):
            self.total.append(self.total[-1] + (count if ok else 0))
            self.days.append(self.days[-1] + (1 if ok else 0))

    def window(self, low: int, high: int) -> tuple[int, int]:
        """Visits and counted days from day ``low`` to day ``high``, both included."""
        low = max(low, 0)
        high = min(high, len(self.counts) - 1)
        if high < low:
            return 0, 0
        return self.total[high + 1] - self.total[low], self.days[high + 1] - self.days[low]


def is_disturbed(statuses: list[str]) -> bool:
    weighed = [s for s in statuses if s != "no_weight"]
    if len(weighed) < DISTURBED_MIN_READINGS:
        return False
    return sum(1 for s in weighed if s == "off_cluster") / len(weighed) >= DISTURBED_SHARE


def visit_status(row: dict[str, Any]) -> str:
    if row["label_source"] == "user":
        return "user"
    return (row.get("identity") or {}).get("status", "unlabelled")


class History:
    def __init__(self, store: Store, cats: CatTracker) -> None:
        self.tz = cats.timezone
        self.today = cats.today()
        self.cats = {row["id"]: row for row in store.cats(include_archived=True)}
        self.anchors = store.anchors()
        rows = [row for row in store.visits() if row["label"] != "not_cat"]
        self.first: date | None = None
        self.visits: list[Visit] = []
        if rows:
            self.first = datetime.fromisoformat(rows[0]["at"]).astimezone(self.tz).date()
        self.days: list[Day] = (
            [] if self.first is None else cats.coverage.days(self.first, self.today)
        )
        for row in rows:
            self.visits.append(self._visit(row))
        size = len(self.days)

        statuses: list[list[str]] = [[] for _ in range(size)]
        for visit in self.visits:
            statuses[visit.day].append(visit.status)
        self.disturbed = [is_disturbed(found) for found in statuses]
        self.reliable = [
            day.unreliable is None and not bad
            for day, bad in zip(self.days, self.disturbed, strict=True)
        ]
        self.counted = [
            day.state == "full" and day.unreliable is None and not bad
            for day, bad in zip(self.days, self.disturbed, strict=True)
        ]

        household = [0] * size
        per_cat: dict[int, list[int]] = {}
        self.by_cat: dict[int, list[Visit]] = {}
        for visit in self.visits:
            household[visit.day] += 1
            if visit.cat_id is not None:
                per_cat.setdefault(visit.cat_id, [0] * size)[visit.day] += 1
                self.by_cat.setdefault(visit.cat_id, []).append(visit)
        self.household = DaySeries(household, self.counted)
        self.cat_series = {cat: DaySeries(counts, self.counted) for cat, counts in per_cat.items()}
        self.readings = {cat: _Readings(found, self) for cat, found in self.by_cat.items()}

    def _visit(self, row: dict[str, Any]) -> Visit:
        ended = datetime.fromisoformat(row["at"])
        local = ended.astimezone(self.tz)
        detail = row["detail"]
        weight = detail.get("weight_lb")
        started = detail.get("started")
        began = datetime.fromisoformat(started).astimezone(self.tz) if started else local
        # A start on the previous day is a robot clock quirk, not a night visit.
        if began.date() != local.date():
            began = local
        assert self.first is not None  # noqa: S101
        return Visit(
            ended,
            local,
            (local.date() - self.first).days,
            row["cat_id"] if row["label"] == "cat" else None,
            None if weight is None else float(weight),
            detail.get("duration_s"),
            visit_status(row),
            began,
        )

    def index(self, day: date) -> int:
        assert self.first is not None  # noqa: S101
        return (day - self.first).days

    def date_of(self, index: int) -> date:
        assert self.first is not None  # noqa: S101
        return self.first + timedelta(days=index)

    def last_complete(self) -> int | None:
        """Index of yesterday, the newest day that is finished, or None before any history."""
        if self.first is None:
            return None
        index = len(self.days) - 2
        return index if index >= 0 else None

    def began(self, series: DaySeries) -> int | None:
        """First day in which a series had a visit; days before it are not 'zero visits'."""
        return next((i for i, count in enumerate(series.counts) if count), None)


class _Readings:
    """One cat's weights, in order, for window medians by bisection."""

    def __init__(self, visits: list[Visit], history: History) -> None:
        weighed = [v for v in visits if v.weight is not None]
        self.all = weighed
        self.trusted = [v for v in weighed if history.reliable[v.day]]
        self.days = [v.day for v in self.trusted]
        self.weights = [v.weight for v in self.trusted]

    def between(self, low: int, high: int) -> list[float]:
        start = bisect.bisect_left(self.days, low)
        stop = bisect.bisect_right(self.days, high)
        return self.weights[start:stop]  # type: ignore[return-value]

    def count_until(self, high: int) -> int:
        return bisect.bisect_right(self.days, high)

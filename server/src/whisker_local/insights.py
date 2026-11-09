from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Literal

from .coverage import Coverage, Day
from .localdays import day_span, local_today
from .store import Store

Granularity = Literal["day", "week", "month"]

# Ten years is far past anything the robot has recorded, and keeps one request to a bounded
# amount of reading.
MAX_RANGE_DAYS = 3660

# Every point is sent for the overall figures and again for each cat, so a chart never gets
# more points than it can draw.
MAX_BUCKETS = 800

DEFAULT_DAYS = 30

# A percentage change between two periods of fewer visits than this is mostly chance: ten visits
# that become fifteen is "up 50 percent" and means nothing.
MIN_COUNT_FOR_CHANGE = 20

# Two periods are compared by their per-day rates only when the robot was heard on a similar
# number of days in both, so a period with a hole in it is not read as a drop.
MIN_COVERAGE_RATIO = 0.75


class InvalidRange(ValueError):
    pass


def pick_granularity(days: int) -> Granularity:
    if days <= 92:
        return "day"
    return "week" if days <= 730 else "month"


def bucket_start(day: date, granularity: Granularity) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _next_bucket(start: date, granularity: Granularity) -> date:
    if granularity == "day":
        return start + timedelta(days=1)
    if granularity == "week":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def bucket_count(start: date, end: date, granularity: Granularity) -> int:
    count, cursor, last = 0, bucket_start(start, granularity), bucket_start(end, granularity)
    while cursor <= last:
        count += 1
        cursor = _next_bucket(cursor, granularity)
    return count


@dataclass
class _Tally:
    visits: int = 0
    timed: int = 0
    seconds: int = 0
    cycles: int = 0
    hours: list[int] = field(default_factory=lambda: [0] * 24)
    by_bucket: dict[date, list[int]] = field(default_factory=dict)
    visits_by_day: dict[date, int] = field(default_factory=dict)
    cycles_by_day: dict[date, int] = field(default_factory=dict)

    def add_visit(self, at: datetime, bucket: date, duration_s: int | None) -> None:
        self.visits += 1
        self.visits_by_day[at.date()] = self.visits_by_day.get(at.date(), 0) + 1
        self.hours[at.hour] += 1
        cell = self.by_bucket.setdefault(bucket, [0, 0, 0, 0])
        cell[0] += 1
        if duration_s is not None:
            self.timed += 1
            self.seconds += duration_s
            cell[1] += 1
            cell[2] += duration_s

    def add_cycle(self, day: date, bucket: date) -> None:
        self.cycles += 1
        self.cycles_by_day[day] = self.cycles_by_day.get(day, 0) + 1
        self.by_bucket.setdefault(bucket, [0, 0, 0, 0])[3] += 1

    def avg_visit_s(self) -> float | None:
        return round(self.seconds / self.timed, 1) if self.timed else None


def _change_pct(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round((now - before) / before * 100, 1)


def _rate(by_day: dict[date, int], counted: list[date]) -> float | None:
    """Per day over the days that can be averaged; None when there are none."""
    if not counted:
        return None
    return round(sum(by_day.get(day, 0) for day in counted) / len(counted), 2)


class Insights:
    """Counts visits and cycles over a range of days, in the timezone the owner lives in.

    Days, hours and weeks are the owner's own, so a visit at 23:30 local counts for that day
    and for that hour even though it is the next day in UTC.
    """

    def __init__(
        self,
        store: Store,
        timezone: tzinfo = UTC,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._tz = timezone
        self._clock = clock or (lambda: datetime.now(UTC))
        self._coverage = Coverage(store, timezone, self._clock)

    def today(self) -> date:
        return local_today(self._clock(), self._tz)

    def report(
        self, start: date | None, end: date | None, granularity: Granularity | None
    ) -> dict[str, Any]:
        last = end or self.today()
        first = start or last - timedelta(days=DEFAULT_DAYS - 1)
        if first > last:
            raise InvalidRange("start is after end")
        days = (last - first).days + 1
        if days > MAX_RANGE_DAYS:
            raise InvalidRange(f"Ask for at most {MAX_RANGE_DAYS} days at a time")
        granularity = granularity or pick_granularity(days)
        if bucket_count(first, last, granularity) > MAX_BUCKETS:
            raise InvalidRange(f"That would be more than {MAX_BUCKETS} points; use a wider step")

        previous_last = first - timedelta(days=1)
        previous_first = previous_last - timedelta(days=days - 1)
        cats = self._store.cats(include_archived=True)
        now: dict[Any, _Tally] = {}
        before: dict[Any, _Tally] = {}
        self._count(previous_first, last, first, granularity, now, before)
        known = self._coverage.by_day(previous_first, last)

        def block(key: Any) -> dict[str, Any]:
            return self._block(
                now.get(key, _Tally()),
                before.get(key, _Tally()),
                known,
                first,
                last,
                previous_first,
                previous_last,
                granularity,
                with_cycles=key == "overall",
            )

        listed = []
        for cat in cats:
            if cat["archived_at"] is None or cat["id"] in now or cat["id"] in before:
                listed.append(
                    {
                        "cat_id": cat["id"],
                        "name": cat["name"],
                        "color_index": cat["color_index"],
                        "archived": cat["archived_at"] is not None,
                        **block(cat["id"]),
                    }
                )
        return {
            "timezone": str(self._tz),
            "start": first.isoformat(),
            "end": last.isoformat(),
            "days": days,
            "previous_start": previous_first.isoformat(),
            "previous_end": previous_last.isoformat(),
            "granularity": granularity,
            "coverage": self._coverage_summary(known, first, last),
            "overall": block("overall"),
            "cats": listed,
            "unassigned": block("unassigned"),
        }

    def _count(
        self,
        first: date,
        last: date,
        split: date,
        granularity: Granularity,
        now: dict[Any, _Tally],
        before: dict[Any, _Tally],
    ) -> None:
        rows = self._store.insight_rows(*day_span(first, last, self._tz))
        for row in rows:
            local = datetime.fromisoformat(row["at"]).astimezone(self._tz)
            tallies = now if local.date() >= split else before
            bucket = bucket_start(local.date(), granularity)
            if row["kind"] == "clean_cycle":
                tallies.setdefault("overall", _Tally()).add_cycle(local.date(), bucket)
                continue
            # The owner said this was not a cat, so it is not a visit to count.
            if row["label"] == "not_cat":
                continue
            who = row["cat_id"] if row["label"] == "cat" else "unassigned"
            duration = row["duration_s"]
            for key in ("overall", who):
                tallies.setdefault(key, _Tally()).add_visit(local, bucket, duration)

    @staticmethod
    def _coverage_summary(known: dict[date, Day], first: date, last: date) -> dict[str, Any]:
        inside = [day for day in known.values() if first <= day.date <= last]
        return {
            "full": sum(1 for day in inside if day.state == "full"),
            "partial": sum(1 for day in inside if day.state == "partial"),
            "none": sum(1 for day in inside if day.state == "none"),
            "unreliable": sum(1 for day in inside if day.unreliable is not None),
        }

    def _block(
        self,
        current: _Tally,
        previous: _Tally,
        known: dict[date, Day],
        first: date,
        last: date,
        previous_first: date,
        previous_last: date,
        granularity: Granularity,
        *,
        with_cycles: bool,
    ) -> dict[str, Any]:
        counted = [d for d in _days(first, last) if known[d].counts]
        previous_counted = [d for d in _days(previous_first, previous_last) if known[d].counts]
        series = []
        cursor = bucket_start(first, granularity)
        while cursor <= last:
            visits, timed, seconds, cycles = current.by_bucket.get(cursor, [0, 0, 0, 0])
            end = min(last, _next_bucket(cursor, granularity) - timedelta(days=1))
            states = [known[d].state for d in _days(max(cursor, first), end)]
            heard = any(state != "none" for state in states)
            point: dict[str, Any] = {
                "date": cursor.isoformat(),
                "visits": visits if heard else None,
                "avg_visit_s": round(seconds / timed, 1) if timed and heard else None,
                "coverage": "none"
                if not heard
                else ("full" if all(state == "full" for state in states) else "partial"),
            }
            if with_cycles:
                point["clean_cycles"] = cycles if heard else None
            series.append(point)
            cursor = _next_bucket(cursor, granularity)
        per_day = _rate(current.visits_by_day, counted)
        previous_per_day = _rate(previous.visits_by_day, previous_counted)
        totals: dict[str, Any] = {
            "visits": current.visits,
            "visits_per_day": per_day,
            "days_counted": len(counted),
            "avg_visit_s": current.avg_visit_s(),
        }
        earlier: dict[str, Any] = {
            "visits": previous.visits,
            "visits_per_day": previous_per_day,
            "days_counted": len(previous_counted),
            "avg_visit_s": previous.avg_visit_s(),
        }
        comparable = _comparable(len(counted), len(previous_counted))
        enough = current.visits >= MIN_COUNT_FOR_CHANGE and previous.visits >= MIN_COUNT_FOR_CHANGE
        change: dict[str, Any] = {
            "visits_pct": _change_pct(per_day, previous_per_day) if comparable and enough else None,
            "avg_visit_pct": _change_pct(current.avg_visit_s(), previous.avg_visit_s())
            if current.timed >= MIN_COUNT_FOR_CHANGE and previous.timed >= MIN_COUNT_FOR_CHANGE
            else None,
        }
        if with_cycles:
            cycle_rate = _rate(current.cycles_by_day, counted)
            previous_cycle_rate = _rate(previous.cycles_by_day, previous_counted)
            totals["clean_cycles"] = current.cycles
            totals["clean_cycles_per_day"] = cycle_rate
            earlier["clean_cycles"] = previous.cycles
            earlier["clean_cycles_per_day"] = previous_cycle_rate
            change["clean_cycles_pct"] = (
                _change_pct(cycle_rate, previous_cycle_rate)
                if comparable
                and current.cycles >= MIN_COUNT_FOR_CHANGE
                and previous.cycles >= MIN_COUNT_FOR_CHANGE
                else None
            )
        return {
            **totals,
            "hours": current.hours,
            "previous": earlier,
            "change": change,
            "series": series,
        }


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _comparable(now: int, before: int) -> bool:
    return bool(now and before and min(now, before) / max(now, before) >= MIN_COVERAGE_RATIO)

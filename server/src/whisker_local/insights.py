from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Literal

from .store import Store

Granularity = Literal["day", "week", "month"]

# Ten years is far past anything the robot has recorded, and keeps one request to a bounded
# amount of reading.
MAX_RANGE_DAYS = 3660

# Every point is sent for the overall figures and again for each cat, so a chart never gets
# more points than it can draw.
MAX_BUCKETS = 800

DEFAULT_DAYS = 30


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

    def add_visit(self, at: datetime, bucket: date, duration_s: int | None) -> None:
        self.visits += 1
        self.hours[at.hour] += 1
        cell = self.by_bucket.setdefault(bucket, [0, 0, 0, 0])
        cell[0] += 1
        if duration_s is not None:
            self.timed += 1
            self.seconds += duration_s
            cell[1] += 1
            cell[2] += duration_s

    def add_cycle(self, bucket: date) -> None:
        self.cycles += 1
        self.by_bucket.setdefault(bucket, [0, 0, 0, 0])[3] += 1

    def avg_visit_s(self) -> float | None:
        return round(self.seconds / self.timed, 1) if self.timed else None


def _change_pct(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round((now - before) / before * 100, 1)


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

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def _day_start(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self._tz).astimezone(UTC)

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

        def block(key: Any) -> dict[str, Any]:
            return self._block(
                now.get(key, _Tally()),
                before.get(key, _Tally()),
                days,
                first,
                last,
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
        rows = self._store.insight_rows(
            self._day_start(first), self._day_start(last + timedelta(days=1))
        )
        for row in rows:
            local = datetime.fromisoformat(row["at"]).astimezone(self._tz)
            tallies = now if local.date() >= split else before
            bucket = bucket_start(local.date(), granularity)
            if row["kind"] == "clean_cycle":
                tallies.setdefault("overall", _Tally()).add_cycle(bucket)
                continue
            # The owner said this was not a cat, so it is not a visit to count.
            if row["label"] == "not_cat":
                continue
            who = row["cat_id"] if row["label"] == "cat" else "unassigned"
            duration = row["duration_s"]
            for key in ("overall", who):
                tallies.setdefault(key, _Tally()).add_visit(local, bucket, duration)

    def _block(
        self,
        current: _Tally,
        previous: _Tally,
        days: int,
        first: date,
        last: date,
        granularity: Granularity,
        *,
        with_cycles: bool,
    ) -> dict[str, Any]:
        per_day = current.visits / days
        previous_per_day = previous.visits / days
        series = []
        cursor = bucket_start(first, granularity)
        while cursor <= last:
            visits, timed, seconds, cycles = current.by_bucket.get(cursor, [0, 0, 0, 0])
            point: dict[str, Any] = {
                "date": cursor.isoformat(),
                "visits": visits,
                "avg_visit_s": round(seconds / timed, 1) if timed else None,
            }
            if with_cycles:
                point["clean_cycles"] = cycles
            series.append(point)
            cursor = _next_bucket(cursor, granularity)
        totals: dict[str, Any] = {
            "visits": current.visits,
            "visits_per_day": round(per_day, 2),
            "avg_visit_s": current.avg_visit_s(),
        }
        earlier: dict[str, Any] = {
            "visits": previous.visits,
            "visits_per_day": round(previous_per_day, 2),
            "avg_visit_s": previous.avg_visit_s(),
        }
        change: dict[str, Any] = {
            "visits_pct": _change_pct(current.visits, previous.visits),
            "avg_visit_pct": _change_pct(current.avg_visit_s(), previous.avg_visit_s()),
        }
        if with_cycles:
            totals["clean_cycles"] = current.cycles
            totals["clean_cycles_per_day"] = round(current.cycles / days, 2)
            earlier["clean_cycles"] = previous.cycles
            earlier["clean_cycles_per_day"] = round(previous.cycles / days, 2)
            change["clean_cycles_pct"] = _change_pct(current.cycles, previous.cycles)
        return {
            **totals,
            "hours": current.hours,
            "previous": earlier,
            "change": change,
            "series": series,
        }

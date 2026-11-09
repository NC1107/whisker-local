from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any, Literal

from .cats import WEEK_DAYS, CatTracker, visit_minutes, visit_started
from .coverage import Day
from .store import Store

Period = Literal["week", "month", "year"]

# The month view is the last 30 days, which is what the chart under it is sized for.
MONTH_DAYS = 30
YEAR_MONTHS = 12

# Nobody scrolls back further than this, and it keeps the date arithmetic bounded.
MAX_OFFSET = 520

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _mean(values: list[float | int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 2) if present else None


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _month_start(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def _month_end(index: int) -> date:
    return _month_start(index + 1) - timedelta(days=1)


class CatInsights:
    """The numbers behind one cat's insights screen, one request for all of its charts.

    A series has one entry per day, or per month for the year. An entry is null where the cat was
    not being tracked yet, the day has not come, or the robot was not heard that day. A day on
    which the robot was heard and the cat did not visit is a real zero. Averages leave out the
    days that are partial, have no data or are marked unreliable.
    """

    def __init__(self, store: Store, cats: CatTracker) -> None:
        self._store = store
        self._cats = cats
        self._tz = cats.timezone

    def report(self, cat_id: int, period: Period, offset: int) -> dict[str, Any]:
        cat = self._cats.cat(cat_id)
        today = self._cats.today()
        start, end = self._span(period, offset, today)
        visits = self._store.visits(
            cat_id=cat_id,
            since=self._cats.day_start(start),
            before=self._cats.day_start(end + timedelta(days=1)),
        )
        first_visit = self._store.visits(cat_id=cat_id, limit=1)
        tracked_from = self._local(first_visit[0]).date() if first_visit else None
        buckets = self._buckets(period, start, end)
        known = self._cats.coverage.by_day(start, min(end, today))
        weights, counts, minutes, averaged = self._series(
            period, buckets, visits, tracked_from, today, known
        )
        overview = {
            key: cat["summary"][key]
            for key in (
                "yesterday_weight_lb",
                "yesterday_visits",
                "yesterday_minutes",
                "yesterday_coverage",
                "week_visits",
                "week_days_with_data",
                "common_visit_hour",
            )
        }
        return {
            "cat_id": cat_id,
            "period": period,
            "offset": offset,
            "timezone": str(self._tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "range_label": self._range_label(period, start, end, today),
            "has_earlier": bool(
                self._store.visits(
                    cat_id=cat_id, before=self._cats.day_start(start), newest_first=True, limit=1
                )
            ),
            "points": [bucket.isoformat() for bucket in buckets],
            "coverage": [self._bucket_state(period, b, known) for b in buckets],
            "unreliable": [self._bucket_unreliable(period, b, known) for b in buckets],
            "weight": {"series": weights, "average": _mean(weights)},
            "visits": {"series": counts, "average": _mean(averaged["visits"])},
            "minutes": {"series": minutes, "average": _mean(averaged["minutes"])},
            "time_of_visit": [] if period == "year" else self._time_of_visit(visits, start),
            "overview": overview,
            "summary": self._summary(cat["summary"], tracked_from, known.get(today - timedelta(1))),
        }

    @staticmethod
    def _span(period: Period, offset: int, today: date) -> tuple[date, date]:
        if period == "week":
            end = today - timedelta(days=1 + WEEK_DAYS * offset)
            return end - timedelta(days=WEEK_DAYS - 1), end
        if period == "month":
            end = today - timedelta(days=MONTH_DAYS * offset)
            return end - timedelta(days=MONTH_DAYS - 1), end
        last = _month_index(today) - YEAR_MONTHS * offset
        return _month_start(last - YEAR_MONTHS + 1), _month_end(last)

    @staticmethod
    def _buckets(period: Period, start: date, end: date) -> list[date]:
        if period != "year":
            return [start + timedelta(days=i) for i in range((end - start).days + 1)]
        first = _month_index(start)
        return [_month_start(first + i) for i in range(YEAR_MONTHS)]

    def _local(self, visit: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(visit["at"]).astimezone(self._tz)

    @staticmethod
    def _days_of(period: Period, bucket: date, known: dict[date, Day]) -> list[Day]:
        last = _month_end(_month_index(bucket)) if period == "year" else bucket
        return [known[d] for d in sorted(known) if bucket <= d <= last]

    def _bucket_state(self, period: Period, bucket: date, known: dict[date, Day]) -> str:
        days = self._days_of(period, bucket, known)
        if not days or all(day.state == "none" for day in days):
            return "none"
        return "full" if all(day.state == "full" for day in days) else "partial"

    def _bucket_unreliable(self, period: Period, bucket: date, known: dict[date, Day]) -> bool:
        return any(day.unreliable is not None for day in self._days_of(period, bucket, known))

    def _series(
        self,
        period: Period,
        buckets: list[date],
        visits: list[dict[str, Any]],
        tracked_from: date | None,
        today: date,
        known: dict[date, Day],
    ) -> tuple[
        list[float | None], list[float | None], list[float | None], dict[str, list[float | None]]
    ]:
        by_bucket: dict[date, list[dict[str, Any]]] = {}
        by_day: dict[date, list[dict[str, Any]]] = {}
        for visit in visits:
            day = self._local(visit).date()
            key = day.replace(day=1) if period == "year" else day
            by_bucket.setdefault(key, []).append(visit)
            by_day.setdefault(day, []).append(visit)

        weights: list[float | None] = []
        counts: list[float | None] = []
        minutes: list[float | None] = []
        averaged: dict[str, list[float | None]] = {"visits": [], "minutes": []}
        for bucket in buckets:
            days = [
                day
                for day in self._days_of(period, bucket, known)
                if tracked_from is not None and day.date >= tracked_from and day.date <= today
            ]
            heard = [day for day in days if day.state != "none"]
            if not heard:
                for series in (weights, counts, minutes):
                    series.append(None)
                averaged["visits"].append(None)
                averaged["minutes"].append(None)
                continue
            group = by_bucket.get(bucket, [])
            found = [w for v in group if (w := v["detail"].get("weight_lb")) is not None]
            weights.append(round(statistics.median(found), 2) if found else None)
            if period == "year":
                counted = [day for day in heard if day.counts]
                per_day = self._per_day(counted, by_day)
                counts.append(per_day)
                minutes.append(self._minutes_per_day(counted, by_day))
                averaged["visits"].append(per_day)
                averaged["minutes"].append(minutes[-1])
                continue
            counts.append(len(group))
            minutes.append(visit_minutes(group, heard[0].state))
            counted_day = heard[0].counts
            averaged["visits"].append(len(group) if counted_day else None)
            averaged["minutes"].append(minutes[-1] if counted_day else None)
        return weights, counts, minutes, averaged

    @staticmethod
    def _per_day(counted: list[Day], by_day: dict[date, list[dict[str, Any]]]) -> float | None:
        if not counted:
            return None
        return round(sum(len(by_day.get(day.date, [])) for day in counted) / len(counted), 2)

    @staticmethod
    def _minutes_per_day(
        counted: list[Day], by_day: dict[date, list[dict[str, Any]]]
    ) -> float | None:
        totals = [visit_minutes(by_day.get(day.date, []), day.state) for day in counted]
        if not totals or any(total is None for total in totals):
            return None
        return round(sum(t for t in totals if t is not None) / len(totals), 1)

    def _time_of_visit(self, visits: list[dict[str, Any]], start: date) -> list[dict[str, Any]]:
        points = []
        for visit in visits:
            began = visit_started(visit, self._tz)
            points.append(
                {
                    "day": (self._local(visit).date() - start).days,
                    "hour": round(began.hour + began.minute / 60, 2),
                    "duration_s": visit["detail"].get("duration_s"),
                }
            )
        return points

    @staticmethod
    def _range_label(period: Period, start: date, end: date, today: date) -> str:
        if period == "year":
            first, last = _MONTHS[start.month - 1][:3], _MONTHS[end.month - 1][:3]
            return f"{first} {start.year} to {last} {end.year}"
        with_year = not (start.year == end.year == today.year)

        def name(day: date) -> str:
            text = f"{_MONTHS[day.month - 1]} {day.day}"
            return f"{text}, {day.year}" if with_year else text

        return f"{name(start)} to {name(end)}"

    def _summary(
        self, summary: dict[str, Any], tracked_from: date | None, yesterday: Day | None
    ) -> list[str]:
        if tracked_from is None:
            return ["No visits yet.", "No weight recorded yet."]
        return [
            self._visits_sentence(summary["yesterday_visits"], yesterday),
            self._weight_sentence(summary["latest_weight_lb"]),
        ]

    @staticmethod
    def _visits_sentence(count: int | None, yesterday: Day | None) -> str:
        """What was recorded yesterday, with no verdict on whether it was usual."""
        if count is None:
            return "No data from the robot yesterday."
        recorded = f"{count} {'visit' if count == 1 else 'visits'}" if count else "No visits"
        if yesterday is not None and yesterday.state != "full":
            return f"{recorded} recorded yesterday, but the robot was not heard all day."
        return f"{recorded} yesterday."

    @staticmethod
    def _weight_sentence(latest: float | None) -> str:
        if latest is None:
            return "No weight recorded yet."
        return f"Latest weight reading {latest:.1f} lb."

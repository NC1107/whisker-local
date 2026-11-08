from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any, Literal

from .cats import WEEK_DAYS, WEIGHT_RANGE_PCT, CatTracker
from .store import Store

Period = Literal["week", "month", "year"]

# The month view is the last 30 days, which is what the chart under it is sized for.
MONTH_DAYS = 30
YEAR_MONTHS = 12

# Nobody scrolls back further than this, and it keeps the date arithmetic bounded.
MAX_OFFSET = 520

# Yesterday is compared with the cat's own recent days, not with other cats.
BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 3

# "About average" has to allow for a cat whose days are one or two visits, where a single extra
# visit is a quarter of the average or more.
AVERAGE_BAND_VISITS = 1.0
AVERAGE_BAND_SHARE = 0.25

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

    A series has one entry per day, or per month for the year, and an entry is null where the cat
    was not being tracked yet or the day has not come. A tracked day with no visits is a real zero.
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
        weights, counts, minutes = self._series(period, buckets, visits, tracked_from, today)
        overview = {
            key: cat["summary"][key]
            for key in (
                "yesterday_weight_lb",
                "yesterday_visits",
                "yesterday_minutes",
                "week_visits",
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
            "weight": {"series": weights, "average": _mean(weights)},
            "visits": {"series": counts, "average": _mean(counts)},
            "minutes": {"series": minutes, "average": _mean(minutes)},
            "time_of_visit": [] if period == "year" else self._time_of_visit(visits, start),
            "overview": overview,
            "summary": self._summary(cat_id, cat["summary"], tracked_from, today),
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

    def _series(
        self,
        period: Period,
        buckets: list[date],
        visits: list[dict[str, Any]],
        tracked_from: date | None,
        today: date,
    ) -> tuple[list[float | None], list[float | None], list[float | None]]:
        by_bucket: dict[date, list[dict[str, Any]]] = {}
        for visit in visits:
            day = self._local(visit).date()
            key = day.replace(day=1) if period == "year" else day
            by_bucket.setdefault(key, []).append(visit)

        weights: list[float | None] = []
        counts: list[float | None] = []
        minutes: list[float | None] = []
        for bucket in buckets:
            tracked_days = self._tracked_days(period, bucket, tracked_from, today)
            group = by_bucket.get(bucket, [])
            if tracked_days == 0:
                weights.append(None)
                counts.append(None)
                minutes.append(None)
                continue
            found = [w for v in group if (w := v["detail"].get("weight_lb")) is not None]
            seconds = sum(v["detail"].get("duration_s") or 0 for v in group)
            weights.append(round(statistics.median(found), 2) if found else None)
            counts.append(round(len(group) / tracked_days, 2))
            minutes.append(round(seconds / 60 / tracked_days, 1))
        return weights, counts, minutes

    @staticmethod
    def _tracked_days(period: Period, bucket: date, tracked_from: date | None, today: date) -> int:
        """Days in the bucket on which the cat could have visited, as far as we know."""
        if tracked_from is None:
            return 0
        last = _month_end(_month_index(bucket)) if period == "year" else bucket
        first = bucket
        return max(0, (min(last, today) - max(first, tracked_from)).days + 1)

    def _time_of_visit(self, visits: list[dict[str, Any]], start: date) -> list[dict[str, Any]]:
        points = []
        for visit in visits:
            ended = self._local(visit)
            began = self._began(visit, ended)
            points.append(
                {
                    "day": (ended.date() - start).days,
                    "hour": round(began.hour + began.minute / 60, 2),
                    "duration_s": visit["detail"].get("duration_s"),
                }
            )
        return points

    def _began(self, visit: dict[str, Any], ended: datetime) -> datetime:
        """When the cat went in, or the end if that is unknown or fell on the day before."""
        started = visit["detail"].get("started")
        if started is None:
            return ended
        began = datetime.fromisoformat(started).astimezone(self._tz)
        return began if began.date() == ended.date() else ended

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
        self, cat_id: int, summary: dict[str, Any], tracked_from: date | None, today: date
    ) -> list[str]:
        if tracked_from is None:
            return ["No visits yet.", "No weight recorded yet."]
        baseline = self._baseline(cat_id, tracked_from, today)
        return [
            self._visits_sentence(summary["yesterday_visits"], baseline),
            self._weight_sentence(summary["latest_weight_lb"], summary["usual_weight_lb"]),
        ]

    def _baseline(self, cat_id: int, tracked_from: date, today: date) -> float | None:
        """Visits per day over the days before yesterday, or None while there are too few."""
        yesterday = today - timedelta(days=1)
        first = max(tracked_from, yesterday - timedelta(days=BASELINE_DAYS))
        days = (yesterday - first).days
        if days < MIN_BASELINE_DAYS:
            return None
        visits = self._store.visits(
            cat_id=cat_id,
            since=self._cats.day_start(first),
            before=self._cats.day_start(yesterday),
        )
        return len(visits) / days

    @staticmethod
    def _visits_sentence(count: int, baseline: float | None) -> str:
        lead = (
            f"{count} {'visit' if count == 1 else 'visits'} yesterday"
            if count
            else ("No visits yesterday")
        )
        if baseline is None:
            return f"{lead}."
        gap = count - baseline
        if abs(gap) <= max(AVERAGE_BAND_VISITS, baseline * AVERAGE_BAND_SHARE):
            return f"{lead}, about average."
        return f"{lead}, {'more' if gap > 0 else 'fewer'} than usual."

    @staticmethod
    def _weight_sentence(latest: float | None, usual: float | None) -> str:
        if latest is None or not usual:
            return "No weight recorded yet."
        change = (latest - usual) / usual * 100
        if abs(change) <= WEIGHT_RANGE_PCT:
            return "Weight is in range."
        return f"Weight is {'above' if change > 0 else 'below'} the usual range."

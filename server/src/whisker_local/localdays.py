"""Days as the owner lives them: midnight to midnight in their timezone, as UTC instants."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo


def day_start(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def day_span(first: date, last: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """The instants from the start of ``first`` up to, not including, the day after ``last``."""
    return day_start(first, tz), day_start(last + timedelta(days=1), tz)


def local_today(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from . import alert_settings
from .store import Store

# The keys are the ones the alerts library reads from Snapshot.maintenance.
JOBS: dict[str, tuple[str, str]] = {
    "filter": ("Carbon filter", "filter_days"),
    "deep_clean": ("Deep clean", "deep_clean_days"),
    "globe_clean": ("Globe clean", "globe_clean_days"),
}

# Nothing about this robot was cleaned before it existed, so an older date is a typo.
EARLIEST_DONE_ON = date(2020, 1, 1)


class MaintenanceInFuture(ValueError):
    pass


class MaintenanceNotFound(LookupError):
    pass


class MaintenanceLog:
    """What was done to the robot and when, and so what is due.

    The robot cannot tell us any of this, so it is a log the owner keeps. Marking a job done
    is logging only: there is no way to tell the robot to run, or to reset, any of these.
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

    def record(self, kind: str, done_on: date | None) -> tuple[dict[str, Any], bool]:
        day = done_on or self.today()
        if day > self.today():
            raise MaintenanceInFuture(day)
        entry, created = self._store.add_maintenance(kind, day, self._clock())
        return _entry(entry), created

    def delete(self, entry_id: int) -> None:
        if not self._store.delete_maintenance(entry_id):
            raise MaintenanceNotFound(entry_id)

    def view(self, *, limit: int) -> dict[str, Any]:
        today = self.today()
        settings = alert_settings.load(self._store)
        last_done = self._store.maintenance_last_done()
        items = []
        for kind, (label, setting) in JOBS.items():
            interval = getattr(settings, setting)
            last = last_done.get(kind)
            due = None if last is None else last + timedelta(days=interval)
            items.append(
                {
                    "kind": kind,
                    "label": label,
                    "interval_days": interval,
                    "last_done": None if last is None else last.isoformat(),
                    "due": None if due is None else due.isoformat(),
                    "days_until_due": None if due is None else (due - today).days,
                    "is_due": due is not None and today >= due,
                }
            )
        return {
            "today": today.isoformat(),
            "items": items,
            "log": [_entry(row) for row in self._store.maintenance_entries(limit=limit)],
        }


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": JOBS[row["kind"]][0],
        "done_on": row["done_on"],
        "recorded_at": row["recorded_at"],
    }

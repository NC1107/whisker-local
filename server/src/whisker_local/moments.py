"""Notices that come from things that happened, not from a condition that holds.

A visit, a finished cycle, a new firmware and a changed control lock are all saved as events
first. Each is announced at most once: the highest event id announced per kind is kept in the
store before the push is queued, so a restart can lose a notice but never repeat one. Nothing
here reads the cat sensor bit, which the globe raises in every cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from statistics import median
from typing import Any

from . import alert_settings, push_options
from .cats import MIN_READINGS_FOR_CHANGE, RECENT_WEIGHT_DAYS, CatTracker
from .push import Push, PushNotifier
from .push_catalog import lock_text, short_name, visit_text
from .store import Store

log = logging.getLogger(__name__)

KEY = "push_moments"

# The weight usually lands within seconds of the end of a visit. A visit that still has none
# this long after it ended is announced without one.
NO_WEIGHT_WAIT = timedelta(seconds=60)
# After a long outage, announcing what happened hours ago is noise.
STALE_AFTER = timedelta(hours=1)
# A daily summary that is hours late is skipped, because it names a morning that has passed.
SUMMARY_GRACE = timedelta(hours=3)
POWER_HOLD = timedelta(minutes=2)
SUMMARY_EVENT_LIMIT = 500


class PowerWatch:
    """Mains power lost: the robot reports battery power (register 0x07) for a couple of minutes."""

    def __init__(self, *, active: bool = False) -> None:
        self.active = active
        self._since: datetime | None = None

    def update(self, on_battery: bool | None, now: datetime) -> bool | None:
        """True when the alert is raised, False when it clears, None for no change."""
        if on_battery is None:
            return None
        if not on_battery:
            self._since = None
            if self.active:
                self.active = False
                return False
            return None
        if self.active:
            return None
        self._since = self._since or now
        if now - self._since >= POWER_HOLD:
            self.active = True
            return True
        return None


class Moments:
    def __init__(
        self,
        store: Store,
        notifier: PushNotifier,
        cats: CatTracker,
        timezone: tzinfo = UTC,
    ) -> None:
        self._store = store
        self._notifier = notifier
        self._cats = cats
        self._tz = timezone
        self._waiting: list[int] = []
        state = store.get_json(KEY)
        self._state: dict[str, Any] = state if isinstance(state, dict) else {}
        self.power = PowerWatch(active=bool(self._state.get("power_active")))

    # State

    def _save(self) -> None:
        self._store.put_json(KEY, self._state)

    def _cursors(self) -> dict[str, int]:
        cursors = self._state.get("cursors")
        if not isinstance(cursors, dict):
            cursors = self._state["cursors"] = {}
        return cursors

    def claim(self, kind: str, event_id: int) -> bool:
        """True the first time an event is seen, and never again, even after a restart."""
        cursors = self._cursors()
        if event_id <= cursors.get(kind, 0):
            return False
        cursors[kind] = event_id
        self._save()
        return True

    def power_changed(self, active: bool) -> None:
        self._state["power_active"] = active
        self._save()

    # Visits

    def resume(self, now: datetime) -> None:
        """Pick up visits saved but not yet announced when the last run stopped."""
        cursors = self._cursors()
        newest = self._store.visits(newest_first=True, limit=1)
        if "visit" not in cursors:
            # Switching push on must not announce the whole history.
            cursors["visit"] = newest[0]["id"] if newest else 0
            self._save()
            return
        recent = self._store.visits(since=now - STALE_AFTER, exclude_imported=True)
        self._waiting = [v["id"] for v in recent if v["id"] > cursors["visit"]]

    def visit_saved(self, event_id: int) -> None:
        self._waiting.append(event_id)

    def settle(self, now: datetime) -> None:
        """Announce the saved visits whose weight is known, or can no longer be waited for."""
        self._announce_visits(now)
        self._daily_summary(now)

    def _announce_visits(self, now: datetime) -> None:
        for event_id in sorted(set(self._waiting)):
            row = self._store.visit(event_id)
            if row is not None and not self._ready(row, now):
                # Later visits wait behind this one, so announcements keep their order.
                break
            self._waiting.remove(event_id)
            if row is None or not self.claim("visit", event_id):
                continue
            try:
                self._announce_visit(row, now)
            except Exception:
                log.exception("could not announce visit %s", event_id)

    @staticmethod
    def _ready(row: dict[str, Any], now: datetime) -> bool:
        if row["detail"].get("weight_lb") is not None:
            return True
        return now - datetime.fromisoformat(row["at"]) >= NO_WEIGHT_WAIT

    def _announce_visit(self, row: dict[str, Any], now: datetime) -> None:
        if row["detail"].get("origin") != "live" or row["label"] == "not_cat":
            return
        if now - datetime.fromisoformat(row["at"]) > STALE_AFTER:
            return
        cat_id = row["cat_id"] if row["label"] == "cat" else None
        cat = None if cat_id is None else self._store.cat(cat_id)
        name = None if cat is None else cat["name"]
        detail = row["detail"]
        settings = alert_settings.load(self._store)

        title, body = visit_text("visit", name, detail)
        self._notifier.notify(Push("visit", body, False, title, cat_id))

        if (detail.get("duration_s") or 0) >= settings.long_visit_minutes * 60:
            title, body = visit_text("long_visit", name, detail)
            self._notifier.notify(Push("long_visit", body, False, title, cat_id))

        self._frequent(row, settings.frequent_visits_per_hour)
        if cat_id is not None:
            self._weight_change(row, cat_id, name or "", settings.weight_change_percent)

    def _frequent(self, row: dict[str, Any], per_hour: int) -> None:
        ended = datetime.fromisoformat(row["at"])
        floor = ended - timedelta(hours=1)
        after = self._state.get("frequent_after")
        if isinstance(after, str):
            floor = max(floor, datetime.fromisoformat(after))
        recent = [
            v
            for v in self._store.visits(since=floor, exclude_imported=True)
            if v["label"] != "not_cat" and floor < datetime.fromisoformat(v["at"]) <= ended
        ]
        if len(recent) < per_hour:
            return
        # One notice per burst, not one for every visit inside it.
        self._state["frequent_after"] = row["at"]
        self._save()
        self._notifier.notify(
            Push(
                "frequent_visits",
                f"{len(recent)} visits in the last hour.",
                False,
                "Busy box",
            )
        )

    def _weight_change(self, row: dict[str, Any], cat_id: int, name: str, percent: int) -> None:
        weight = row["detail"].get("weight_lb")
        if weight is None:
            return
        at = datetime.fromisoformat(row["at"])
        history = self._cats.confident_visits(
            cat_id, since=at - timedelta(days=RECENT_WEIGHT_DAYS), before=at
        )
        weights = [
            float(v["detail"]["weight_lb"])
            for v in history
            if v["detail"].get("weight_lb") is not None
        ]
        if len(weights) < MIN_READINGS_FOR_CHANGE:
            return
        usual = median(weights)
        change = (weight - usual) / usual * 100
        if abs(change) < percent:
            return
        title, body = visit_text(
            "weight_change",
            name,
            {"weight_lb": weight, "usual_lb": round(usual, 1), "change_percent": round(change)},
        )
        self._notifier.notify(Push("weight_change", body, False, title, cat_id))

    # Other events

    def cycle_completed(self, event_id: int, drawer_percent: int | None) -> None:
        if self.claim("clean_cycle", event_id):
            body = (
                "The clean cycle is done."
                if drawer_percent is None
                else f"The drawer is {drawer_percent}% full."
            )
            self._notifier.notify(Push("cycle_completed", body, False, "Cycle finished"))

    def firmware_updated(self, event_id: int, version: str) -> None:
        if self.claim("firmware", event_id):
            self._notifier.notify(
                Push("firmware_updated", f"Firmware is now {version}.", False, "Robot updated")
            )

    def panel_lock_changed(self, event_id: int, locked: bool) -> None:
        if self.claim("panel_lock", event_id):
            title, body = lock_text(locked)
            self._notifier.notify(Push("panel_lock_changed", body, False, title))

    def on_irregularity(
        self, cat: int | Mapping[str, Any] | None, kind: str, title: str, body: str
    ):
        """A change in a cat's routine, worked out elsewhere, offered as a notice.

        ``cat`` is a cat id or a cat record, or None when it is about the household. ``kind``
        names the sort of change, so two different changes for one cat do not replace each other.
        """
        cat_id = cat.get("id") if isinstance(cat, Mapping) else cat
        return self._notifier.notify(
            Push(
                "behaviour_change",
                body,
                False,
                title,
                cat_id if type(cat_id) is int else None,
                kind,
            )
        )

    # Daily summary

    def _daily_summary(self, now: datetime) -> None:
        local = now.astimezone(self._tz)
        today = local.date().isoformat()
        if self._state.get("summary_day") == today:
            return
        hour = push_options.load(self._store)["daily_summary_hour"]
        due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if local < due:
            return
        self._state["summary_day"] = today
        self._save()
        if local - due > SUMMARY_GRACE:
            return
        body = self._summary_body(local.date() - timedelta(days=1))
        self._notifier.notify(Push("daily_summary", body, False, "Yesterday in the box"))

    def _summary_body(self, day: date) -> str:
        start = self._cats.day_start(day)
        end = self._cats.day_start(day + timedelta(days=1))
        visits = [v for v in self._store.visits(since=start, before=end) if v["label"] != "not_cat"]
        names = {c["id"]: short_name(c["name"]) for c in self._store.cats(include_archived=True)}
        counts: dict[str, int] = {}
        for v in visits:
            who = names.get(v["cat_id"], "Unknown") if v["label"] == "cat" else "Unknown"
            counts[who] = counts.get(who, 0) + 1
        parts = [
            f"{who}: {n} {'visit' if n == 1 else 'visits'}."
            for who, n in sorted(counts.items(), key=lambda item: (item[0] == "Unknown", item[0]))
        ] or ["No visits."]
        cycles = [
            e
            for e in self._store.events(limit=SUMMARY_EVENT_LIMIT, kinds=["clean_cycle"])
            if start <= datetime.fromisoformat(e["at"]) < end
        ]
        if cycles:
            parts.append(f"{len(cycles)} {'cycle' if len(cycles) == 1 else 'cycles'}.")
            drawer = cycles[0]["detail"].get("drawer_percent")
            if drawer is not None:
                parts.append(f"Drawer {drawer}%.")
        return " ".join(parts)

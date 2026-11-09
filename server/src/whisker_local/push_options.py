"""The choices around push that are not an on or off per kind: quiet hours and per-cat filters.

Stored sparsely in one document, like the kinds and the alert settings, so only what a person
set on purpose is kept and a default that changes later still follows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from typing import Any

from .push_catalog import CATALOG
from .store import Store

KEY = "push_options"

QUIET_MODES = ("hold", "drop")
CAT_MODES = ("any", "selected")
DAILY_SUMMARY_HOURS = (0, 23)
DEFAULT_SUMMARY_HOUR = 8

_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CAT_KINDS = frozenset(kind.key for kind in CATALOG if kind.supports_cats)

DEFAULT_QUIET: dict[str, Any] = {
    "enabled": False,
    "start": "22:00",
    "end": "07:00",
    "mode": "hold",
}
DEFAULT_CATS: dict[str, Any] = {"mode": "any", "cat_ids": [], "include_unknown": False}


def parse_clock(value: Any) -> time | None:
    match = _CLOCK.match(value) if isinstance(value, str) else None
    return None if match is None else time(int(match[1]), int(match[2]))


@dataclass(frozen=True, slots=True)
class QuietHours:
    enabled: bool
    start: time
    end: time
    mode: str

    def covers(self, moment: time) -> bool:
        if not self.enabled or self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


def _quiet(stored: Any) -> dict[str, Any]:
    out = dict(DEFAULT_QUIET)
    if not isinstance(stored, dict):
        return out
    if type(stored.get("enabled")) is bool:
        out["enabled"] = stored["enabled"]
    for edge in ("start", "end"):
        if parse_clock(stored.get(edge)) is not None:
            out[edge] = stored[edge]
    if stored.get("mode") in QUIET_MODES:
        out["mode"] = stored["mode"]
    if out["start"] == out["end"]:
        out["start"], out["end"] = DEFAULT_QUIET["start"], DEFAULT_QUIET["end"]
    return out


def _cats(stored: Any) -> dict[str, Any]:
    out = dict(DEFAULT_CATS)
    if not isinstance(stored, dict):
        return {**out, "cat_ids": []}
    if stored.get("mode") in CAT_MODES:
        out["mode"] = stored["mode"]
    ids = stored.get("cat_ids")
    out["cat_ids"] = sorted({i for i in ids if type(i) is int}) if isinstance(ids, list) else []
    if type(stored.get("include_unknown")) is bool:
        out["include_unknown"] = stored["include_unknown"]
    if out["mode"] == "selected" and not out["cat_ids"]:
        out["mode"] = "any"
    return out


def load(store: Store) -> dict[str, Any]:
    stored = store.get_json(KEY)
    stored = stored if isinstance(stored, dict) else {}
    chosen = stored.get("cats") if isinstance(stored.get("cats"), dict) else {}
    hour = stored.get("daily_summary_hour")
    valid_hour = type(hour) is int and DAILY_SUMMARY_HOURS[0] <= hour <= DAILY_SUMMARY_HOURS[1]
    return {
        "quiet_hours": _quiet(stored.get("quiet_hours")),
        "cats": {key: _cats(chosen.get(key)) for key in sorted(CAT_KINDS)},
        "daily_summary_hour": hour if valid_hour else DEFAULT_SUMMARY_HOUR,
    }


class InvalidOptions(ValueError):
    pass


def update(
    store: Store, changes: dict[str, Any], *, known_cats: set[int] | None = None
) -> dict[str, Any]:
    """Merge changes into what was stored, refusing a combination that would do nothing."""
    stored = store.get_json(KEY)
    stored = dict(stored) if isinstance(stored, dict) else {}
    if "quiet_hours" in changes:
        quiet = {**_quiet(stored.get("quiet_hours")), **changes["quiet_hours"]}
        if quiet["start"] == quiet["end"]:
            raise InvalidOptions("Quiet hours need different start and end times")
        stored["quiet_hours"] = quiet
    if "cats" in changes:
        kept = stored.get("cats") if isinstance(stored.get("cats"), dict) else {}
        merged = dict(kept)
        for kind, change in changes["cats"].items():
            choice = {**_cats(kept.get(kind)), **change}
            choice["cat_ids"] = sorted(set(choice["cat_ids"]))
            if known_cats is not None and not set(choice["cat_ids"]) <= known_cats:
                raise InvalidOptions("Pick cats that exist")
            if choice["mode"] == "selected" and not choice["cat_ids"]:
                raise InvalidOptions("Pick at least one cat")
            merged[kind] = choice
        stored["cats"] = merged
    if "daily_summary_hour" in changes:
        stored["daily_summary_hour"] = changes["daily_summary_hour"]
    store.put_json(KEY, stored)
    return load(store)


def quiet_hours(options: dict[str, Any]) -> QuietHours:
    quiet = options["quiet_hours"]
    return QuietHours(
        quiet["enabled"],
        parse_clock(quiet["start"]) or time(22),
        parse_clock(quiet["end"]) or time(7),
        quiet["mode"],
    )


def wants_cat(options: dict[str, Any], kind: str, cat_id: int | None) -> bool:
    """Whether a visit notice for ``cat_id`` passes the filter; None means no cat was named."""
    choice = options["cats"].get(kind)
    if choice is None or choice["mode"] == "any":
        return True
    if cat_id is None:
        return choice["include_unknown"]
    return cat_id in choice["cat_ids"]

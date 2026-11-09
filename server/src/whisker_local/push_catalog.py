"""What can be sent as a push: the kinds, how they are grouped, and what each one says.

The app renders its screen from this table, so a kind that is listed here is a kind the server
really sends. Wording is kept short enough for a lock screen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from whiskerless.devices.litter_robot_4 import alerts

# Titles and bodies are cut by bytes, because the relay limits bytes and a name can be any text.
MAX_TITLE_BYTES = 256
MAX_BODY_BYTES = 1024
MAX_COLLAPSE_BYTES = 64
MAX_CAT_NAME_CHARS = 20

GROUP_NOW = "Needs you now"
GROUP_LITTER = "Litter and waste"
GROUP_UPKEEP = "Upkeep"
GROUP_CATS = "Cats"
GROUP_ROUTINE = "Routine"
GROUP_BEHAVIOUR = "Behaviour changes"
GROUPS = (GROUP_NOW, GROUP_LITTER, GROUP_UPKEEP, GROUP_CATS, GROUP_ROUTINE, GROUP_BEHAVIOUR)

# What the phone shows for kinds that are not one of ours, such as a test.
FALLBACK_TITLE = "Litter robot"


@dataclass(frozen=True, slots=True)
class Threshold:
    """A number the person can change for a kind. Bounds are the same ones the API enforces."""

    setting: str
    unit: str
    low: int
    high: int


@dataclass(frozen=True, slots=True)
class Kind:
    key: str
    group: str
    name: str
    default: bool
    urgent: bool = False
    # A per-cat choice only makes sense for kinds that come from one visit.
    supports_cats: bool = False
    threshold: Threshold | None = None
    # Hidden until the robot shows the thing this kind is about.
    needs: str | None = None

    def view(self) -> dict[str, Any]:
        threshold = self.threshold
        return {
            "key": self.key,
            "group": self.group,
            "name": self.name,
            "default": self.default,
            "urgent": self.urgent,
            "supports_cats": self.supports_cats,
            "needs": self.needs,
            "threshold": None
            if threshold is None
            else {
                "setting": threshold.setting,
                "unit": threshold.unit,
                "min": threshold.low,
                "max": threshold.high,
            },
        }


DRAWER_LEVEL_RANGE = (50, 95)
LITTER_LOW_RANGE = (10, 50)
LONG_VISIT_RANGE = (2, 5)
VISITS_PER_HOUR_RANGE = (3, 10)
NO_VISIT_HOURS_RANGE = (1, 720)

CATALOG: tuple[Kind, ...] = (
    Kind("globe_fault", GROUP_NOW, "Motor fault", True, urgent=True),
    Kind("stuck_cycle", GROUP_NOW, "Cycle stuck", True, urgent=True),
    Kind("excess_weight", GROUP_NOW, "Weight on the scale", True, urgent=True),
    Kind("drawer_full_lockout", GROUP_NOW, "Drawer full, robot stopped", True, urgent=True),
    Kind("bonnet_removed", GROUP_NOW, "Bonnet off", True, urgent=True),
    Kind("cat_sensor_blocked", GROUP_NOW, "Cat sensor blocked", True, urgent=True),
    Kind("offline", GROUP_NOW, "Robot offline", True, urgent=True),
    Kind("power_loss", GROUP_NOW, "Running on battery", True, urgent=True),
    Kind("drawer_full", GROUP_LITTER, "Drawer full", True),
    Kind(
        "drawer_level",
        GROUP_LITTER,
        "Drawer filling up",
        True,
        threshold=Threshold("drawer_level_percent", "%", *DRAWER_LEVEL_RANGE),
    ),
    Kind(
        "litter_low",
        GROUP_LITTER,
        "Litter low",
        True,
        threshold=Threshold("litter_low_percent", "%", *LITTER_LOW_RANGE),
    ),
    Kind("hopper_empty", GROUP_LITTER, "Hopper empty", True, needs="hopper"),
    Kind("deep_clean_due", GROUP_UPKEEP, "Deep clean due", True, needs="maintenance"),
    Kind("filter_due", GROUP_UPKEEP, "Filter due", True, needs="maintenance"),
    Kind("globe_clean_due", GROUP_UPKEEP, "Globe clean due", True, needs="maintenance"),
    Kind("visit", GROUP_CATS, "A cat used the box", False, supports_cats=True),
    Kind(
        "long_visit",
        GROUP_CATS,
        "A cat stayed a long time",
        False,
        supports_cats=True,
        threshold=Threshold("long_visit_minutes", "min", *LONG_VISIT_RANGE),
    ),
    Kind(
        "frequent_visits",
        GROUP_CATS,
        "Many visits in an hour",
        False,
        threshold=Threshold("frequent_visits_per_hour", "visits", *VISITS_PER_HOUR_RANGE),
    ),
    Kind(
        "no_visit",
        GROUP_CATS,
        "No visit for a while",
        True,
        threshold=Threshold("no_visit_hours", "hours", *NO_VISIT_HOURS_RANGE),
    ),
    Kind("weight_change", GROUP_CATS, "Weight changed", False),
    Kind("cycle_completed", GROUP_ROUTINE, "Clean cycle finished", False),
    Kind("panel_lock_changed", GROUP_ROUTINE, "Control lock changed", False),
    Kind("firmware_updated", GROUP_ROUTINE, "Firmware updated", True),
    Kind("daily_summary", GROUP_ROUTINE, "Daily summary", False),
    # Filled in by the workstream that watches for changes in routine.
    Kind("behaviour_change", GROUP_BEHAVIOUR, "Changes in routine", False),
)

BY_KEY: dict[str, Kind] = {kind.key: kind for kind in CATALOG}
DEFAULT_KINDS: dict[str, bool] = {kind.key: kind.default for kind in CATALOG}

# Drawer notices replace each other on the phone, and the worst one wins when several are ready.
DRAWER_RANK = {"drawer_level": 1, "drawer_full": 2, "drawer_full_lockout": 3}
DRAWER_COLLAPSE = "drawer"


def catalog_view() -> list[dict[str, Any]]:
    return [kind.view() for kind in CATALOG]


def is_urgent(key: str) -> bool:
    kind = BY_KEY.get(key)
    return kind is not None and kind.urgent


def trim_bytes(text: str, limit: int) -> str:
    """Cut to at most ``limit`` UTF-8 bytes without splitting a character."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    marker = "…".encode()
    cut = raw[: limit - len(marker)].decode("utf-8", errors="ignore")
    return cut.rstrip() + "…"


def short_name(name: str) -> str:
    name = " ".join(name.split())
    if len(name) <= MAX_CAT_NAME_CHARS:
        return name
    return name[: MAX_CAT_NAME_CHARS - 1].rstrip() + "…"


def collapse_id(kind: str, cat_id: int | None = None, topic: str | None = None) -> str:
    """The phone replaces an earlier notice with the same id, so a cat gets an id of its own."""
    if kind in DRAWER_RANK:
        return DRAWER_COLLAPSE
    parts = [kind, topic, None if cat_id is None else str(cat_id)]
    return trim_bytes(":".join(p for p in parts if p), MAX_COLLAPSE_BYTES)


def _length(seconds: Any) -> str:
    minutes, rest = divmod(int(seconds or 0), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _pounds(details: Mapping[str, Any]) -> str:
    weight = details.get("weight_lb")
    return "" if weight is None else f", {weight} lb"


def _days_since(details: Mapping[str, Any], what: str) -> str:
    days = details.get("days")
    if days is None:
        return f"A {what} is due."
    return f"It has been {days} days since the last {what}."


def _globe_fault(details: Mapping[str, Any]) -> str:
    cause = details.get("cause")
    if cause == "pinch":
        return "The globe detected a pinch and stopped. Check for a trapped item."
    if cause and cause != "unknown":
        return f"Globe motor fault: {str(cause).replace('_', ' ')}. Check the robot."
    return "Globe motor fault. Check the robot."


def _litter_low(details: Mapping[str, Any]) -> str:
    level = details.get("level")
    about = "about " if details.get("estimated") else ""
    return f"The globe is at {about}{level}%. Add litter."


_TITLES: dict[str, str] = {
    "globe_fault": "Robot has stopped",
    "stuck_cycle": "Cycle is stuck",
    "excess_weight": "Something is on the scale",
    "drawer_full_lockout": "Robot stopped, drawer full",
    "bonnet_removed": "Bonnet is off",
    "cat_sensor_blocked": "Cat sensor blocked",
    "offline": "Robot is offline",
    "power_loss": "Running on battery",
    "drawer_full": "Waste drawer is full",
    "drawer_level": "Drawer filling up",
    "litter_low": "Litter is low",
    "hopper_empty": "Hopper is empty",
    "deep_clean_due": "Deep clean due",
    "filter_due": "New filter due",
    "globe_clean_due": "Globe clean due",
    "frequent_visits": "Busy box",
    "cycle_completed": "Cycle finished",
    "firmware_updated": "Robot updated",
    "daily_summary": "Yesterday in the box",
    "test": "Test notification",
}

_BODIES: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "globe_fault": _globe_fault,
    "stuck_cycle": lambda _: "A clean cycle has run for 30 minutes without finishing.",
    "excess_weight": lambda _: (
        "Something has weighed on the box for 30 minutes. The robot will not cycle."
    ),
    "drawer_full_lockout": lambda _: (
        "Empty the drawer and press Reset. The robot ignores cats until you do."
    ),
    "bonnet_removed": lambda _: "The bonnet has been off for 5 minutes. The robot will not cycle.",
    "cat_sensor_blocked": lambda _: (
        "The sensor has been blocked for 30 minutes. Check the globe and the bonnet."
    ),
    "offline": lambda _: "No word from the robot for 15 minutes.",
    "power_loss": lambda _: "The robot lost mains power. Check the adapter.",
    "drawer_full": lambda _: "Empty the drawer and press Reset.",
    "drawer_level": lambda d: f"The waste drawer is {d.get('level')}% full.",
    "litter_low": _litter_low,
    "hopper_empty": lambda _: "Refill the LitterHopper.",
    "deep_clean_due": lambda d: _days_since(d, "deep clean"),
    "filter_due": lambda d: _days_since(d, "filter change"),
    "globe_clean_due": lambda d: _days_since(d, "globe clean"),
    "frequent_visits": lambda d: f"{d.get('visits')} visits in the last hour.",
    "cycle_completed": lambda d: (
        f"The drawer is {d.get('drawer_percent')}% full."
        if d.get("drawer_percent") is not None
        else "The clean cycle is done."
    ),
    "firmware_updated": lambda d: f"Firmware is now {d.get('version')}.",
    "test": lambda _: "Notifications are working.",
}

_CLEARED_TITLES = {"offline": "Robot is back"}


def alert_text(key: str, details: Mapping[str, Any], raised: bool = True) -> tuple[str, str]:
    """Title and body for a condition alert, falling back to the library's own sentence."""
    if not raised:
        title = _CLEARED_TITLES.get(key, _TITLES.get(key, FALLBACK_TITLE))
        try:
            return title, alerts.describe(alerts.Alert(key), False, details)
        except ValueError:
            return title, "Back to normal."
    if key == "no_visit":
        hours = details.get("hours")
        return f"No visits for {hours} hours", _no_visit_body(details)
    body = _BODIES.get(key)
    if body is not None:
        return _TITLES.get(key, FALLBACK_TITLE), body(details)
    try:
        return _TITLES.get(key, FALLBACK_TITLE), alerts.describe(alerts.Alert(key), True, details)
    except ValueError:
        return _TITLES.get(key, FALLBACK_TITLE), ""


def _no_visit_body(details: Mapping[str, Any]) -> str:
    since = details.get("since")
    return f"No cat has used the box since {since}." if since else "No cat has used the box."


def visit_text(kind: str, name: str | None, details: Mapping[str, Any]) -> tuple[str, str]:
    """Wording for the notices that come from one saved visit."""
    who = None if name is None else short_name(name)
    length = _length(details.get("duration_s"))
    if kind == "visit":
        body = f"{length}{_pounds(details)}."
        return (f"{who} used the box" if who else "A cat used the box"), body
    if kind == "long_visit":
        title = f"{who} stayed a while" if who else "A cat stayed a while"
        minutes = max(1, int(details.get("duration_s") or 0) // 60)
        return title, f"{minutes} min in the box."
    if kind == "weight_change":
        usual = details.get("usual_lb")
        change = details.get("change_percent")
        return (
            f"{who}'s weight changed" if who else "A cat's weight changed",
            f"{details.get('weight_lb')} lb, {change:+}% from the usual {usual} lb.",
        )
    raise ValueError(kind)


def lock_text(locked: bool) -> tuple[str, str]:
    state = "on" if locked else "off"
    return f"Control lock {state}", f"The panel is now {'locked' if locked else 'unlocked'}."

from __future__ import annotations

from dataclasses import replace
from typing import Any

from whiskerless.devices.litter_robot_4.alerts import AlertSettings

from .store import Store

_KEY = "alert_settings"

# Only the thresholds that something here acts on. The library also has visit, reminder and
# weight settings, but those belong to notifications this server does not send.
BOUNDS: dict[str, tuple[int, int]] = {
    "drawer_level_percent": (1, 100),
    "litter_low_percent": (1, 99),
    "no_visit_hours": (1, 720),
    "deep_clean_days": (1, 3650),
    "filter_days": (1, 3650),
    "globe_clean_days": (1, 3650),
}

DEFAULTS = AlertSettings()


def as_dict(settings: AlertSettings) -> dict[str, int]:
    return {name: getattr(settings, name) for name in BOUNDS}


def defaults() -> dict[str, int]:
    return as_dict(DEFAULTS)


def _overrides(store: Store) -> dict[str, int]:
    stored = store.get_json(_KEY)
    if not isinstance(stored, dict):
        return {}
    # A value that no longer fits (hand-edited, or from a build with other limits) falls back
    # to the default instead of stopping the alerts.
    return {
        name: value
        for name, value in stored.items()
        if name in BOUNDS and type(value) is int and BOUNDS[name][0] <= value <= BOUNDS[name][1]
    }


def load(store: Store) -> AlertSettings:
    return replace(DEFAULTS, **_overrides(store))


def update(store: Store, changes: dict[str, Any]) -> AlertSettings:
    """Keep only what was set on purpose, so a default the library improves still follows."""
    store.put_json(_KEY, {**_overrides(store), **changes})
    return load(store)


def reset(store: Store) -> AlertSettings:
    store.put_json(_KEY, {})
    return load(store)

"""Sample data, so the app can be looked at before a robot is connected.

Sample data and real data must never share a database. Seeding refuses a database that has any
history, the server refuses to run a sample database without ``DEMO_MODE=1``, and it refuses
``DEMO_MODE=1`` on a database that holds real history. ``scripts/end-demo.sh`` is the way out.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from whiskerless.devices.litter_robot_4 import alerts, commands

from .cats import CatTracker
from .photos import CatPhotos, ImageTooLarge, NotAnImage, normalise
from .store import Store

DEMO_KEY = "demo_data"

MAX_DEMO_DAYS = 730
MIN_CAT_LB, MAX_CAT_LB = 2.0, 40.0

# Typical hours of a visit, as weights over the 24 hours: morning, midday and evening peaks, and
# a thin tail through the night.
_HOUR_WEIGHTS = (3, 2, 1, 1, 1, 2, 5, 9, 7, 4, 3, 4, 5, 5, 4, 3, 3, 4, 7, 9, 8, 5, 4, 3)

# A visit is followed by this long wait and a short cycle before the robot reports it finished.
CYCLE_AFTER_VISIT = timedelta(minutes=9)

# Each cycle fills the drawer a little; at this level it is emptied, as an owner would.
DRAWER_EMPTY_AT = 86.0
DRAWER_PER_CYCLE = 1.1
DRAWER_ALERT_AT = 80.0

# Seeded upkeep, as days ago, so that one job is nearly due, one is far off and one is overdue
# against the default intervals of 30, 90 and 60 days.
UPKEEP_DAYS_AGO = {
    "filter": (18, 48),
    "deep_clean": (49,),
    "globe_clean": (63,),
}


class DemoModeMismatch(RuntimeError):
    """The database and the DEMO_MODE setting disagree about whether the data is real."""


@dataclass(frozen=True, slots=True)
class CatSpec:
    name: str
    weight_lb: float
    photo: Path | None = None

    @classmethod
    def parse(cls, text: str) -> CatSpec:
        name, _, rest = text.partition(":")
        weight_text, _, photo = rest.partition(":")
        try:
            weight = float(weight_text)
        except ValueError:
            raise ValueError(f"{text!r}: use NAME:WEIGHT or NAME:WEIGHT:PHOTO_PATH") from None
        if not name.strip() or len(name.strip()) > 40:
            raise ValueError(f"{text!r}: a name is 1 to 40 characters")
        if not MIN_CAT_LB <= weight <= MAX_CAT_LB:
            raise ValueError(f"{text!r}: weight is {MIN_CAT_LB} to {MAX_CAT_LB} lb")
        return cls(name.strip(), weight, Path(photo) if photo else None)


def is_marked(store: Store) -> bool:
    return store.get_json(DEMO_KEY) is True


def ensure_mode(store: Store, demo_mode: bool) -> None:
    """Refuse to mix sample and real data, and mark a fresh database used for the demo."""
    marked = is_marked(store)
    if demo_mode and not marked:
        if not store.is_empty():
            raise DemoModeMismatch(
                "DEMO_MODE=1 is set, but this database holds real history. "
                "Remove DEMO_MODE, or point DB_PATH at an empty folder."
            )
        store.put_json(DEMO_KEY, True)
    elif marked and not demo_mode:
        raise DemoModeMismatch(
            "This database holds sample data. Run scripts/end-demo.sh to start the real robot "
            "from an empty history, or set DEMO_MODE=1 to keep looking at the sample data."
        )


class _Builder:
    def __init__(
        self, tracker: CatTracker, store: Store, tz: tzinfo, rng: random.Random, now: datetime
    ) -> None:
        self.tracker = tracker
        self.store = store
        self.tz = tz
        self.rng = rng
        self.now = now
        self.visits: list[tuple[datetime, int | None, float, int]] = []

    def local_time(self, day: date, hour: int) -> datetime:
        minute = self.rng.randrange(60)
        second = self.rng.randrange(60)
        naive = datetime(day.year, day.month, day.day, hour, minute, second)
        return naive.replace(tzinfo=self.tz).astimezone(UTC)

    def cat_visits(self, cat_id: int, base: float, first: date, last: date) -> None:
        day, index = first, 0
        while day <= last:
            # Weight wanders by a few percent over weeks, which the insights screen then shows.
            drift = 0.015 * base * _wave(index / 38)
            for _ in range(self.rng.choices((2, 3, 4, 5), weights=(2, 5, 4, 1))[0]):
                hour = self.rng.choices(range(24), weights=_HOUR_WEIGHTS)[0]
                ended = self.local_time(day, hour)
                if ended >= self.now - CYCLE_AFTER_VISIT:
                    continue
                weight = base + drift + self.rng.gauss(0, 0.1)
                duration = int(min(420, max(18, self.rng.gauss(75, 35))))
                self.visits.append((ended, cat_id, round(weight, 2), duration))
            day += timedelta(days=1)
            index += 1


def _wave(x: float) -> float:
    return math.sin(x * math.tau)


def seed(
    store: Store,
    photos: CatPhotos,
    specs: list[CatSpec],
    *,
    days: int,
    timezone: tzinfo = UTC,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> dict[str, int]:
    """Fill an empty database with ``days`` of believable history for the given cats."""
    if not specs:
        raise ValueError("give at least one cat")
    if not 1 <= days <= MAX_DEMO_DAYS:
        raise ValueError(f"days is 1 to {MAX_DEMO_DAYS}")
    if len({spec.name.casefold() for spec in specs}) != len(specs):
        raise ValueError("cat names must be different")
    if not store.is_empty() or is_marked(store):
        raise DemoModeMismatch("This database already holds history. Sample data needs a new one.")
    pictures = [_load_picture(spec.photo) for spec in specs]

    now = now or datetime.now(UTC)
    rng = rng or random.Random(1)  # noqa: S311 - sample data, not secrets
    store.put_json(DEMO_KEY, True)
    tracker = CatTracker(store, timezone, clock=lambda: now)
    builder = _Builder(tracker, store, timezone, rng, now)
    today = now.astimezone(timezone).date()
    first = today - timedelta(days=days)

    cat_ids = []
    for spec, picture in zip(specs, pictures, strict=True):
        cat = tracker.create_cat(spec.name, spec.weight_lb)
        cat_ids.append(cat["id"])
        if picture is not None:
            photos.save(cat["id"], picture)
        builder.cat_visits(cat["id"], spec.weight_lb, first, today)

    builder.visits.extend(_unclear_visits(builder, specs, today))
    builder.visits.sort(key=lambda visit: visit[0])
    with store.transaction():
        visit_ids = _write_history(builder, store)
        _write_upkeep(store, today, now)
    tracker.relabel(since=now - timedelta(days=days + 1))
    if visit_ids.not_cat is not None:
        tracker.label_visit(visit_ids.not_cat, "not_cat", None)
    return {
        "cats": len(specs),
        "visits": len(builder.visits),
        "needing_a_cat": tracker.needing_cat(),
    }


def _load_picture(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        return normalise(path.read_bytes())
    except OSError as err:
        raise NotAnImage(f"cannot read {path}: {err.strerror}") from err
    except (NotAnImage, ImageTooLarge) as err:
        raise NotAnImage(f"{path} is not a usable image") from err


def _unclear_visits(
    builder: _Builder, specs: list[CatSpec], today: date
) -> list[tuple[datetime, int | None, float, int]]:
    """A few recent visits far from every cat's weight, which the server rightly leaves open."""
    heaviest = max(spec.weight_lb for spec in specs)
    lightest = min(spec.weight_lb for spec in specs)
    # Past the matching tolerance of every cat, whichever side it falls on.
    odd = [heaviest + 1.9, max(2.5, lightest - 2.1), heaviest + 1.6]
    out = []
    for weight, ago in zip(odd, (1, 3, 5), strict=True):
        day = today - timedelta(days=ago)
        ended = builder.local_time(day, builder.rng.choice((9, 15, 22)))
        if ended < builder.now - CYCLE_AFTER_VISIT:
            out.append((ended, None, round(weight, 2), builder.rng.randrange(25, 70)))
    return out


@dataclass(slots=True)
class _Written:
    not_cat: int | None = None


def _write_history(builder: _Builder, store: Store) -> _Written:
    written = _Written()
    cycles = 1000
    drawer = 20.0
    litter_low_done = False
    litter_low_at = 2 * len(builder.visits) // 3
    for position, (ended, _cat, weight, duration) in enumerate(builder.visits):
        event_id = store.add_event(
            ended,
            "visit",
            {
                "started": (ended - timedelta(seconds=duration)).isoformat(timespec="milliseconds"),
                "duration_s": duration,
                "weight_lb": weight,
            },
        )
        if written.not_cat is None and _cat is None:
            written.not_cat = event_id
        cycles += 1
        drawer += DRAWER_PER_CYCLE + builder.rng.uniform(-0.3, 0.3)
        finished = ended + CYCLE_AFTER_VISIT
        if finished >= builder.now:
            continue
        store.add_event(finished, "clean_cycle", {"count": cycles, "drawer_percent": round(drawer)})
        if drawer >= DRAWER_ALERT_AT and drawer - DRAWER_PER_CYCLE < DRAWER_ALERT_AT:
            _alert(store, finished, alerts.Alert.DRAWER_LEVEL, True, {"level": round(drawer)})
        if drawer >= DRAWER_EMPTY_AT:
            emptied = finished + timedelta(hours=builder.rng.randrange(2, 9))
            if emptied < builder.now:
                store.add_event(emptied, "drawer_moved", {})
                _alert(store, emptied, alerts.Alert.DRAWER_LEVEL, False, {})
            drawer = 3.0
        if not litter_low_done and position > litter_low_at:
            litter_low_done = True
            _alert(store, finished, alerts.Alert.LITTER_LOW, True, {"level": 28})
            _alert(store, finished + timedelta(hours=5), alerts.Alert.LITTER_LOW, False, {})
    _write_odd_events(builder, store)
    return written


def _write_odd_events(builder: _Builder, store: Store) -> None:
    span = (builder.now - builder.visits[0][0]).total_seconds() if builder.visits else 0
    if span < 3 * 86400:
        return
    went_offline = builder.visits[0][0] + timedelta(seconds=span * 0.45)
    _alert(store, went_offline, alerts.Alert.OFFLINE, True, {})
    _alert(store, went_offline + timedelta(minutes=40), alerts.Alert.OFFLINE, False, {})
    store.add_event(
        went_offline + timedelta(hours=2), "command", {"label": commands.clean_cycle().label}
    )


def _alert(
    store: Store, at: datetime, alert: alerts.Alert, raised: bool, details: dict[str, Any]
) -> None:
    store.add_event(
        at,
        "alert",
        {
            "key": alert.value,
            "raised": raised,
            "urgent": alert in alerts.URGENT,
            "message": alerts.describe(alert, raised, details),
        },
    )


def _write_upkeep(store: Store, today: date, now: datetime) -> None:
    for kind, ago in UPKEEP_DAYS_AGO.items():
        for days_ago in ago:
            store.add_maintenance(kind, today - timedelta(days=days_ago), now)


def _database_from_env() -> tuple[Path, tzinfo]:
    return (
        Path(os.environ.get("DB_PATH", "/data/whisker-local.db")),
        ZoneInfo(os.environ.get("TZ") or "UTC"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m whisker_local.demo", description=__doc__)
    commands_ = parser.add_subparsers(dest="command", required=True)
    seeding = commands_.add_parser("seed", help="fill an empty database with sample history")
    seeding.add_argument(
        "--cat",
        action="append",
        required=True,
        metavar="NAME:WEIGHT[:PHOTO_PATH]",
        help="a cat to invent history for; repeat for more cats",
    )
    seeding.add_argument("--days", type=int, default=120)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path, timezone = _database_from_env()
    try:
        specs = [CatSpec.parse(text) for text in args.cat]
        store = Store(db_path)
        try:
            summary = seed(
                store,
                CatPhotos(db_path.parent / "cat-photos"),
                specs,
                days=args.days,
                timezone=timezone,
            )
        finally:
            store.close()
    except (ValueError, DemoModeMismatch, NotAnImage) as err:
        print(f"demo seed: {err}", file=sys.stderr)
        return 1
    print(
        f"Seeded {summary['cats']} cats and {summary['visits']} visits, "
        f"{summary['needing_a_cat']} waiting for a cat. Start the server with DEMO_MODE=1."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

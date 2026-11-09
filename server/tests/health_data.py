"""Invented households for the health checks: nothing here comes from a real cat."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from whisker_local.cats import CatTracker
from whisker_local.health import HealthService
from whisker_local.identity import Anchor
from whisker_local.store import Store

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)

# Evenings and mornings, the way a household of cats uses a box.
HOURS = [0.2, 0.2, 0.2, 0.2, 0.3, 0.6, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1.5, 2, 2, 2, 1, 0.5, 0.3]


@dataclass
class CatPlan:
    name: str
    weight: float
    sex: str = "unknown"
    per_day: tuple[int, ...] = (1, 2, 2, 3)
    # (first day index, weight multiplier from then on)
    step: tuple[int, float] | None = None
    # (first day index, how many times as many visits from then on)
    busier: tuple[int, int] | None = None


@dataclass
class World:
    store: Store
    tracker: CatTracker
    service: HealthService
    cat_ids: dict[str, int]
    start: date
    visit_ids: list[int] = field(default_factory=list)

    def day(self, index: int) -> date:
        return self.start + timedelta(days=index)


def build(
    tmp_path: Path,
    plans: list[CatPlan],
    *,
    days: int = 240,
    seed: int = 7,
    offline: tuple[int, int] | None = None,
    quiet: tuple[datetime, datetime] | None = None,
    clock: Callable[[], datetime] | None = None,
    store: Store | None = None,
    end: datetime = NOW,
) -> World:
    rng = random.Random(seed)
    store = store or Store(tmp_path / "h.db")
    now = clock or (lambda: end)
    tracker = CatTracker(store, clock=now)
    start = (end - timedelta(days=days)).date()
    store.add_presence(datetime.combine(start, datetime.min.time(), tzinfo=UTC), True)
    if offline is not None:
        first, last = offline
        store.add_presence(
            datetime.combine(start + timedelta(days=first), datetime.min.time(), tzinfo=UTC), False
        )
        store.add_presence(
            datetime.combine(start + timedelta(days=last + 1), datetime.min.time(), tzinfo=UTC),
            True,
        )
    cat_ids = {}
    for plan in plans:
        cat = tracker.create_cat(plan.name, plan.weight, sex=plan.sex)
        cat_ids[plan.name] = cat["id"]
        store.put_anchor(
            cat["id"],
            Anchor(plan.weight, 0.3, 12, sum(plan.per_day) / len(plan.per_day), NOW, "auto"),
        )
    world = World(store, tracker, HealthService(store, tracker, clock=now), cat_ids, start)
    with store.transaction():
        for index in range(days):
            if offline is not None and offline[0] <= index <= offline[1]:
                continue
            for plan in plans:
                count = rng.choice(plan.per_day)
                if plan.busier is not None and index >= plan.busier[0]:
                    count *= plan.busier[1]
                weight = plan.weight * (plan.step[1] if plan.step and index >= plan.step[0] else 1)
                for _ in range(count):
                    hour = rng.choices(range(24), weights=HOURS)[0]
                    at = datetime.combine(
                        world.day(index), datetime.min.time(), tzinfo=UTC
                    ) + timedelta(hours=hour, minutes=rng.randrange(60), seconds=rng.randrange(60))
                    if at >= end or (quiet and quiet[0] <= at < quiet[1]):
                        continue
                    add_visit(
                        world, at, plan.name, rng.gauss(weight, 0.3), int(rng.uniform(40, 120))
                    )
    return world


def add_visit(
    world: World, at: datetime, cat: str | None, weight: float, duration_s: int | None
) -> int:
    detail: dict = {"weight_lb": round(weight, 2)}
    if duration_s is not None:
        detail["duration_s"] = duration_s
        detail["started"] = (at - timedelta(seconds=duration_s)).isoformat()
    event_id = world.store.add_event(at, "visit", detail)
    if cat is not None:
        world.store.set_user_label(event_id, "cat", world.cat_ids[cat])
    world.visit_ids.append(event_id)
    return event_id

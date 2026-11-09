"""Which cat a weight belongs to, and how sure we are.

Two things are kept apart on purpose. A slow tracker follows each cat's usual weight so that
labels keep working as a cat changes, but it moves slowly when a cat drifts toward another one.
A frozen anchor per cat, taken from its first readings, is what health trends are measured
against, so a cat that loses weight cannot drag its own baseline down with it.

Each reading gets a probability for every cat from a mixture of bell curves plus a small
allowance for readings that fit nobody. A cat is named only when it is at least 90 percent
likely. Anything less is left as not sure, and the household count keeps the visit.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

Status = Literal["confident", "user", "unsure", "off_cluster", "unsettled", "no_weight"]

# A cat is named only when it is at least this likely. Below it the visit stays unassigned.
CONFIDENCE = 0.9

# Visit to visit scatter for one cat is 0.3 to 0.4 lb on this scale. A new cat starts from the
# owner's estimate with a wide curve and tightens as it learns.
INITIAL_SD_LB = 0.45
MIN_SD_LB = 0.3
MAX_SD_LB = 0.6
SD_LEARNING_RATE = 0.05

# A reading further than this many curves from the cat it is nearest to fits nobody. 2.5 keeps
# nearly all of a cat's own readings and leaves out the odd ones: a paw, a litter bag, two cats.
GATE_SD = 2.5

# Readings that fit no cat: a uniform allowance over the weights a box could ever see.
OUTLIER_SHARE = 0.03
OUTLIER_RANGE_LB = (2.0, 25.0)

# The first readings of a cat move its tracker freely; after this many it moves slowly.
LEARNING_VISITS = 10
TRACKER_RATE = 0.03

# Toward another cat the tracker may move this share of the cat's weight per week. Away from
# every other cat, or with only one cat, it follows as fast as its learning rate allows.
DRIFT_PER_WEEK = 0.005
MAX_DRIFT_DAYS = 14.0

# A hand label is the one real ground truth, so it pulls harder than an automatic one.
USER_RATE = 0.1

# The anchor is the median of a cat's first trusted readings, taken once there are enough of
# them over enough days, and only changed when the owner says so.
ANCHOR_VISITS = 12
ANCHOR_MIN_DAYS = 5.0
MIN_SPREAD_LB = 0.25

# A cat with an anchor that has not been seen for this many of its usual visits is silent. Its
# weight may have dropped into another cat's range, so that cat's visits in that range are not
# trusted until the silent one is seen again. 5 expected visits missing is a 1 in 150 chance.
SILENT_EXPECTED_VISITS = 5.0
MIN_SILENT_DAYS = 2.5

# How far below its anchor a silent cat could plausibly have fallen.
PLAUSIBLE_LOSS = 0.7
PLAUSIBLE_ABOVE_SD = 2.0


@dataclass(frozen=True, slots=True)
class Seed:
    cat_id: int
    reference_lb: float | None


@dataclass(frozen=True, slots=True)
class Reading:
    visit_id: int
    at: datetime
    weight: float | None
    # ("cat", id) or ("not_cat", None) when the owner has said so.
    user: tuple[str, int | None] | None = None


@dataclass(frozen=True, slots=True)
class Anchor:
    weight_lb: float
    spread_lb: float
    visits: int
    visits_per_day: float | None
    set_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class Verdict:
    status: Status
    # The cat the visit is named for; None unless confident or labelled by the owner.
    cat_id: int | None
    # The most likely cat, even when not sure enough to name it.
    candidate_id: int | None
    confidence: float | None


@dataclass(slots=True)
class Tracker:
    cat_id: int
    mean: float
    sd: float = INITIAL_SD_LB
    visits: int = 0
    moved_at: datetime | None = None
    first_readings: list[tuple[datetime, float]] = field(default_factory=list)

    def learn(self, at: datetime, weight: float, others: Iterable[float], *, user: bool) -> None:
        if self.visits < LEARNING_VISITS and not user:
            rate = 1 / (self.visits + 2)
            step = rate * (weight - self.mean)
        else:
            rate = USER_RATE if user else TRACKER_RATE
            step = rate * (weight - self.mean)
            step = self._limited(step, at, list(others))
        self.sd = min(
            MAX_SD_LB,
            max(
                MIN_SD_LB,
                math.sqrt(
                    (1 - SD_LEARNING_RATE) * self.sd**2
                    + SD_LEARNING_RATE * (weight - self.mean) ** 2
                ),
            ),
        )
        self.mean += step
        self.visits += 1
        self.moved_at = at

    def _limited(self, step: float, at: datetime, others: list[float]) -> float:
        closer = any(abs(self.mean + step - other) < abs(self.mean - other) for other in others)
        if not closer or self.moved_at is None:
            return step
        days = min(MAX_DRIFT_DAYS, max(0.0, (at - self.moved_at).total_seconds() / 86400))
        budget = DRIFT_PER_WEEK / 7 * self.mean * days
        return max(-budget, min(budget, step))


def _density(weight: float, mean: float, sd: float) -> float:
    z = (weight - mean) / sd
    return math.exp(-0.5 * z * z) / (sd * math.sqrt(2 * math.pi))


def posteriors(
    weight: float, trackers: Sequence[Tracker], counts: dict[int, int]
) -> tuple[dict[int, float], float]:
    """Probability of each cat, and of the reading fitting none of them."""
    low, high = OUTLIER_RANGE_LB
    total_visits = sum(counts.values()) + len(trackers)
    weights: dict[int, float] = {}
    for tracker in trackers:
        share = (counts.get(tracker.cat_id, 0) + 1) / total_visits
        weights[tracker.cat_id] = (
            (1 - OUTLIER_SHARE) * share * _density(weight, tracker.mean, tracker.sd)
        )
    outlier = OUTLIER_SHARE / (high - low)
    norm = sum(weights.values()) + outlier
    return {cat_id: value / norm for cat_id, value in weights.items()}, outlier / norm


def decide(weight: float, trackers: Sequence[Tracker], counts: dict[int, int]) -> Verdict:
    if not trackers:
        return Verdict("unsure", None, None, None)
    chances, outlier = posteriors(weight, trackers, counts)
    best = max(chances, key=lambda cat_id: chances[cat_id])
    chosen = next(t for t in trackers if t.cat_id == best)
    far = abs(weight - chosen.mean) > GATE_SD * chosen.sd
    if far or outlier >= chances[best]:
        return Verdict("off_cluster", None, best, round(chances[best], 3))
    if chances[best] >= CONFIDENCE:
        return Verdict("confident", best, best, round(chances[best], 3))
    return Verdict("unsure", None, best, round(chances[best], 3))


@dataclass(slots=True)
class Silence:
    """A stretch in which a cat with an anchor was not seen when it should have been."""

    cat_id: int
    since: datetime
    until: datetime | None


@dataclass(slots=True)
class Result:
    verdicts: dict[int, Verdict]
    trackers: dict[int, Tracker]
    anchors: dict[int, Anchor]
    new_anchors: dict[int, Anchor]
    silences: list[Silence]


def make_anchor(
    readings: Sequence[tuple[datetime, float]], now: datetime, rate: float | None, source: str
) -> Anchor:
    weights = [w for _, w in readings]
    centre = statistics.median(weights)
    spread = 1.4826 * statistics.median(abs(w - centre) for w in weights)
    return Anchor(
        round(centre, 2), round(max(spread, MIN_SPREAD_LB), 2), len(weights), rate, now, source
    )


def elapsed_days(first: datetime, last: datetime) -> float:
    return (last - first).total_seconds() / 86400


def assign(
    readings: Iterable[Reading],
    seeds: Sequence[Seed],
    anchors: dict[int, Anchor],
    *,
    heard_days: Callable[[datetime, datetime], float] = elapsed_days,
    trusted: Callable[[datetime], bool] = lambda _: True,
) -> Result:
    """Walk the readings in time order, as if each had just arrived."""
    trackers = {
        seed.cat_id: Tracker(seed.cat_id, seed.reference_lb)
        for seed in seeds
        if seed.reference_lb is not None
    }
    counts: dict[int, int] = defaultdict(int)
    verdicts: dict[int, Verdict] = {}
    confident_at: dict[int, list[datetime]] = defaultdict(list)
    held = dict(anchors)
    new_anchors: dict[int, Anchor] = {}
    ordered = sorted(readings, key=lambda r: (r.at, r.visit_id))

    for reading in ordered:
        if reading.user is not None:
            label, cat_id = reading.user
            if label == "cat" and cat_id is not None:
                verdicts[reading.visit_id] = Verdict("user", cat_id, cat_id, 1.0)
                if reading.weight is not None:
                    _learn(trackers, cat_id, reading, counts, held, new_anchors, trusted, user=True)
                    confident_at[cat_id].append(reading.at)
            continue
        if reading.weight is None:
            verdicts[reading.visit_id] = Verdict("no_weight", None, None, None)
            continue
        verdict = decide(reading.weight, list(trackers.values()), counts)
        verdicts[reading.visit_id] = verdict
        if verdict.status == "confident" and verdict.cat_id is not None:
            _learn(
                trackers, verdict.cat_id, reading, counts, held, new_anchors, trusted, user=False
            )
            confident_at[verdict.cat_id].append(reading.at)

    silences = _withdraw_near_silent_cats(ordered, verdicts, held, confident_at, heard_days)
    return Result(verdicts, trackers, held, new_anchors, silences)


def _learn(
    trackers: dict[int, Tracker],
    cat_id: int,
    reading: Reading,
    counts: dict[int, int],
    anchors: dict[int, Anchor],
    new_anchors: dict[int, Anchor],
    trusted: Callable[[datetime], bool],
    *,
    user: bool,
) -> None:
    assert reading.weight is not None  # noqa: S101
    if not trusted(reading.at):
        return
    tracker = trackers.get(cat_id)
    if tracker is None:
        tracker = trackers[cat_id] = Tracker(cat_id, reading.weight)
    others = [t.mean for other, t in trackers.items() if other != cat_id]
    tracker.learn(reading.at, reading.weight, others, user=user)
    counts[cat_id] += 1
    if cat_id in anchors:
        return
    tracker.first_readings.append((reading.at, reading.weight))
    taken = tracker.first_readings
    span = (taken[-1][0] - taken[0][0]).total_seconds() / 86400
    if len(taken) >= ANCHOR_VISITS and span >= ANCHOR_MIN_DAYS:
        rate = len(taken) / span if span else None
        anchor = make_anchor(taken[:ANCHOR_VISITS], reading.at, rate, "first readings")
        anchors[cat_id] = new_anchors[cat_id] = anchor


def _withdraw_near_silent_cats(
    ordered: list[Reading],
    verdicts: dict[int, Verdict],
    anchors: dict[int, Anchor],
    confident_at: dict[int, list[datetime]],
    heard_days: Callable[[datetime, datetime], float],
) -> list[Silence]:
    """Take back other cats' names where a silent cat could have been the one inside.

    A cat that has lost weight can land in the range of a lighter cat, and by weight alone
    nothing separates them. What does show is that the heavier cat stopped being seen. Until it
    is seen again, visits in the range it could have fallen to are marked not settled.
    """
    silences: list[Silence] = []
    if not ordered:
        return silences
    end = ordered[-1].at
    for cat_id, anchor in anchors.items():
        if not anchor.visits_per_day:
            continue
        needed = max(MIN_SILENT_DAYS, SILENT_EXPECTED_VISITS / anchor.visits_per_day)
        seen = sorted(confident_at.get(cat_id, []))
        stops = [anchor.set_at, *[t for t in seen if t > anchor.set_at]]
        for before, after in zip(stops, [*stops[1:], None], strict=True):
            until = after or end
            if heard_days(before, until) < needed:
                continue
            silences.append(Silence(cat_id, before, after))
            low = PLAUSIBLE_LOSS * anchor.weight_lb
            high = anchor.weight_lb + PLAUSIBLE_ABOVE_SD * anchor.spread_lb
            for reading in ordered:
                verdict = verdicts.get(reading.visit_id)
                if (
                    verdict is not None
                    and verdict.status == "confident"
                    and verdict.cat_id != cat_id
                    and before < reading.at <= until
                    and reading.weight is not None
                    and low <= reading.weight <= high
                ):
                    verdicts[reading.visit_id] = replace(verdict, status="unsettled", cat_id=None)
    return silences


def silence_summary(silences: Sequence[Silence], now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "cat_id": s.cat_id,
            "since": s.since.isoformat(),
            "until": None if s.until is None else s.until.isoformat(),
            "open": s.until is None,
            "days": round(((s.until or now) - s.since) / timedelta(days=1), 1),
        }
        for s in silences
    ]

"""The irregularity checks, each a pure function of the history and a day or an instant.

Every check is built to stay silent when it cannot be sure: it needs a minimum of history, it
leaves out days that were not heard or were marked unreliable, and a change must hold for three
days in a row before it counts. Parameters follow the audit in the design notes; the reason for
each is in the comment above it.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import stats
from .health_history import DaySeries, History, Visit

# A change must hold on this many days in a row. A single day fires on 16 percent of cat-years
# of pure noise; three in a row on 4 to 6 percent.
PERSIST_DAYS = 3

# Visit rate: household total over a week and per cat over two, against the 28 days before.
HOUSEHOLD_WINDOW = 7
CAT_WINDOW = 14
BASELINE_DAYS = 28
HOUSEHOLD_MIN_HISTORY = 21
CAT_MIN_HISTORY = 35
# A true doubling measured over a week lands anywhere from 1.7 to 2.3 times, so asking for
# exactly two would miss half of them. The p value and the sums below do the real filtering.
RATE_RATIO = 1.75
RATE_P = 0.01
CUSUM_H = 4.0
# The square root of a count has a standard deviation near a half whatever the rate.
MIN_COUNT_SD = 0.5
# Too few counted days in the window to speak for it.
MIN_WINDOW_SHARE = 0.7

# Weight: the median of two weeks against the cat's frozen anchor.
WEIGHT_WINDOW = 14
WEIGHT_MIN_SHIFT = 0.04
WEIGHT_MIN_Z = 3.0
WEIGHT_MIN_READINGS = 8
# A reading is off by 3 to 4 percent on its own, so the spread is never taken as smaller than 2.
WEIGHT_MIN_SD_SHARE = 0.02
# The standard error of a median is about 1.25 times that of a mean.
MEDIAN_SE_FACTOR = 1.2533
TREND_MIN_DAYS = 21
TREND_MIN_READINGS = 15
CHANGE_MIN_DAYS = 42
SLOPE_DAYS = 90
SLOPE_MIN_DAYS_WITH_DATA = 30
SLOPE_MIN_MONTHLY_SHARE = 0.005
# Run every day on overlapping windows, a 95 percent interval would leave a false alarm in most
# years, so the slow tier asks for 99 percent.
SLOPE_Z = 2.576
# The interval must also have excluded zero this many days earlier, with the same sign.
SLOPE_HELD_AT = (7, 14)
# A larger move than these over a month or a quarter gets stronger wording.
LARGER_MONTH_SHARE = 0.05
LARGER_QUARTER_SHARE = 0.10

# Silence: no visit for longer than the household's own longest quiet stretch, robot online.
SILENCE_MIN_DAYS = 14
SILENCE_FLOOR = timedelta(hours=24)
SILENCE_FACTOR = 1.5
ONLINE_THROUGHOUT = 0.99

# Burst: the one pattern that cannot wait, a cat going in and out for short visits.
BURST_VISITS = 4
BURST_WINDOW = timedelta(hours=3)
BURST_MAX_PER_DAY = 1.3
BURST_MIN_TIMED = 10
BURST_BASELINE_DAYS = 14

# Rhythm: eight blocks of three hours, four weeks against the eight before.
RHYTHM_MIN_DAYS = 56
RHYTHM_RECENT = 28
RHYTHM_BASELINE = 56
BLOCK_HOURS = 3
BLOCKS = 24 // BLOCK_HOURS
RHYTHM_P = RATE_P / BLOCKS
RHYTHM_MIN_COUNTED = (14, 28)
# A block with fewer visits than this, now or expected, is too thin to say it moved.
RHYTHM_MIN_BLOCK_VISITS = 8


@dataclass(frozen=True, slots=True)
class RateState:
    ready: bool
    need_days: int = 0
    observed: int = 0
    days: int = 0
    per_day: float = 0.0
    usual_per_day: float = 0.0
    ratio: float | None = None
    p: float = 1.0
    direction: str | None = None
    onset: int | None = None
    met: bool = False


def _anscombe_days(series: DaySeries, low: int, high: int) -> list[tuple[int, float]]:
    return [
        (i, stats.anscombe(series.counts[i]))
        for i in range(max(low, 0), min(high, len(series.counts) - 1) + 1)
        if series.counted[i]
    ]


def rate_state(
    series: DaySeries, i: int, window: int, min_history: int, begin: int | None
) -> RateState:
    """One day's verdict on whether the visit rate left the usual range."""
    if begin is None or i < begin:
        return RateState(False, min_history)
    low = i - window + 1
    history = series.window(begin, i)[1]
    observed, days = series.window(low, i)
    base_count, base_days = series.window(max(begin, low - BASELINE_DAYS), low - 1)
    need_base = min_history - window
    if (
        history < min_history
        or base_days < need_base
        or days < math.ceil(MIN_WINDOW_SHARE * window)
    ):
        return RateState(False, max(min_history - history, need_base - base_days, 0))
    usual = base_count / base_days
    per_day = observed / days
    if usual > 0:
        ratio: float | None = per_day / usual
    else:
        ratio = math.inf if observed else 1.0
    p = stats.gamma_poisson_p(observed, days, base_count, base_days)
    direction = None
    if ratio is not None and ratio >= RATE_RATIO:
        direction = "up"
    elif ratio is not None and ratio <= 1 / RATE_RATIO:
        direction = "down"
    onset = None
    met = False
    if direction is not None and p < RATE_P:
        base = [v for _, v in _anscombe_days(series, low - BASELINE_DAYS, low - 1)]
        recent = _anscombe_days(series, low, i)
        spread = max(stats.robust_sd(base), MIN_COUNT_SD)
        sums = stats.cusum([v for _, v in recent], statistics.fmean(base), spread)
        crossed = sums.up if direction == "up" else sums.down
        start = sums.up_from if direction == "up" else sums.down_from
        if crossed >= CUSUM_H:
            met = True
            onset = recent[start][0]
    return RateState(True, 0, observed, days, per_day, usual, ratio, p, direction, onset, met)


@dataclass(frozen=True, slots=True)
class Persisted:
    state: RateState
    since: int


def persistent_rate(
    series: DaySeries, i: int, window: int, min_history: int, begin: int | None
) -> Persisted | None:
    """The rate change as of day ``i`` if it has held for three days in a row, else None."""
    today = rate_state(series, i, window, min_history, begin)
    if not today.met or not all(series.counted[max(i - n, 0)] for n in range(PERSIST_DAYS)):
        return None
    for n in range(1, PERSIST_DAYS):
        earlier = rate_state(series, i - n, window, min_history, begin)
        if not earlier.met or earlier.direction != today.direction:
            return None
    return Persisted(today, today.onset if today.onset is not None else i)


@dataclass(frozen=True, slots=True)
class WeightState:
    stage: str
    need_days: int = 0
    median: float | None = None
    readings: int = 0
    reference: float | None = None
    pct: float | None = None
    pct_low: float | None = None
    pct_high: float | None = None
    se: float | None = None
    p: float = 1.0
    noise_pct: float | None = None
    met: bool = False


def _normal_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def weight_state(history: History, cat_id: int, i: int) -> WeightState:
    """Where a cat's weight stands on day ``i``: nothing yet, a trend line, or a change."""
    readings = history.readings.get(cat_id)
    if readings is None or not readings.days or i < readings.days[0]:
        return WeightState("none", TREND_MIN_DAYS)
    since_first = i - readings.days[0]
    total = readings.count_until(i)
    if since_first < TREND_MIN_DAYS or total < TREND_MIN_READINGS:
        return WeightState("none", max(TREND_MIN_DAYS - since_first, 1))
    recent = readings.between(i - WEIGHT_WINDOW + 1, i)
    if len(recent) < 3:
        return WeightState("none", 0)
    centre = statistics.median(recent)
    noise = stats.robust_sd(recent, WEIGHT_MIN_SD_SHARE * centre)
    anchor = history.anchors.get(cat_id)
    if since_first < CHANGE_MIN_DAYS or anchor is None or len(recent) < WEIGHT_MIN_READINGS:
        need = max(CHANGE_MIN_DAYS - since_first, 0)
        return WeightState(
            "trend", need, centre, len(recent), None, noise_pct=round(noise / centre * 100, 1)
        )
    se = MEDIAN_SE_FACTOR * noise / math.sqrt(len(recent))
    reference_se = anchor.spread_lb / math.sqrt(max(anchor.visits, 1)) if anchor.visits else 0.0
    se = math.hypot(se, reference_se)
    diff = centre - anchor.weight_lb
    pct = diff / anchor.weight_lb * 100
    p = _normal_p(diff / se)
    met = (
        abs(diff) / anchor.weight_lb >= WEIGHT_MIN_SHIFT
        and abs(diff) >= WEIGHT_MIN_Z * se
        and len(recent) >= WEIGHT_MIN_READINGS
    )
    return WeightState(
        "change",
        0,
        centre,
        len(recent),
        anchor.weight_lb,
        pct,
        (diff - stats.Z95 * se) / anchor.weight_lb * 100,
        (diff + stats.Z95 * se) / anchor.weight_lb * 100,
        se,
        p,
        round(noise / centre * 100, 1),
        met,
    )


def persistent_weight(history: History, cat_id: int, i: int) -> tuple[WeightState, int] | None:
    """The weight change as of day ``i`` if it has held for three days, with the day it began."""
    today = weight_state(history, cat_id, i)
    if not today.met:
        return None
    sign = math.copysign(1, today.pct or 0)
    for n in range(1, PERSIST_DAYS):
        earlier = weight_state(history, cat_id, i - n)
        if not earlier.met or math.copysign(1, earlier.pct or 0) != sign:
            return None
    start = i - PERSIST_DAYS + 1
    while start > 0:
        earlier = weight_state(history, cat_id, start - 1)
        if not earlier.met or math.copysign(1, earlier.pct or 0) != sign:
            break
        start -= 1
    return today, start


def longer_change(history: History, cat_id: int, i: int, days: int) -> float | None:
    """Share by which the median of the last ``days`` days differs from the anchor."""
    anchor = history.anchors.get(cat_id)
    readings = history.readings.get(cat_id)
    if anchor is None or readings is None:
        return None
    found = readings.between(i - days + 1, i)
    if len(found) < WEIGHT_MIN_READINGS:
        return None
    return (statistics.median(found) - anchor.weight_lb) / anchor.weight_lb


@dataclass(frozen=True, slots=True)
class SlopeState:
    per_month_pct: float
    low_pct: float
    high_pct: float
    days_with_data: int


def slope_state(history: History, cat_id: int, i: int) -> SlopeState | None:
    """The slow drift over 90 days, only when its interval leaves out zero."""
    readings = history.readings.get(cat_id)
    if readings is None or not readings.days or i - readings.days[0] < SLOPE_DAYS:
        return None
    by_day: dict[int, list[float]] = {}
    low = i - SLOPE_DAYS + 1
    start = bisect.bisect_left(readings.days, low)
    stop = bisect.bisect_right(readings.days, i)
    for day, weight in zip(readings.days[start:stop], readings.weights[start:stop], strict=True):
        by_day.setdefault(day, []).append(weight)  # type: ignore[arg-type]
    if len(by_day) < SLOPE_MIN_DAYS_WITH_DATA:
        return None
    points = [(day, statistics.median(found)) for day, found in sorted(by_day.items())]
    fit = stats.theil_sen(points, SLOPE_Z)
    if fit is None:
        return None
    slope, lower, upper = fit
    level = statistics.median(w for _, w in points)
    per_month = slope * 30 / level
    if lower <= 0 <= upper or abs(per_month) < SLOPE_MIN_MONTHLY_SHARE:
        return None
    return SlopeState(
        round(per_month * 100, 2),
        round(lower * 30 / level * 100, 2),
        round(upper * 30 / level * 100, 2),
        len(by_day),
    )


@dataclass(frozen=True, slots=True)
class SilenceState:
    ready: bool
    fired: bool = False
    quiet_for: timedelta | None = None
    usual_longest: timedelta | None = None
    last: datetime | None = None


class SilenceWatch:
    """Household quiet stretches, with the longest so far at every visit, for any instant."""

    def __init__(self, history: History, heard: Callable[[datetime, datetime], float]) -> None:
        self._history = history
        self._heard = heard
        self.times = [v.at for v in history.visits]
        self._longest: list[timedelta] = []
        longest = timedelta(0)
        for before, after in zip(self.times, self.times[1:], strict=False):
            if heard(before, after) >= ONLINE_THROUGHOUT:
                longest = max(longest, after - before)
            self._longest.append(longest)

    def at(self, now: datetime) -> SilenceState:
        index = bisect.bisect_right(self.times, now) - 1
        if index < 0:
            return SilenceState(False)
        last = self.times[index]
        history = self._history
        first = history.visits[0].at
        counted = history.household.window(0, history.index(now.astimezone(history.tz).date()))[1]
        if (now - first) < timedelta(days=SILENCE_MIN_DAYS) or counted < SILENCE_MIN_DAYS:
            return SilenceState(False)
        usual = self._longest[index - 1] if index >= 1 else timedelta(0)
        threshold = max(SILENCE_FLOOR, SILENCE_FACTOR * usual)
        quiet = now - last
        online = self._heard(last, now) >= ONLINE_THROUGHOUT
        return SilenceState(True, online and quiet > threshold, quiet, usual, last)


@dataclass(frozen=True, slots=True)
class Burst:
    cat_id: int
    visits: int
    first: datetime
    last: datetime
    median_s: float
    usual_short_s: float


def find_bursts(history: History, cat_id: int) -> list[Burst]:
    """Windows of three hours with four or more short visits by one cat, from live durations."""
    visits = history.by_cat.get(cat_id, [])
    found: list[Burst] = []
    lead = 0
    last_end: datetime | None = None
    prior: list[int] = []
    for end, visit in enumerate(visits):
        while visits[lead].at <= visit.at - BURST_WINDOW:
            if visits[lead].duration_s is not None:
                bisect.insort(prior, visits[lead].duration_s)
            lead += 1
        group = visits[lead : end + 1]
        if len(group) < BURST_VISITS or (last_end is not None and visit.at <= last_end):
            continue
        durations = [v.duration_s for v in group]
        if any(d is None for d in durations) or len(prior) < BURST_MIN_TIMED:
            continue
        short = stats.quantile(prior, 0.25)
        median = statistics.median(durations)  # type: ignore[type-var]
        if median >= short or not _baseline_allows(history, cat_id, group[0]):
            continue
        found.append(Burst(cat_id, len(group), group[0].at, group[-1].at, median, short))
        last_end = group[-1].at + BURST_WINDOW
    return found


def _baseline_allows(history: History, cat_id: int, first: Visit) -> bool:
    """A cat that visits often anyway is not in a burst; unknown habits do not excuse one."""
    series = history.cat_series.get(cat_id)
    if series is None:
        return True
    count, days = series.window(first.day - BURST_BASELINE_DAYS, first.day - 1)
    return days < BURST_BASELINE_DAYS // 2 or count / days < BURST_MAX_PER_DAY


@dataclass(frozen=True, slots=True)
class RhythmShift:
    block: int
    direction: str
    recent_share: float
    usual_share: float
    p: float


@dataclass(frozen=True, slots=True)
class Rhythm:
    ready: bool
    need_days: int
    recent_visits: int = 0
    baseline_visits: int = 0
    shifts: tuple[RhythmShift, ...] = ()
    recent_share: tuple[float, ...] = ()
    usual_share: tuple[float, ...] = ()


def rhythm_state(history: History, cat_id: int | None, i: int) -> Rhythm:
    """Whether the time of day this cat uses the box moved, over four weeks against eight."""
    visits = history.visits if cat_id is None else history.by_cat.get(cat_id, [])
    series = history.household if cat_id is None else history.cat_series.get(cat_id)
    if not visits or series is None:
        return Rhythm(False, RHYTHM_MIN_DAYS)
    begin = visits[0].day
    if i - begin + 1 < RHYTHM_MIN_DAYS:
        return Rhythm(False, RHYTHM_MIN_DAYS - (i - begin + 1))
    recent_low = i - RHYTHM_RECENT + 1
    base_low = recent_low - RHYTHM_BASELINE
    recent_days = series.window(recent_low, i)[1]
    base_days = series.window(max(base_low, begin), recent_low - 1)[1]
    if recent_days < RHYTHM_MIN_COUNTED[0] or base_days < RHYTHM_MIN_COUNTED[1]:
        return Rhythm(False, 0)
    recent = [0] * BLOCKS
    usual = [0] * BLOCKS
    for visit in visits:
        if not history.counted[visit.day]:
            continue
        block = visit.started.hour // BLOCK_HOURS
        if recent_low <= visit.day <= i:
            recent[block] += 1
        elif max(base_low, begin) <= visit.day < recent_low:
            usual[block] += 1
    total_recent, total_usual = sum(recent), sum(usual)
    if not total_recent or not total_usual:
        return Rhythm(False, 0)
    shares = tuple(n / total_recent for n in recent), tuple(n / total_usual for n in usual)
    shifts = []
    for block in range(BLOCKS):
        p = stats.gamma_poisson_p(recent[block], total_recent, usual[block], total_usual)
        ratio_up = shares[0][block] >= RATE_RATIO * shares[1][block] and recent[block] > 0
        ratio_down = shares[0][block] * RATE_RATIO <= shares[1][block]
        expected = shares[1][block] * total_recent
        thin = max(recent[block], expected) < RHYTHM_MIN_BLOCK_VISITS
        if p < RHYTHM_P and (ratio_up or ratio_down) and not thin:
            shifts.append(
                RhythmShift(
                    block, "up" if ratio_up else "down", shares[0][block], shares[1][block], p
                )
            )
    return Rhythm(True, 0, total_recent, total_usual, tuple(shifts), shares[0], shares[1])

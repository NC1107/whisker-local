"""Small statistics for the health checks, with no dependencies and no hidden state."""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from typing import NamedTuple

# Turns the median absolute deviation into a standard deviation for normal data.
MAD_TO_SD = 1.4826

# The z value for a two-sided 95 percent interval.
Z95 = 1.96


def robust_sd(values: Sequence[float], floor: float = 0.0) -> float:
    """Spread that a few wild readings cannot inflate, never below ``floor``."""
    if len(values) < 2:
        return floor
    centre = statistics.median(values)
    return max(MAD_TO_SD * statistics.median(abs(v - centre) for v in values), floor)


def quantile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    position = share * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def anscombe(count: float) -> float:
    """Makes counts of one a day and counts of five a day vary by about the same amount."""
    return math.sqrt(count + 3 / 8)


def negative_binomial_cdf(k: int, shape: float, prob: float) -> float:
    """P(X <= k) for the count of a Poisson whose rate has a gamma prior."""
    if k < 0:
        return 0.0
    total = 0.0
    term = math.exp(shape * math.log(prob))
    for i in range(k + 1):
        total += term
        term *= (i + shape) / (i + 1) * (1 - prob)
    return min(total, 1.0)


def gamma_poisson_p(
    observed: int,
    exposure: float,
    baseline_count: int,
    baseline_exposure: float,
    prior_days: float = 0.5,
) -> float:
    """Two-sided chance of a count this far from the baseline's expectation.

    The rate has a gamma posterior from the baseline days plus a prior worth ``prior_days``
    of the baseline's own mean. A short baseline therefore gives a wide predictive, which is
    what keeps a few days of history from raising alarms.
    """
    if baseline_exposure <= 0 or exposure <= 0:
        return 1.0
    mean = baseline_count / baseline_exposure
    # A baseline of nothing still leaves room for a rare event; half a count is Jeffreys' prior.
    shape = max(prior_days * mean + baseline_count, 0.5)
    rate = prior_days + baseline_exposure
    prob = rate / (rate + exposure)
    lower = negative_binomial_cdf(observed, shape, prob)
    upper = 1.0 - negative_binomial_cdf(observed - 1, shape, prob)
    return min(1.0, 2 * min(lower, max(upper, 0.0)))


class Cusum(NamedTuple):
    up: float
    down: float
    # Where the running sum last left zero, the usual estimate of when the shift began.
    up_from: int
    down_from: int


def cusum(values: Sequence[float], centre: float, spread: float, slack: float = 0.5) -> Cusum:
    """Page's one-sided sums, upward and downward, after the last value.

    They start from zero at the first value, so each call asks whether the recent values
    shifted together, not whether the series ever did.
    """
    up = down = 0.0
    up_from = down_from = 0
    for index, value in enumerate(values):
        z = (value - centre) / spread
        if up == 0.0:
            up_from = index
        if down == 0.0:
            down_from = index
        up = max(0.0, up + z - slack)
        down = max(0.0, down - z - slack)
    return Cusum(up, down, up_from, down_from)


def theil_sen(
    points: Sequence[tuple[float, float]], z: float = Z95
) -> tuple[float, float, float] | None:
    """Median pairwise slope with an interval of ``z`` deviations (Sen, 1968)."""
    n = len(points)
    if n < 4:
        return None
    slopes = sorted(
        (y2 - y1) / (x2 - x1)
        for (x1, y1), (x2, y2) in itertools.combinations(points, 2)
        if x2 != x1
    )
    pairs = len(slopes)
    if pairs == 0:
        return None
    spread = z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    low = max(0, math.floor((pairs - spread) / 2))
    high = min(pairs - 1, math.ceil((pairs + spread) / 2))
    return statistics.median(slopes), slopes[low], slopes[high]


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.1) -> set[int]:
    """Indexes of the tests that stay significant when many are run at once (BH, 1995)."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    cutoff = -1
    for rank, index in enumerate(order, start=1):
        if p_values[index] <= q * rank / len(p_values):
            cutoff = rank
    return set(order[:cutoff]) if cutoff > 0 else set()

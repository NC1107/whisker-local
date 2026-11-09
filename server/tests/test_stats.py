from __future__ import annotations

import math

import pytest

from whisker_local import stats


def test_robust_sd_ignores_a_wild_reading():
    steady = [10.0, 10.1, 9.9, 10.05, 9.95, 10.0, 10.1]
    assert stats.robust_sd([*steady, 17.0]) == pytest.approx(stats.robust_sd(steady), abs=0.05)


def test_robust_sd_never_goes_below_its_floor():
    assert stats.robust_sd([10.0, 10.0, 10.0], floor=0.2) == 0.2


def test_the_negative_binomial_sums_to_one():
    assert stats.negative_binomial_cdf(400, 3.5, 0.4) == pytest.approx(1.0)


def test_a_count_at_the_baseline_is_unremarkable_and_a_doubling_is_not():
    usual = stats.gamma_poisson_p(28, 7, 112, 28)
    doubled = stats.gamma_poisson_p(56, 7, 112, 28)
    assert usual > 0.5
    assert doubled < 0.01


def test_a_short_baseline_gives_a_wide_answer():
    short = stats.gamma_poisson_p(14, 7, 8, 4)
    long = stats.gamma_poisson_p(14, 7, 112, 28)
    assert short > long


def test_a_baseline_of_nothing_does_not_make_one_visit_impossible():
    assert stats.gamma_poisson_p(1, 28, 0, 56) > 0.01


def test_cusum_grows_on_a_shift_and_not_on_noise():
    quiet = stats.cusum([1.0, 1.1, 0.9, 1.0, 1.05, 0.95], 1.0, 0.5)
    shifted = stats.cusum([1.0] * 3 + [2.0] * 6, 1.0, 0.5)
    assert quiet.up < 1 and quiet.down < 1
    assert shifted.up > 4 and shifted.up_from == 3


def test_theil_sen_finds_a_line_and_reports_an_interval_around_it():
    points = [(x, 10 - 0.01 * x + (0.05 if x % 2 else -0.05)) for x in range(60)]
    slope, low, high = stats.theil_sen(points)
    assert slope == pytest.approx(-0.01, abs=0.002)
    assert low <= slope <= high < 0


def test_theil_sen_interval_contains_zero_for_noise():
    points = [(x, 10 + (0.3 if x % 3 == 0 else -0.2 if x % 3 == 1 else 0.05)) for x in range(60)]
    _, low, high = stats.theil_sen(points)
    assert low <= 0 <= high


def test_theil_sen_wants_four_points():
    assert stats.theil_sen([(0, 1), (1, 2), (2, 3)]) is None


def test_benjamini_hochberg_keeps_the_strong_and_drops_the_borderline():
    assert stats.benjamini_hochberg([0.0001, 0.2, 0.5, 0.009]) == {0, 3}
    assert stats.benjamini_hochberg([0.2, 0.5, 0.9]) == set()


def test_anscombe_is_a_square_root_with_an_offset():
    assert stats.anscombe(0) == pytest.approx(math.sqrt(0.375))


def test_quantile_interpolates():
    assert stats.quantile([1, 2, 3, 4, 5], 0.25) == 2
    assert stats.quantile([1, 2], 0.5) == 1.5

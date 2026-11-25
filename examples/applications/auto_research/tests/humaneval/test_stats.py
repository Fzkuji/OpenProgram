"""Agreement and Bradley-Terry against published values; raters needed."""

import pytest

from auto_research.tasks.humaneval import stats
from auto_research.tasks.qualitative import agreement

# Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability": 4 coders, 12 units, missing values.
CODERS = [
    [1, 2, 3, 3, 2, 1, 4, 1, 2, None, None, None],
    [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, None, 3],
    [None, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, None],
    [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, None],
]
UNITS = {u: [c[u] for c in CODERS if c[u] is not None] for u in range(12)}

# Fleiss (1971) as on Wikipedia: 10 subjects, 14 raters, 5 categories, kappa 0.210.
FLEISS = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
          [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]


def test_krippendorff_published_values():
    assert stats.krippendorff_alpha(UNITS, "nominal") == pytest.approx(0.743, abs=1e-3)
    assert stats.krippendorff_alpha(UNITS, "ordinal") == pytest.approx(0.815, abs=1e-3)
    assert stats.krippendorff_alpha(UNITS, "interval") == pytest.approx(0.849, abs=1e-3)
    assert stats.krippendorff_alpha({1: [2, 2], 2: [2, 2]}) is None          # no variation
    assert stats.krippendorff_alpha({1: [1], 2: [2]}) is None                # nothing pairable


def test_alpha_matches_thematic_analysis_two_coders():
    a = [True, False, True, True, False, False, True, False]
    b = [True, False, False, True, False, True, True, False]
    units = {i: [x, y] for i, (x, y) in enumerate(zip(a, b))}
    assert stats.krippendorff_alpha(units, "nominal") == pytest.approx(agreement.alpha(a, b))


def test_fleiss_published_value():
    units = {i: [cat for cat, n in enumerate(row) for _ in range(n)] for i, row in enumerate(FLEISS)}
    assert stats.fleiss_kappa(units) == pytest.approx(0.210, abs=1e-3)
    assert stats.fleiss_kappa({1: [1]}) is None


def test_bradley_terry_and_bootstrap():
    games = [("a", "b", 1.0)] * 7 + [("a", "b", 0.0)] * 3
    s = stats.bradley_terry(games, ["a", "b"])
    assert stats.pair_probability(s, "a", "b") == pytest.approx(7.5 / 11, abs=1e-6)     # one virtual tie
    unbeaten = stats.bradley_terry([("a", "b", 1.0)] * 5, ["a", "b"])
    assert 0.5 < stats.pair_probability(unbeaten, "a", "b") < 1                         # finite
    three = [("a", "b", 1.0)] * 8 + [("b", "c", 1.0)] * 8 + [("a", "c", 1.0)] * 8
    rates = stats.win_rates(stats.bradley_terry(three, ["a", "b", "c"]))
    assert rates["a"] > rates["b"] > rates["c"] and sum(rates.values()) == pytest.approx(1.5)
    by_item = {f"i{k}": [("a", "b", 1.0 if k % 4 else 0.0)] * 3 for k in range(20)}
    bt = stats.bootstrap_bt(by_item, ["a", "b"], resamples=300)
    lo, hi = bt["ci"]["a"]
    assert lo < bt["win_rate"]["a"] < hi and lo > 0.5
    p, (plo, phi) = bt["pairs"][("a", "b")]
    assert plo < p < phi
    mean, (mlo, mhi) = stats.bootstrap_mean({"x": [4, 5], "y": [3], "z": [4]}, resamples=200)
    assert mean == pytest.approx((4.5 + 3 + 4) / 3) and mlo <= mean <= mhi


def test_raters_needed():
    many = stats.raters_needed(items=200, pairs=1, effect=0.2)
    few = stats.raters_needed(items=60, pairs=1, effect=0.2)
    assert many["comparisons"] == few["comparisons"] > 150
    assert 3 <= many["raters"] < few["raters"] and few["recruit"] > few["raters"]
    none = stats.raters_needed(items=10, pairs=1, effect=0.2)
    assert none["raters"] is None and none["items_needed"] > 10
    assert stats.raters_needed(items=200, pairs=3, effect=0.2)["comparisons"] > many["comparisons"]   # Bonferroni

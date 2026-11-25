"""Cohen's kappa, Krippendorff's alpha and the saturation curve."""

import itertools
import math

from auto_research.tasks.qualitative import agreement as ag


def alpha_by_pairs(a, b):
    """Krippendorff's alpha from its definition: observed vs expected disagreement of pairs."""
    values = list(a) + list(b)
    d_o = sum(x != y for x, y in zip(a, b)) * 2 / (2 * len(a))
    pairs = [(x, y) for i, x in enumerate(values) for j, y in enumerate(values) if i != j]
    d_e = sum(x != y for x, y in pairs) / len(pairs)
    return 1 - d_o / d_e


def test_kappa_known_values():
    a = [True] * 20 + [False] * 5 + [True] * 10 + [False] * 15
    b = [True] * 20 + [True] * 5 + [False] * 10 + [False] * 15
    assert math.isclose(ag.kappa(a, b), 0.4, abs_tol=1e-9)             # po 0.7, pe 0.5
    assert ag.kappa([True, False], [True, False]) == 1
    assert ag.kappa([False, False], [False, False]) is None             # undefined, both always "no"


def test_alpha_matches_definition():
    for a, b in [([1, 1, 0, 0, 1, 0], [1, 0, 0, 0, 1, 1]), ([1, 0, 0, 0, 0], [1, 0, 0, 1, 0])]:
        a, b = [bool(x) for x in a], [bool(x) for x in b]
        assert math.isclose(ag.alpha(a, b), alpha_by_pairs(a, b), abs_tol=1e-9)
    assert ag.alpha([True, False], [True, False]) == 1


def test_agreement_table():
    segs = ["s1", "s2", "s3", "s4", "s5", "s6"]
    applied = {"A": {"s1": {"x"}, "s2": {"y"}, "s3": set(), "s4": {"x"}, "s5": set(), "s6": {"y"}},
               "B": {"s1": {"x"}, "s2": {"y", "x"}, "s3": set(), "s4": {"x"}, "s5": set(), "s6": {"y"}}}
    out = ag.agreement(["x", "y", "z"], segs, applied)
    rows = {r["code"]: r for r in out["codes"]}
    assert rows["y"]["kappa"] == 1 and not rows["y"]["low"]
    assert rows["x"]["a"] == 2 and rows["x"]["b"] == 3 and rows["x"]["both"] == 2
    assert rows["x"]["kappa"] == round(ag.kappa([1, 0, 0, 1, 0, 0], [1, 1, 0, 1, 0, 0]), 3)
    assert rows["x"]["low"] == (rows["x"]["kappa"] < 0.6)
    assert rows["z"]["kappa"] is None and rows["z"]["unused"]
    a_all = [c in applied["A"][s] for c in "xyz" for s in segs]
    b_all = [c in applied["B"][s] for c in "xyz" for s in segs]
    assert out["overall"] == round(ag.kappa(a_all, b_all), 3) and out["percent"] == round(100 * 17 / 18, 1)


def test_saturation_curve():
    order = ["a:1", "a:2", "b:1", "c:1", "d:1"]
    files = {"a:1": "a", "a:2": "a", "b:1": "b", "c:1": "c", "d:1": "d"}
    applied = {"a:1": {"x"}, "a:2": {"y"}, "b:1": {"x", "z"}, "c:1": {"y"}, "d:1": set()}
    emerged = {"a:1": {"x", "y"}, "b:1": {"z"}, "c:1": {"x"}, "d:1": {"w"}}
    curve = ag.saturation(order, files, applied, emerged)
    assert [(r["file"], r["new"], r["total"]) for r in curve] == [("a", 2, 2), ("b", 1, 3), ("c", 0, 3), ("d", 0, 3)]
    assert [r["emerged_new"] for r in curve] == [2, 1, 0, 1] and curve[-1]["emerged"] == ["w"]
    assert not ag.saturated(curve)                       # a code still emerged in open coding at d
    curve = ag.saturation(order, files, applied)
    assert ag.saturated(curve) and not ag.saturated(curve[:3])
    assert list(itertools.chain(*(r["codes"] for r in curve))) == ["x", "y", "z"]

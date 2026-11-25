"""Effects and variances, DerSimonian-Laird pooling, Egger's test and GRADE."""

import math
import statistics

import pytest

from auto_research.tasks.metaanalysis import stats


def dl_by_hand(ys, vs):
    """DerSimonian-Laird written out from the 1986 paper's formulas."""
    w = [1 / v for v in vs]
    fixed = sum(a * b for a, b in zip(w, ys)) / sum(w)
    q = sum(a * (y - fixed) ** 2 for a, y in zip(w, ys))
    tau2 = max(0, (q - (len(ys) - 1)) / (sum(w) - sum(a * a for a in w) / sum(w)))
    ws = [1 / (v + tau2) for v in vs]
    mu = sum(a * y for a, y in zip(ws, ys)) / sum(ws)
    return mu, math.sqrt(1 / sum(ws)), tau2, q


def test_t_quantile_table_values():
    assert stats.t_quantile(0.975, 10) == pytest.approx(2.228139, abs=1e-5)
    assert stats.t_quantile(0.975, 1) == pytest.approx(12.7062, abs=1e-3)
    assert stats.t_quantile(0.975, 1e6) == pytest.approx(1.95996, abs=1e-4)


def test_pool_matches_the_formulas():
    ys, vs = [1.0, 2.0, 0.5, 1.5, -0.2], [0.1, 0.2, 0.05, 0.3, 0.15]
    r = stats.pool([{"y": y, "v": v} for y, v in zip(ys, vs)])
    mu, se, tau2, q = dl_by_hand(ys, vs)
    assert r["mu"] == pytest.approx(mu) and r["se"] == pytest.approx(se) and r["tau2"] == pytest.approx(tau2)
    assert r["i2"] == pytest.approx(100 * (q - 4) / q)
    assert r["ci"] == pytest.approx([mu - 1.959964 * se, mu + 1.959964 * se], abs=1e-5)
    half = stats.t_quantile(0.975, 3) * math.sqrt(tau2 + se * se)
    assert r["pi"] == pytest.approx([mu - half, mu + half])
    assert sum(r["weights"]) == pytest.approx(100)


def test_homogeneous_studies_have_no_tau():
    r = stats.pool([{"y": 1.0, "v": 0.1}, {"y": 1.0, "v": 0.2}, {"y": 1.0, "v": 0.4}])
    assert r["tau2"] == 0 and r["i2"] == 0 and r["mu"] == pytest.approx(1.0)
    one = stats.pool([{"y": 0.7, "v": 0.04}])
    assert one["mu"] == 0.7 and one["ci"] == pytest.approx([0.7 - 1.959964 * 0.2, 0.7 + 1.959964 * 0.2], abs=1e-5)
    assert one["pi"] == [None, None]


def test_effects_variances_and_imputation():
    studies = [
        {"method": 96.0, "comparator": 95.0, "kind": "sd", "method_spread": 0.2, "comparator_spread": 0.1, "n": 5,
         "decimals": 1},
        {"method": 94.1, "comparator": 93.0, "kind": "se", "method_spread": 0.3, "comparator_spread": 0.4,
         "decimals": 1},
        {"method": 0.042, "comparator": 0.056, "higher_is_better": False, "scale": 100, "kind": "none",
         "decimals": 3},
        {"method": 95.5, "comparator": 95.3, "kind": "ci95", "method_spread": 0.392, "decimals": 1},
    ]
    rows, info = stats.effects(studies)
    assert [round(r["y"], 6) for r in rows] == [1.0, 1.1, 1.4, 0.2]          # an error rate's gain is positive
    assert rows[0]["v"] == pytest.approx((0.04 + 0.01) / 5) and rows[1]["v"] == pytest.approx(0.25)
    se = 0.392 / stats.Z95                                                   # one arm's spread used for both
    assert rows[3]["v"] == pytest.approx(2 * se * se) and rows[3]["variance"] == "reported"
    sds = sorted([0.2, 0.1, 0.3, 0.4, se, se])
    assert info["imputed_sd"] == pytest.approx(statistics.median(sds))
    assert rows[2]["variance"] == "imputed" and rows[2]["v"] == pytest.approx(2 * info["imputed_sd"] ** 2)
    zero, _ = stats.effects([{"method": 90.0, "comparator": 89.0, "kind": "sd", "method_spread": 0.0,
                              "comparator_spread": 0.0, "n": 3, "decimals": 1}])
    assert zero[0]["v"] == pytest.approx(2 * (0.05 / math.sqrt(3)) ** 2)    # never below rounding


def test_unweighted_when_no_study_reports_a_spread():
    rows, info = stats.effects([{"method": m, "comparator": c, "kind": "none", "decimals": 1}
                                for m, c in [(95.0, 94.0), (96.0, 94.5), (93.0, 92.8)]])
    assert info["method"] == "unweighted" and all(r["v"] is None for r in rows)
    r = stats.pool(rows, info)
    ys = [1.0, 1.5, 0.2]
    se = statistics.stdev(ys) / math.sqrt(3)
    assert r["method"] == "unweighted" and r["mu"] == pytest.approx(statistics.fmean(ys))
    assert r["ci"][1] - r["mu"] == pytest.approx(stats.t_quantile(0.975, 2) * se)
    g = stats.grade(r, rows, info, None)
    assert g["downgrades"]["imprecision"] == 2 and r["ci"][0] < 0 and "没有研究报告方差" in g["reasons"]["imprecision"]


def test_egger():
    assert stats.egger([{"y": 1, "v": 0.1}] * 9) is None
    sym = [{"y": 1.0 + (0.3 if i % 2 else -0.3) * s, "v": s * s} for i, s in enumerate([0.1, 0.1, 0.2, 0.2, 0.3, 0.3,
                                                                                       0.4, 0.4, 0.5, 0.5, 0.6, 0.6])]
    assert stats.egger(sym)["p"] > 0.5
    asym = [{"y": 0.5 + 2.0 * s + 0.01 * (i % 3), "v": s * s} for i, s in enumerate([0.1, 0.15, 0.2, 0.25, 0.3, 0.35,
                                                                                     0.4, 0.45, 0.5, 0.55])]
    e = stats.egger(asym)
    assert e["intercept"] > 1.5 and e["p"] < 0.01 and e["df"] == 8


def test_grade_downgrades():
    rows = [{"y": 1.0, "v": 0.1, "high_risk": True}, {"y": 1.2, "v": 0.1, "high_risk": True},
            {"y": 0.9, "v": 0.1, "indirect": True}]
    res = stats.pool(rows)
    g = stats.grade(res, rows, {"reported": 3}, None)
    assert g["downgrades"] == {"risk_of_bias": 1, "inconsistency": 0, "indirectness": 0, "imprecision": 0,
                               "publication_bias": 0}
    assert g["certainty"] == "Moderate" and g["certainty_zh"] == "中"
    wide = [{"y": 3.0, "v": 0.1}, {"y": -2.0, "v": 0.1}]
    res = stats.pool(wide)
    g = stats.grade(res, wide, {"reported": 2}, None)
    assert g["downgrades"]["inconsistency"] == 1 and g["downgrades"]["imprecision"] == 2
    assert g["certainty"] == "Very low"
    bias = {"intercept": 2.0, "p": 0.03}
    g = stats.grade(stats.pool(rows), rows, {"reported": 3}, bias)
    assert g["downgrades"]["publication_bias"] == 1 and g["certainty"] == "Low"


def test_leave_one_out_finds_the_outlier():
    rows = [{"id": i, "y": y, "v": 0.01} for i, y in zip("abcde", [1.0, 1.1, 0.9, 1.05, 16.0])]
    out = stats.leave_one_out(rows)
    flagged = [x["id"] for x in out if x["influential"]]
    assert flagged == ["e"] and out[4]["residual"] > 10 and out[4]["mu"] == pytest.approx(1.0125, abs=0.01)
    rest = stats.pool(rows[:4])
    assert out[4]["residual"] == pytest.approx((16.0 - rest["mu"]) / math.sqrt(0.01 + rest["tau2"] + rest["se"] ** 2))
    assert stats.leave_one_out(rows[:2]) == []

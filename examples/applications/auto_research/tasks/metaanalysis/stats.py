"""The statistics of meta_analysis, in the standard library only: per-study effects and
their variances, DerSimonian-Laird random-effects pooling with I², tau² and the
prediction interval (Higgins, Thompson & Spiegelhalter 2009), Egger's regression test
for funnel asymmetry, and GRADE's certainty rating from the pooled result.

Effects are differences "method minus comparator" turned so that a positive value
favours the method (an error rate's difference is negated), in percentage points when
the metric is a percentage (a fraction such as 0.953 is scaled by 100).
"""

import math
import statistics

from auto_research.tasks.methods.significance import t_two_sided

Z95 = statistics.NormalDist().inv_cdf(0.975)
I2_HIGH = 50.0          # GRADE inconsistency: I² above this downgrades
EGGER_MIN = 10          # Egger's test needs at least this many studies (Cochrane Handbook 13.3.5.4)
EGGER_ALPHA = 0.10
FEW_STUDIES = 3         # fewer pooled studies than this downgrades for imprecision
LEVELS = ("Very low", "Low", "Moderate", "High")
LEVELS_ZH = {"High": "高", "Moderate": "中", "Low": "低", "Very low": "极低"}


def t_quantile(p: float, df: float) -> float:
    """The p quantile (p > 0.5) of Student's t, by bisection on the two-sided tail."""
    tail = 2 * (1 - p)
    lo, hi = 0.0, 1.0
    while t_two_sided(hi, df) > tail:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided(mid, df) > tail:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def decimals(value: str) -> int:
    v = str(value).strip().rstrip("%")
    return len(v.split(".", 1)[1]) if "." in v else 0


def arm_se(spread: float | None, kind: str, n: int | None) -> float | None:
    """The standard error of one arm's mean from what the study reports: a standard
    deviation over n runs, a standard error, or the half-width of a 95% interval."""
    if spread is None or kind == "none":
        return None
    if kind == "sd":
        return spread / math.sqrt(n or 1)
    if kind == "se":
        return spread
    if kind == "ci95":
        return spread / Z95
    return None


def effects(studies: list[dict]) -> tuple[list[dict], dict]:
    """Each study's effect y and variance v, and how the variances were obtained.

    A study dict has method, comparator (numbers), higher_is_better, scale (1, or 100
    for a fraction), kind (sd, se, ci95, none), method_spread, comparator_spread, n and
    the decimals of its reported values. A spread reported for one arm only is used for
    both. A study without any spread gets the median standard deviation of the studies
    that report one (Cochrane Handbook 6.5.2.7), over its own n or one run; with no
    spread anywhere, v is None and the pooling is unweighted. A standard error is never
    below the rounding of the reported numbers (half a unit of the last decimal over
    √3, the standard deviation of a uniform rounding error)."""
    out, reported_sd = [], []
    for s in studies:
        sign = 1 if s.get("higher_is_better", True) else -1
        scale = s.get("scale", 1)
        y = sign * (s["method"] - s["comparator"]) * scale
        ms, cs = s.get("method_spread"), s.get("comparator_spread")
        ms, cs = (ms if ms is not None else cs), (cs if cs is not None else ms)
        floor = 0.5 * 10 ** -s.get("decimals", 1) / math.sqrt(3) * scale
        n = s.get("n")
        se_m, se_c = arm_se(ms, s.get("kind", "none"), n), arm_se(cs, s.get("kind", "none"), n)
        row = dict(s, y=y, v=None, variance="none")
        if se_m is not None and se_c is not None:
            se_m, se_c = max(se_m * scale, floor), max(se_c * scale, floor)
            row["v"] = se_m ** 2 + se_c ** 2
            row["variance"] = "reported"
            for se in (se_m, se_c):
                reported_sd.append(se * math.sqrt(n or 1))
        out.append(row)
    info = {"reported": sum(r["variance"] == "reported" for r in out), "imputed": 0, "imputed_sd": None,
            "method": "dersimonian-laird"}
    if not reported_sd:
        info["method"] = "unweighted"
        return out, info
    sd = statistics.median(reported_sd)
    info["imputed_sd"] = sd
    for r in out:
        if r["v"] is None:
            n = r.get("n") or 1
            floor = 0.5 * 10 ** -r.get("decimals", 1) / math.sqrt(3) * r.get("scale", 1)
            se = max(sd / math.sqrt(n), floor)
            r["v"] = 2 * se ** 2
            r["variance"] = "imputed"
            info["imputed"] += 1
    return out, info


def pool(rows: list[dict], info: dict | None = None) -> dict:
    """DerSimonian-Laird random-effects pooling of rows {y, v}; with v None for every row,
    the unweighted mean with a t interval over the study effects. Returns mu, se, ci,
    z, p, tau2, q, df, i2, the prediction interval (k >= 3) and each row's weight (%)."""
    k = len(rows)
    ys = [r["y"] for r in rows]
    if not k:
        return {"k": 0}
    unweighted = any(r.get("v") is None for r in rows)
    if unweighted:
        mu = statistics.fmean(ys)
        if k == 1:
            return {"k": 1, "mu": mu, "se": None, "ci": [None, None], "p": None, "tau2": None, "q": None, "df": 0,
                    "i2": None, "pi": [None, None], "weights": [100.0], "method": "unweighted"}
        sd = statistics.stdev(ys)
        se = sd / math.sqrt(k)
        t = t_quantile(0.975, k - 1)
        p = t_two_sided(mu / se, k - 1) if se else 0.0
        half_pi = t_quantile(0.975, k - 1) * sd * math.sqrt(1 + 1 / k)
        return {"k": k, "mu": mu, "se": se, "ci": [mu - t * se, mu + t * se], "p": p, "tau2": sd ** 2,
                "q": None, "df": k - 1, "i2": None, "pi": [mu - half_pi, mu + half_pi] if k >= 3 else [None, None],
                "weights": [100 / k] * k, "method": "unweighted"}
    w = [1 / r["v"] for r in rows]
    sw = sum(w)
    fixed = sum(wi * y for wi, y in zip(w, ys)) / sw
    q = sum(wi * (y - fixed) ** 2 for wi, y in zip(w, ys))
    df = k - 1
    c = sw - sum(wi * wi for wi in w) / sw
    tau2 = max(0.0, (q - df) / c) if df > 0 and c > 0 else 0.0
    ws = [1 / (r["v"] + tau2) for r in rows]
    sws = sum(ws)
    mu = sum(wi * y for wi, y in zip(ws, ys)) / sws
    se = math.sqrt(1 / sws)
    z = mu / se
    p = 2 * (1 - statistics.NormalDist().cdf(abs(z)))
    i2 = max(0.0, (q - df) / q) * 100 if q > 0 else 0.0
    pi = [None, None]
    if k >= 3:
        half = t_quantile(0.975, k - 2) * math.sqrt(tau2 + se * se)
        pi = [mu - half, mu + half]
    return {"k": k, "mu": mu, "se": se, "ci": [mu - Z95 * se, mu + Z95 * se], "z": z, "p": p, "tau2": tau2,
            "q": q, "df": df, "i2": i2, "pi": pi, "fixed": fixed, "weights": [100 * wi / sws for wi in ws],
            "method": (info or {}).get("method", "dersimonian-laird")}


def leave_one_out(rows: list[dict], info: dict | None = None) -> list[dict]:
    """The pooled result without each study in turn (from 3 studies): mu, ci, I², and
    the study's studentized deleted residual, (y - mu without it) / sqrt(v + tau² + se²)
    of the others (Viechtbauer & Cheung 2010). A study is an outlier when the residual
    is beyond 1.96 in size, or when leaving it out moves the pooled effect outside the
    full 95% CI."""
    full = pool(rows, info)
    if len(rows) < 3 or full.get("ci", [None])[0] is None:
        return []
    out = []
    for i, r in enumerate(rows):
        rest = pool(rows[:i] + rows[i + 1:], info)
        spread = (r.get("v") or 0) + (rest.get("tau2") or 0) + (rest.get("se") or 0) ** 2
        resid = (r["y"] - rest["mu"]) / math.sqrt(spread) if spread > 0 else 0.0
        moved = not (full["ci"][0] <= rest["mu"] <= full["ci"][1])
        out.append({"id": r.get("id", str(i)), "mu": rest["mu"], "ci": rest["ci"], "i2": rest.get("i2"),
                    "residual": resid, "influential": abs(resid) > Z95 or moved})
    return out


def egger(rows: list[dict]) -> dict | None:
    """Egger's regression: y/se on 1/se by least squares; the intercept's two-sided t test
    (k - 2 degrees of freedom). None below EGGER_MIN studies or without variances."""
    if len(rows) < EGGER_MIN or any(not r.get("v") for r in rows):
        return None
    se = [math.sqrt(r["v"]) for r in rows]
    x = [1 / s for s in se]
    z = [r["y"] / s for r, s in zip(rows, se)]
    k = len(rows)
    mx, mz = statistics.fmean(x), statistics.fmean(z)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        return None
    slope = sum((xi - mx) * (zi - mz) for xi, zi in zip(x, z)) / sxx
    intercept = mz - slope * mx
    resid = sum((zi - intercept - slope * xi) ** 2 for xi, zi in zip(x, z)) / (k - 2)
    se_b0 = math.sqrt(resid * (1 / k + mx * mx / sxx))
    t = intercept / se_b0 if se_b0 > 0 else math.inf
    return {"intercept": intercept, "se": se_b0, "t": t, "df": k - 2, "p": t_two_sided(t, k - 2)}


def grade(result: dict, rows: list[dict], info: dict, bias: dict | None) -> dict:
    """GRADE certainty for the pooled effect. Controlled experiments start High; each
    domain downgrades in code:
    risk of bias: studies at high risk carry more than half the weight (-1);
    inconsistency: I² above 50% (-1), or, unweighted, study effects on both sides of 0;
    indirectness: studies whose setting differs from the question carry more than half
    the weight (-1);
    imprecision: the 95% CI crosses 0 (-1); fewer than 3 studies, or no study reporting
    its spread (-1), at most -2 in all;
    publication bias: Egger p < 0.10 with 10 or more studies (-1); not assessed below 10.
    """
    k = result.get("k", 0)
    weights = result.get("weights") or []
    downs, reasons = {}, {}

    def share(flag: str) -> float:
        return sum(w for r, w in zip(rows, weights) if r.get(flag)) / 100 if weights else 0.0

    high = share("high_risk")
    downs["risk_of_bias"] = 1 if high > 0.5 else 0
    reasons["risk_of_bias"] = f"高偏倚风险研究占权重 {high:.0%}"
    i2 = result.get("i2")
    if i2 is not None:
        downs["inconsistency"] = 1 if i2 > I2_HIGH else 0
        reasons["inconsistency"] = f"I² = {i2:.0f}%"
    else:
        both = any(r["y"] > 0 for r in rows) and any(r["y"] < 0 for r in rows)
        downs["inconsistency"] = 1 if both else 0
        reasons["inconsistency"] = "研究效应方向不一" if both else "研究效应方向一致（未加权，I² 无法计算）"
    ind = share("indirect")
    downs["indirectness"] = 1 if ind > 0.5 else 0
    reasons["indirectness"] = f"设定与问题不完全一致的研究占权重 {ind:.0%}"
    lo, hi = result.get("ci") or [None, None]
    crosses = lo is None or hi is None or lo <= 0 <= hi
    few = k < FEW_STUDIES or info.get("reported", 0) == 0
    downs["imprecision"] = min(2, int(crosses) + int(few))
    parts = []
    if crosses:
        parts.append("95% CI 跨过 0" if lo is not None else "只有一项研究，无法给区间")
    if k < FEW_STUDIES:
        parts.append(f"只有 {k} 项研究")
    if info.get("reported", 0) == 0:
        parts.append("没有研究报告方差，按未加权合并")
    reasons["imprecision"] = "；".join(parts) or "95% CI 不跨 0"
    if bias is None:
        downs["publication_bias"] = 0
        reasons["publication_bias"] = f"研究少于 {EGGER_MIN} 项，未做 Egger 检验"
    else:
        downs["publication_bias"] = 1 if bias["p"] < EGGER_ALPHA else 0
        reasons["publication_bias"] = f"Egger 截距 {bias['intercept']:.2f}，p = {bias['p']:.3f}"
    total = sum(downs.values())
    level = LEVELS[max(0, 3 - total)]
    return {"start": "High", "downgrades": downs, "reasons": reasons, "total": total, "certainty": level,
            "certainty_zh": LEVELS_ZH[level]}


__all__ = ["effects", "egger", "grade", "leave_one_out", "pool", "t_quantile"]

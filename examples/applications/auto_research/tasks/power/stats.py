"""Power of a t test and the sample size it needs, in the standard library only.

The power of a two-sided t test with df degrees of freedom and noncentrality ncp is
E[Phi(ncp - c*S) + Phi(-ncp - c*S)], where c is the critical value and S = sqrt(V/df)
with V chi-square on df. The expectation over S is integrated numerically (Simpson's
rule on S's density), which is exact to well below the precision a seed count needs.
The critical value comes from methods.significance's t distribution by bisection.
"""

import math

from ..methods import significance as sig

MAX_SEEDS = 1000        # a comparison needing more is reported as infeasible
STEPS = 2000            # Simpson intervals (even)


def phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def t_critical(alpha: float, df: float) -> float:
    """c with P(|T| >= c) = alpha for Student's t on df degrees of freedom."""
    lo, hi = 0.0, 1.0
    while sig.t_two_sided(hi, df) > alpha:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if sig.t_two_sided(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return (lo + hi) / 2


def _s_density(s: float, df: float) -> float:
    """Density of S = sqrt(V/df), V ~ chi-square(df)."""
    if s <= 0:
        return 0.0 if df > 1 else math.sqrt(2 / math.pi) if df == 1 else math.inf
    log = (df / 2) * math.log(df) - (df / 2 - 1) * math.log(2) - math.lgamma(df / 2) \
        + (df - 1) * math.log(s) - df * s * s / 2
    return math.exp(log)


def power(ncp: float, df: float, alpha: float) -> float:
    """Power of the two-sided t test at level alpha when the statistic is noncentral t
    with df degrees of freedom and noncentrality ncp."""
    if df <= 0:
        return 0.0
    c = t_critical(alpha, df)
    top = 1 + 12 / math.sqrt(df)
    h = top / STEPS
    total = 0.0
    for i in range(STEPS + 1):
        s = i * h
        f = _s_density(s, df) * (phi(ncp - c * s) + phi(-ncp - c * s))
        total += f * (1 if i in (0, STEPS) else 4 if i % 2 else 2)
    return min(1.0, max(0.0, total * h / 3))


def power_at(n: int, effect: float, paired: bool, sd_diff: float = 0.0, sd_a: float = 0.0, sd_b: float = 0.0,
             alpha: float = 0.05) -> float:
    """Power with n seeds (per method when unpaired) to detect a mean difference of
    effect. Paired: one-sample t on the seed-wise differences (sd_diff). Unpaired:
    two-sample t with n runs per method, variance sd_a^2 + sd_b^2."""
    if n < 2:
        return 0.0
    if paired:
        se, df = sd_diff / math.sqrt(n), n - 1
    else:
        se, df = math.sqrt((sd_a ** 2 + sd_b ** 2) / n), 2 * n - 2
    if se == 0:
        return 1.0 if effect else alpha
    return power(abs(effect) / se, df, alpha)


def seeds_needed(effect: float, paired: bool, sd_diff: float = 0.0, sd_a: float = 0.0, sd_b: float = 0.0,
                 alpha: float = 0.05, target: float = 0.8) -> int | None:
    """The smallest n >= 2 whose power reaches target; None above MAX_SEEDS or when the
    effect is zero."""
    if not effect:
        return None
    args = dict(effect=effect, paired=paired, sd_diff=sd_diff, sd_a=sd_a, sd_b=sd_b, alpha=alpha)
    if power_at(MAX_SEEDS, **args) < target:
        return None
    lo, hi = 2, MAX_SEEDS            # power grows with n: bisect for the first n reaching target
    if power_at(lo, **args) >= target:
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power_at(mid, **args) >= target:
            hi = mid
        else:
            lo = mid
    return hi


__all__ = ["MAX_SEEDS", "phi", "power", "power_at", "seeds_needed", "t_critical"]

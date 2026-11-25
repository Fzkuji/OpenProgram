"""The statistics of claim_stats, in the standard library only: Student's t
distribution through the regularized incomplete beta function, paired and Welch t
tests, a percentile bootstrap of the mean difference, and the p-to-e calibrator that
POPPER's sequential falsification combines.
"""

import math
import random
import statistics

BOOTSTRAP = 10_000
P_FLOOR = 1e-12         # a p-value below this is read as this, so e stays finite


def mean(xs: list[float]) -> float:
    return statistics.fmean(xs)


def sd(xs: list[float]) -> float:
    """Sample standard deviation (n-1); 0 for fewer than two values."""
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (Lentz, as in Numerical Recipes)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def t_two_sided(t: float, df: float) -> float:
    """P(|T| >= |t|) for Student's t with df degrees of freedom."""
    if math.isinf(t):
        return 0.0
    if math.isnan(t) or df <= 0:
        return 1.0
    return min(1.0, betainc(df / 2, 0.5, df / (df + t * t)))


def t_greater(t: float, df: float) -> float:
    """P(T >= t), the one-sided p-value for 'the mean difference is above zero'."""
    two = t_two_sided(t, df)
    return two / 2 if t > 0 else 1 - two / 2


def paired(a: list[float], b: list[float]) -> dict:
    """Paired t test of a - b (same seeds in the same order)."""
    d = [x - y for x, y in zip(a, b)]
    n, m, s = len(d), mean(d), sd(d)
    t = m / (s / math.sqrt(n)) if s > 0 else (math.copysign(math.inf, m) if m else 0.0)
    return {"test": "paired t", "n": n, "diff": m, "t": t, "df": n - 1,
            "p": t_two_sided(t, n - 1), "p_greater": t_greater(t, n - 1)}


def welch(a: list[float], b: list[float]) -> dict:
    """Welch's t test of mean(a) - mean(b), unequal variances."""
    na, nb = len(a), len(b)
    va, vb = sd(a) ** 2 / na, sd(b) ** 2 / nb
    m = mean(a) - mean(b)
    se = math.sqrt(va + vb)
    if se == 0:
        t, df = (math.copysign(math.inf, m) if m else 0.0), float(na + nb - 2)
    else:
        t = m / se
        df = (va + vb) ** 2 / ((va ** 2 / (na - 1) if na > 1 else 0) + (vb ** 2 / (nb - 1) if nb > 1 else 0))
    return {"test": "Welch t", "n": min(na, nb), "diff": m, "t": t, "df": df,
            "p": t_two_sided(t, df), "p_greater": t_greater(t, df)}


def bootstrap_ci(a: list[float], b: list[float], pair: bool, level: float = 0.95, n: int = BOOTSTRAP,
                 seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval of mean(a) - mean(b): the seed-wise differences
    resampled when paired, each group resampled on its own otherwise. Seeded, so a rerun
    gives the same interval."""
    rng = random.Random(seed)
    stats = []
    if pair:
        d = [x - y for x, y in zip(a, b)]
        k = len(d)
        stats = [sum(d[rng.randrange(k)] for _ in range(k)) / k for _ in range(n)]
    else:
        ka, kb = len(a), len(b)
        stats = [sum(a[rng.randrange(ka)] for _ in range(ka)) / ka - sum(b[rng.randrange(kb)] for _ in range(kb)) / kb
                 for _ in range(n)]
    stats.sort()
    tail = (1 - level) / 2
    lo = stats[max(0, int(math.floor(tail * n)))]
    hi = stats[min(n - 1, int(math.ceil((1 - tail) * n)) - 1)]
    return lo, hi


def compare(a: dict, b: dict, level: float) -> dict:
    """a better than b, values already oriented so that larger is better. a and b map
    seed -> value. Paired when both have the same two or more seeds, else Welch; the
    bootstrap interval and the larger seed standard deviation come with it."""
    shared = sorted(set(a) & set(b), key=str)
    xa, xb = list(a.values()), list(b.values())
    out = {"n_a": len(xa), "n_b": len(xb), "mean_a": mean(xa), "mean_b": mean(xb),
           "sd": max(sd(xa), sd(xb)), "diff": mean(xa) - mean(xb)}
    if len(xa) < 2 or len(xb) < 2:
        return out | {"test": "none", "t": None, "df": None, "p": None, "p_greater": None, "ci": None}
    pair = len(shared) >= 2 and set(a) == set(b)
    test = paired([a[s] for s in shared], [b[s] for s in shared]) if pair else welch(xa, xb)
    out.update({k: test[k] for k in ("test", "t", "df", "p", "p_greater")})
    out["ci"] = bootstrap_ci([a[s] for s in shared] if pair else xa, [b[s] for s in shared] if pair else xb, pair, level)
    return out


def e_value(p: float) -> float:
    """The calibrator e = 1 / (2 sqrt(p)) (Vovk and Wang 2021, kappa = 1/2): a valid e-value
    for any valid p-value, since its integral over [0, 1] is 1."""
    return 1 / (2 * math.sqrt(max(p, P_FLOOR)))


def combine(es: list[float]) -> float:
    """Product of e-values of independent (or sequentially run) tests, itself an e-value."""
    return math.prod(es) if es else 1.0


__all__ = ["betainc", "bootstrap_ci", "combine", "compare", "e_value", "paired", "t_greater", "t_two_sided",
           "welch"]

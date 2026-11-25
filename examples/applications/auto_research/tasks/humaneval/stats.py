"""Agreement, Bradley-Terry and the number of raters, in the standard library only.

Krippendorff's alpha is computed from the coincidence matrix of pairable values
(Krippendorff 2011, "Computing Krippendorff's Alpha-Reliability"): any number of raters,
missing values allowed, nominal, ordinal or interval distances. Fleiss' kappa uses the
per-unit agreement with each unit's own number of ratings, so units rated by different
numbers of raters still count. Bradley-Terry strengths are fitted by Hunter's (2004) MM
iterations with ties as half a win to each side and one virtual tie per pair, so a system
that wins every comparison keeps a finite strength. The number of raters reuses the
t-test power of tasks/power on +1/0/-1 preference scores.
"""

import math
import random
from collections import Counter, defaultdict

from ..power import stats as power_stats

ALPHA_OK = 0.667          # Krippendorff's lowest acceptable alpha for tentative conclusions
BOOTSTRAP = 1000
PRIOR_TIES = 1.0          # virtual tied comparisons per pair of systems
MM_STEPS = 500


# Agreement ---------------------------------------------------------------------------------

def _delta(metric: str, values: list, counts: Counter):
    """Squared distance between two values under the metric."""
    if metric == "nominal":
        return lambda c, k: 0.0 if c == k else 1.0
    if metric == "interval":
        return lambda c, k: float(c - k) ** 2
    order = sorted(values)
    rank = {v: i for i, v in enumerate(order)}

    def ordinal(c, k):
        if c == k:
            return 0.0
        lo, hi = sorted((rank[c], rank[k]))
        between = sum(counts[order[g]] for g in range(lo, hi + 1))
        return (between - (counts[c] + counts[k]) / 2) ** 2
    return ordinal


def krippendorff_alpha(units: dict, metric: str = "nominal") -> float | None:
    """units maps a unit to the list of values its raters gave (missing raters left out).
    Units with fewer than two values are not pairable and drop out. None when there is
    no pairable unit or every pairable value is the same (no variation to agree on)."""
    pairable = [v for v in units.values() if len(v) >= 2]
    coincide: Counter = Counter()
    for vals in pairable:
        m = len(vals)
        for i, c in enumerate(vals):
            for j, k in enumerate(vals):
                if i != j:
                    coincide[(c, k)] += 1 / (m - 1)
    counts: Counter = Counter()
    for (c, _), w in coincide.items():
        counts[c] += w
    n = sum(counts.values())
    values = list(counts)
    if n <= 1 or len(values) < 2:
        return None
    delta = _delta(metric, values, counts)
    observed = sum(w * delta(c, k) for (c, k), w in coincide.items())
    expected = sum(counts[c] * counts[k] * delta(c, k) for c in values for k in values) / (n - 1)
    if expected == 0:
        return None
    return 1 - observed / expected


def fleiss_kappa(units: dict) -> float | None:
    """Fleiss' kappa over units rated by two or more raters, each unit's agreement
    computed with its own number of ratings. None without such units or variation."""
    rated = [Counter(v) for v in units.values() if len(v) >= 2]
    if not rated:
        return None
    total = Counter()
    for c in rated:
        total.update(c)
    n_all = sum(total.values())
    p_bar = sum((sum(x * x for x in c.values()) - sum(c.values())) /
                (sum(c.values()) * (sum(c.values()) - 1)) for c in rated) / len(rated)
    p_e = sum((x / n_all) ** 2 for x in total.values())
    if p_e >= 1:
        return None
    return (p_bar - p_e) / (1 - p_e)


# Bradley-Terry -----------------------------------------------------------------------------

def bradley_terry(games: list[tuple[str, str, float]], systems: list[str]) -> dict[str, float]:
    """Strengths (geometric mean 1) from games (a, b, score of a: 1 win, 0.5 tie, 0 loss)."""
    wins = defaultdict(float)
    meet = defaultdict(float)
    for a, b, s in games:
        wins[a] += s
        wins[b] += 1 - s
        meet[frozenset((a, b))] += 1
    for i, a in enumerate(systems):
        for b in systems[i + 1:]:
            wins[a] += PRIOR_TIES / 2
            wins[b] += PRIOR_TIES / 2
            meet[frozenset((a, b))] += PRIOR_TIES
    p = {s: 1.0 for s in systems}
    for _ in range(MM_STEPS):
        new = {}
        for a in systems:
            denom = sum(meet[frozenset((a, b))] / (p[a] + p[b]) for b in systems if b != a)
            new[a] = wins[a] / denom if denom else p[a]
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        new = {s: v / g for s, v in new.items()}
        done = max(abs(new[s] - p[s]) for s in systems) < 1e-10
        p = new
        if done:
            break
    return p


def win_rates(strength: dict[str, float]) -> dict[str, float]:
    """Each system's chance to beat an opponent drawn uniformly from the others."""
    names = list(strength)
    if len(names) < 2:
        return {s: 0.5 for s in names}
    return {a: sum(strength[a] / (strength[a] + strength[b]) for b in names if b != a) / (len(names) - 1)
            for a in names}


def pair_probability(strength: dict[str, float], a: str, b: str) -> float:
    return strength[a] / (strength[a] + strength[b])


def percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def bootstrap_bt(games_by_item: dict[str, list], systems: list[str], resamples: int = BOOTSTRAP,
                 level: float = 0.95, seed: int = 0) -> dict:
    """Bradley-Terry win rates and pairwise probabilities with percentile CIs, resampling
    items (all of an item's comparisons together, since raters of one item correlate)."""
    items = sorted(games_by_item)
    point = bradley_terry([g for i in items for g in games_by_item[i]], systems)
    rng = random.Random(seed)
    rates = defaultdict(list)
    pairs = defaultdict(list)
    for _ in range(resamples if items else 0):
        pick = [rng.choice(items) for _ in items]
        s = bradley_terry([g for i in pick for g in games_by_item[i]], systems)
        for k, v in win_rates(s).items():
            rates[k].append(v)
        for a in systems:
            for b in systems:
                if a != b:
                    pairs[(a, b)].append(pair_probability(s, a, b))
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    out = {"strength": point, "win_rate": win_rates(point), "ci": {}, "pairs": {}}
    for k in systems:
        out["ci"][k] = (percentile(rates[k], lo), percentile(rates[k], hi)) if rates[k] else None
    for a in systems:
        for b in systems:
            if a != b:
                xs = pairs[(a, b)]
                out["pairs"][(a, b)] = (pair_probability(point, a, b),
                                        (percentile(xs, lo), percentile(xs, hi)) if xs else None)
    return out


def bootstrap_mean(values_by_item: dict[str, list[float]], resamples: int = BOOTSTRAP, level: float = 0.95,
                   seed: int = 0) -> tuple[float, tuple[float, float] | None]:
    """Mean over items of each item's mean, with a percentile CI resampling items."""
    means = [sum(v) / len(v) for v in values_by_item.values() if v]
    if not means:
        return float("nan"), None
    rng = random.Random(seed)
    boots = []
    for _ in range(resamples):
        pick = [rng.choice(means) for _ in means]
        boots.append(sum(pick) / len(pick))
    return sum(means) / len(means), (percentile(boots, (1 - level) / 2), percentile(boots, 1 - (1 - level) / 2))


# Raters needed -----------------------------------------------------------------------------

def raters_needed(items: int, pairs: int, effect: float, sd: float = 1.0, alpha: float = 0.05,
                  power: float = 0.8, icc: float = 0.1, attrition: float = 0.15) -> dict:
    """Raters to recruit so each compared pair of systems shows `effect` (mean of the
    +1/0/-1 preference score, or of the per-item rating difference) at alpha (Bonferroni
    over pairs) with the target power. tasks/power gives the comparisons n a one-sample t
    test needs; comparisons of one item by r raters are correlated (intra-item
    correlation icc), so items * r / (1 + (r - 1) * icc) must reach n, which solves to
    r = n (1 - icc) / (items - n icc). Infeasible when items <= n * icc: more raters
    cannot make up for too few items. The count is raised for attention-check drops."""
    a = alpha / max(pairs, 1)
    n = power_stats.seeds_needed(effect=effect, paired=True, sd_diff=sd, alpha=a, target=power)
    out = {"comparisons": n, "alpha_per_pair": a, "effect": effect, "icc": icc, "items": items,
           "attrition": attrition, "raters": None, "recruit": None, "items_needed": None}
    if n is None or items <= n * icc:
        out["items_needed"] = None if n is None else math.floor(n * icc) + 1
        return out
    r = max(3, math.ceil(n * (1 - icc) / (items - n * icc)))
    out["raters"] = r
    out["recruit"] = math.ceil(r / (1 - attrition))
    return out


__all__ = ["ALPHA_OK", "bootstrap_bt", "bootstrap_mean", "bradley_terry", "fleiss_kappa", "krippendorff_alpha",
           "pair_probability", "raters_needed", "win_rates"]

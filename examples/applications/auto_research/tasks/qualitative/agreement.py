"""Inter-coder agreement and saturation. Plain code, no model calls.

Each (segment, code) pair is one yes/no decision per coder. Cohen's kappa is computed
per code over the segments both coders coded, and pooled over every (segment, code)
decision for the overall figure (De Vries et al. 2008); Krippendorff's alpha (nominal,
two coders, no missing values) is computed on the same pooled decisions.
"""

KAPPA_OK = 0.6


def kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa of two yes/no series; None when chance agreement is 1 (both coders
    gave one value throughout, so kappa is undefined)."""
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def alpha(a: list[bool], b: list[bool]) -> float | None:
    """Krippendorff's alpha for nominal values, two coders, every unit coded by both."""
    o = {(x, y): 0 for x in (False, True) for y in (False, True)}
    for x, y in zip(a, b):
        o[(x, y)] += 1
        o[(y, x)] += 1
    n = 2 * len(a)
    if n < 2:
        return None
    nc = {c: o[(c, False)] + o[(c, True)] for c in (False, True)}
    disagree = o[(False, True)] + o[(True, False)]
    expected = 2 * nc[False] * nc[True]
    if not expected:
        return None
    return 1 - (n - 1) * disagree / expected


def agreement(codes: list[str], segments: list[str], applied: dict[str, dict[str, set]]) -> dict:
    """Per-code and pooled agreement. applied[coder][segment] is the set of codes."""
    a_all, b_all, rows = [], [], []
    for code in codes:
        a = [code in applied["A"].get(s, set()) for s in segments]
        b = [code in applied["B"].get(s, set()) for s in segments]
        a_all += a
        b_all += b
        k = kappa(a, b)
        rows.append({"code": code, "a": sum(a), "b": sum(b), "both": sum(x and y for x, y in zip(a, b)),
                     "kappa": None if k is None else round(k, 3),
                     "low": k is not None and k < KAPPA_OK, "unused": not any(a) and not any(b)})
    overall = kappa(a_all, b_all)
    defined = [r["kappa"] for r in rows if r["kappa"] is not None]
    a_ = alpha(a_all, b_all)
    agree = sum(x == y for x, y in zip(a_all, b_all))
    return {"codes": rows, "segments": len(segments),
            "overall": None if overall is None else round(overall, 3),
            "mean": round(sum(defined) / len(defined), 3) if defined else None,
            "alpha": None if a_ is None else round(a_, 3),
            "percent": round(100 * agree / len(a_all), 1) if a_all else None}


def saturation(order: list[str], file_of: dict[str, str], applied: dict[str, set],
               emerged: dict[str, set] | None = None) -> list[dict]:
    """New codes per additional transcript, in file order (Guest et al. 2006).
    applied[segment] is the set of final codes either coder gave in re-coding;
    emerged[segment] the codebook codes whose open labels (either coder) arose there in
    open coding, which shows when each code first emerged from the data."""
    seen, seen_e, out = set(), set(), []
    files = list(dict.fromkeys(file_of[s] for s in order))
    for f in files:
        segs = [s for s in order if file_of[s] == f]
        here = set().union(*(applied.get(s, set()) for s in segs))
        new = sorted(here - seen)
        seen |= here
        row = {"file": f, "new": len(new), "codes": new, "total": len(seen)}
        if emerged is not None:
            got = set().union(*(emerged.get(s, set()) for s in segs))
            row["emerged"] = sorted(got - seen_e)
            row["emerged_new"] = len(row["emerged"])
            seen_e |= got
            row["emerged_total"] = len(seen_e)
        out.append(row)
    return out


def saturated(curve: list[dict], tail: int = 2) -> bool:
    """Saturation: the last `tail` transcripts add no new code (needs more than `tail`
    files). When open coding ran, codes must also have stopped emerging there."""
    if len(curve) <= tail:
        return False
    key = "emerged_new" if "emerged_new" in curve[0] else "new"
    return all(r[key] == 0 for r in curve[-tail:])


__all__ = ["KAPPA_OK", "agreement", "alpha", "kappa", "saturated", "saturation"]

"""The search space of a sweep, its samplers and the parameter importance. Plain code.

A space is `name=spec` entries separated by `;`: `lr=1e-4..1e-1 log` (a float range,
log scale), `epochs=5..20` (an integer range), `batch_size=16,32,64` (choices). The
sampler is Optuna's TPE when optuna is installed, else a Latin hypercube followed by local
perturbation of the best trial so far.
"""

import math
import random
import re
from statistics import fmean, pvariance

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
RANGE = re.compile(r"^(\S+?)\s*\.\.\s*(\S+?)(?:\s+(log))?$")
INT = re.compile(r"^[-+]?\d+$")
MOST_PARAMS = 12
SEED = 0
TRIES = 50                  # attempts at a configuration not tried yet


def parse_space(text: str) -> list[dict]:
    """[{name, type (float, int, choice), low, high, log, choices}] of a space spec; ValueError when malformed."""
    entries = [e.strip() for e in re.split(r"[;\n]", str(text or "")) if e.strip()]
    if not entries:
        raise ValueError('space is empty: write it like "lr=1e-4..1e-1 log; batch_size=16,32,64"')
    space, seen = [], set()
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"space entry {entry!r} is not name=range or name=choices")
        name, spec = (s.strip() for s in entry.split("=", 1))
        if not NAME.match(name):
            raise ValueError(f"space entry {entry!r}: {name!r} is not a parameter name")
        if name in seen:
            raise ValueError(f"space names {name} twice")
        seen.add(name)
        space.append(parse_entry(name, spec))
    if len(space) > MOST_PARAMS:
        raise ValueError(f"space has {len(space)} parameters; at most {MOST_PARAMS}")
    return space


def parse_entry(name: str, spec: str) -> dict:
    m = RANGE.match(spec)
    if m:
        lo_text, hi_text, log = m.group(1), m.group(2), bool(m.group(3))
        try:
            lo, hi = float(lo_text), float(hi_text)
        except ValueError:
            raise ValueError(f"space entry {name}: {spec!r} is not a range of two numbers") from None
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
            raise ValueError(f"space entry {name}: the range {spec!r} must go from a smaller to a larger number")
        if log and lo <= 0:
            raise ValueError(f"space entry {name}: a log range must be above 0")
        kind = "int" if INT.match(lo_text) and INT.match(hi_text) else "float"
        if kind == "int":
            lo, hi = int(lo), int(hi)
        return {"name": name, "type": kind, "low": lo, "high": hi, "log": log, "choices": []}
    if ".." in spec:
        raise ValueError(f"space entry {name}: {spec!r} is not a range like 1e-4..1e-1 or 1e-4..1e-1 log")
    choices = []
    for part in spec.split(","):
        value = convert(part.strip())
        if part.strip() == "":
            raise ValueError(f"space entry {name}: an empty choice in {spec!r}")
        if value not in choices:
            choices.append(value)
    if len(choices) < 2:
        raise ValueError(f"space entry {name}: {spec!r} needs a range or at least two choices")
    return {"name": name, "type": "choice", "low": None, "high": None, "log": False, "choices": choices}


def convert(text: str):
    """A choice as the program would read it from JSON: int, float, bool or string."""
    if INT.match(text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        pass
    return {"true": True, "false": False}.get(text.lower(), text)


def show(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def describe(params: dict) -> str:
    return " ".join(f"{k}={show(v)}" for k, v in params.items())


# Samplers -------------------------------------------------------------------------------

def _to_unit(p: dict, value) -> float:
    lo, hi, v = p["low"], p["high"], value
    if p["log"]:
        lo, hi, v = math.log(lo), math.log(hi), math.log(v)
    return (v - lo) / (hi - lo)


def _from_unit(p: dict, u: float):
    u = min(1.0, max(0.0, u))
    lo, hi = (math.log(p["low"]), math.log(p["high"])) if p["log"] else (p["low"], p["high"])
    v = lo + u * (hi - lo)
    v = math.exp(v) if p["log"] else v
    if p["type"] == "int":
        return min(p["high"], max(p["low"], int(round(v))))
    return float(f"{v:.4g}")


class LocalSearch:
    """A Latin hypercube for the first trials (every range split into as many strata as
    there are initial trials, each stratum used once; choices taken in turn), then
    Gaussian steps around the best one (in the unit or log-unit scale), the step
    shrinking as trials go on."""

    name = "Latin hypercube + local search"

    def __init__(self, space: list[dict], lower: bool, trials: int, seed: int = SEED):
        self.space, self.lower, self.rng = space, lower, random.Random(seed)
        self.initial = min(trials, max(3, trials // 2))
        self.done: list[tuple[dict, float | None]] = []
        self.tried: set[str] = set()
        self.design = self.hypercube(self.initial)

    def hypercube(self, n: int) -> list[dict]:
        columns = {}
        for p in self.space:
            if p["type"] == "choice":
                order = self.rng.sample(p["choices"], len(p["choices"]))
                column = [order[i % len(order)] for i in range(n)]
            else:
                column = [_from_unit(p, (i + self.rng.random()) / n) for i in range(n)]
            self.rng.shuffle(column)
            columns[p["name"]] = column
        return [{name: column[i] for name, column in columns.items()} for i in range(n)]

    def random(self) -> dict:
        return {p["name"]: self.rng.choice(p["choices"]) if p["type"] == "choice" else _from_unit(p, self.rng.random())
                for p in self.space}

    def around(self, best: dict, scale: float) -> dict:
        out = {}
        for p in self.space:
            if p["type"] == "choice":
                others = [c for c in p["choices"] if c != best[p["name"]]]
                out[p["name"]] = self.rng.choice(others) if self.rng.random() < 0.3 else best[p["name"]]
            else:
                out[p["name"]] = _from_unit(p, _to_unit(p, best[p["name"]]) + self.rng.gauss(0, scale))
        return out

    def ask(self):
        scored = [(params, v) for params, v in self.done if v is not None]
        step = len(self.done) - self.initial
        for attempt in range(TRIES):
            if step < 0 and attempt == 0:
                params = self.design[len(self.done)]
            elif step < 0 or not scored:
                params = self.random()
            else:
                best = (min if self.lower else max)(scored, key=lambda x: x[1])[0]
                params = self.around(best, 0.2 * 0.75 ** step)
            if describe(params) not in self.tried:
                break
        self.tried.add(describe(params))
        return params, params

    def tell(self, handle, value: float | None) -> None:
        self.done.append((handle, value))


class OptunaTPE:
    """Optuna's TPE sampler through its ask-and-tell interface."""

    name = "Optuna TPE"

    def __init__(self, optuna, space: list[dict], lower: bool, seed: int = SEED):
        self.optuna = optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        self.study = optuna.create_study(direction="minimize" if lower else "maximize",
                                         sampler=optuna.samplers.TPESampler(seed=seed))
        d = optuna.distributions
        self.dists = {p["name"]: d.CategoricalDistribution(p["choices"]) if p["type"] == "choice" else
                      d.IntDistribution(p["low"], p["high"], log=p["log"]) if p["type"] == "int" else
                      d.FloatDistribution(p["low"], p["high"], log=p["log"]) for p in space}

    def ask(self):
        trial = self.study.ask(self.dists)
        params = {k: (float(f"{v:.4g}") if isinstance(v, float) else v) for k, v in trial.params.items()}
        return params, trial

    def tell(self, trial, value: float | None) -> None:
        if value is None:
            self.study.tell(trial, state=self.optuna.trial.TrialState.FAIL)
        else:
            self.study.tell(trial, value)


def sampler(space: list[dict], lower: bool, trials: int):
    """Optuna TPE when optuna imports, else the plain local search."""
    try:
        import optuna
    except ImportError:
        return LocalSearch(space, lower, trials)
    return OptunaTPE(optuna, space, lower)


# Importance -----------------------------------------------------------------------------

def levels(p: dict, values: list) -> list[str]:
    """Each trial's level of a parameter: the choice, the value when few distinct ones,
    else its third (low, mid, high) by rank."""
    if p["type"] == "choice" or len(set(values)) <= 3:
        return [show(v) for v in values]
    order = sorted(range(len(values)), key=lambda i: values[i])
    names = ["low", "mid", "high"]
    out = [""] * len(values)
    for rank, i in enumerate(order):
        out[i] = names[min(2, rank * 3 // len(values))]
    return out


def importance(space: list[dict], trials: list[dict], lower: bool) -> list[dict]:
    """Per parameter: the share of the metric's variance between its levels (eta
    squared, 0 to 1), the mean metric of each level and the best level. Trials without
    a metric are left out; with fewer than three, nothing is estimated."""
    ok = [t for t in trials if t["metric"] is not None]
    if len(ok) < 3:
        return []
    metrics = [t["metric"] for t in ok]
    total, mean = pvariance(metrics), fmean(metrics)
    out = []
    for p in space:
        tags = levels(p, [t["params"][p["name"]] for t in ok])
        groups: dict[str, list[float]] = {}
        for tag, m in zip(tags, metrics):
            groups.setdefault(tag, []).append(m)
        if len(groups) < 2:
            out.append({"name": p["name"], "eta2": None, "levels": {}, "best": ""})
            continue
        means = {k: fmean(v) for k, v in groups.items()}
        between = sum(len(v) * (fmean(v) - mean) ** 2 for v in groups.values()) / len(metrics)
        best = (min if lower else max)(means, key=means.get)
        out.append({"name": p["name"], "eta2": round(between / total, 3) if total > 0 else 0.0,
                    "levels": {k: {"mean": means[k], "n": len(groups[k])} for k in groups}, "best": best})
    return sorted(out, key=lambda x: -(x["eta2"] or 0))


def at_edge(p: dict, value) -> str:
    """"low" or "high" when a numeric best value sits in the outer tenth of its range."""
    if p["type"] == "choice":
        return ""
    u = _to_unit(p, value)
    return "low" if u <= 0.1 else "high" if u >= 0.9 else ""


__all__ = ["LocalSearch", "OptunaTPE", "at_edge", "describe", "importance", "parse_space", "sampler", "show"]

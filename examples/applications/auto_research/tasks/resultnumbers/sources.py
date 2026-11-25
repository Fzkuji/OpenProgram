"""Where the paper's result numbers can come from, as (file, run, seed, metric, split,
value) entries. Plain code.

Sources: every run of workflow/results.tsv (crashes left out; grouped as the experiment
loop names them: branch, else description without "seed N"; the metric is
experiment.md's Metric), the seed mean of a run with two or more seeds, each
evidence.csv row's value column, and each file an evidence row names as source: a
CSV/TSV by column (identity columns name the run), a JSON by key path, a log by
"name: value" lines (the last value of each name). An entry is current when it is the
latest kept row of its identity; earlier rows and the values its file held in earlier
Git revisions are history, which is how a stale number is recognized.
"""

import csv
import io
import json
import re
import subprocess
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from auto_research import checks, stages

from ..methods.claims import group_name, seed_of

RESULTS = "workflow/results.tsv"
EVIDENCE = "workflow/evidence.csv"
HISTORY = 20                    # earlier Git revisions read per source file
MAX_BYTES = 2_000_000           # a source file larger than this is not read
METRICISH = re.compile(r"loss|acc|error|err\b|f1|score|bleu|rouge|auc|mse|mae|rmse|ppl|perplex|reward|precision"
                       r"|recall|success|exact|latency|throughput|time|seconds|wer|cer|map\b|ndcg|top", re.I)
SPLIT_WORDS = (("test", re.compile(r"(?:^|[^a-z])test", re.I)),
               ("val", re.compile(r"(?:^|[^a-z])(?:val|valid|validation|dev)(?:[^a-z]|$)", re.I)),
               ("train", re.compile(r"(?:^|[^a-z])train", re.I)))
LOG_LINE = re.compile(r"(?<![\w.])([A-Za-z][\w/.\-]*)\s*[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![\w.])")
LOWER_BETTER = re.compile(r"loss|error|err\b|perplex|ppl|mse|mae|rmse|latency|time|seconds|wer|cer", re.I)


def split_of(name: str) -> str:
    for split, rx in SPLIT_WORDS:
        if rx.search("_" + name):
            return split
    return ""


def dec(value) -> Decimal | None:
    try:
        d = Decimal(str(value).strip().replace("−", "-").rstrip("%"))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def places(value) -> int:
    d = dec(value)
    return max(0, -d.as_tuple().exponent) if d is not None else 0


def scaled(paper: str, percent: bool, source: str) -> tuple[Decimal, int, Decimal, int] | None:
    """Both numbers on the paper's scale: a fraction (0.853) next to a percentage
    (85.3, or a number written with \\%) is read as a percentage."""
    p, s = dec(paper), dec(source)
    if p is None or s is None:
        return None
    dp, ds = places(paper), places(source)
    if (percent or abs(p) > Decimal("1.5")) and abs(s) <= 1 and abs(p) > 1:
        s, ds = s * 100, max(ds - 2, 0)
    return p, dp, s, ds


def same(paper: str, source: str, percent: bool = False) -> bool:
    """Equal in exact decimals at the rounding precision of the less precise side."""
    sc = scaled(paper, percent, source)
    if sc is None:
        return False
    p, dp, s, ds = sc
    digits = min(dp, ds)
    if p:                       # rounding may not erase the paper's first two significant digits
        digits = max(digits, min(dp, -p.adjusted() + 1))
    q = Decimal(1).scaleb(-digits)
    return any(p.quantize(q, rounding=r) == s.quantize(q, rounding=r) for r in (ROUND_HALF_UP, ROUND_HALF_EVEN))


def written(value, like: str, percent: bool = False) -> str:
    """value written with the decimals of like (and on its percentage scale)."""
    v = dec(value)
    if v is None:
        return str(value)
    p = dec(like)
    if p is not None and (percent or abs(p) > Decimal("1.5")) and abs(v) <= 1 and abs(p) > 1:
        v *= 100
    return str(v.quantize(Decimal(1).scaleb(-places(like)), rounding=ROUND_HALF_UP))


def entry(file: str, run: str, metric: str, value: str, *, seed: str = "", split: str = "", commit: str = "",
          evidence: str = "", status: str = "", order: int = 0, revision: str = "") -> dict:
    return {"file": file, "run": run, "seed": seed, "metric": metric, "split": split or split_of(metric),
            "value": value, "commit": commit, "evidence": evidence, "status": status, "order": order,
            "revision": revision, "current": False}


def key(e: dict) -> tuple:
    return e["file"], e["run"], e["seed"], e["metric"], e["split"]


def read_table(text: str, suffix: str) -> list[dict]:
    with io.StringIO(text, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t" if suffix == ".tsv" else ",")
        return [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]


def from_results(text: str, metric: str, revision: str = "") -> list[dict]:
    out = []
    for n, r in enumerate(read_table(text, ".tsv")):
        if r.get("status") == "crash" or checks.number(r.get("metric", "")) is None:
            continue
        seed = seed_of(r)
        out.append(entry(RESULTS, group_name(r), metric or "metric", r["metric"], seed=seed,
                         commit=r.get("commit", ""), status=r.get("status", ""), order=n, revision=revision))
    return out


def seed_means(entries: list[dict]) -> list[dict]:
    """The mean over seeds of every results.tsv run with two or more seeded current rows."""
    groups: dict = {}
    for e in entries:
        if e["current"] and e["file"] == RESULTS:
            groups.setdefault((e["run"], e["metric"], e["split"]), []).append(e)
    out = []
    for (run, metric, split), es in groups.items():
        if len(es) < 2 or len({e["seed"] for e in es}) < 2:
            continue
        values = [dec(e["value"]) for e in es]
        mean = sum(values) / len(values)
        digits = max(places(e["value"]) for e in es) + 1
        m = entry(RESULTS, run, metric, str(mean.quantize(Decimal(1).scaleb(-digits))), seed="mean",
                  split=split, commit=es[-1]["commit"], status="mean")
        m["current"] = True
        out.append(m)
    return out


def from_csv(file: str, text: str, evidence: str, metric: str, revision: str = "") -> list[dict]:
    return from_rows(file, read_table(text, Path(file).suffix), evidence, metric, revision)


def from_rows(file: str, rows: list[dict], evidence: str, metric: str, revision: str = "") -> list[dict]:
    if not rows:
        return []
    cols = list(rows[0])
    numeric = [c for c in cols if any(checks.number(r.get(c, "")) is not None for r in rows)]
    metrics = [c for c in numeric if METRICISH.search(c) or (metric and c.lower() == metric.lower())] or numeric
    ident = [c for c in cols if c not in metrics and c.lower() not in ("split",)]
    out = []
    for n, r in enumerate(rows):
        run = "; ".join(f"{c}={r[c]}" for c in ident if r.get(c) and c.lower() != "seed") or f"row {n + 1}"
        for c in metrics:
            if checks.number(r.get(c, "")) is None:
                continue
            out.append(entry(file, run, c, r[c], seed=seed_of(r), split=r.get("split", "") or split_of(c),
                             commit=r.get("commit", ""), evidence=evidence, order=n, revision=revision))
    return out


def leaves(data, path=()):
    if isinstance(data, dict):
        for k, v in data.items():
            yield from leaves(v, path + (str(k),))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from leaves(v, path + (str(i),))
    elif isinstance(data, (int, float)) and not isinstance(data, bool):
        yield path, data


def from_json(file: str, text: str, evidence: str, revision: str = "") -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        rows = [{str(k): "" if v is None else str(v) for k, v in r.items()} for r in data]
        return from_rows(file, rows, evidence, "", revision)
    commit = str(data.get("commit", "")) if isinstance(data, dict) else ""
    out = []
    for n, (path, value) in enumerate(leaves(data)):
        split = next((split_of(p) for p in path if split_of(p)), "")
        rest = [p for p in path[:-1] if split_of(p) != split or not split]
        out.append(entry(file, "/".join(rest) or Path(file).stem, path[-1], repr(value) if isinstance(value, float)
                         else str(value), split=split or split_of(path[-1]), commit=commit, evidence=evidence,
                         order=n, revision=revision))
    return out


def from_log(file: str, text: str, evidence: str, revision: str = "") -> list[dict]:
    last: dict = {}
    for n, m in enumerate(LOG_LINE.finditer(text)):
        last[m.group(1)] = (m.group(2), n)
    return [entry(file, Path(file).stem, name, value, evidence=evidence, order=n, revision=revision)
            for name, (value, n) in last.items()]


def parse(file: str, text: str, evidence: str, metric: str, revision: str = "") -> list[dict]:
    suffix = Path(file).suffix.lower()
    if file == RESULTS:
        return from_results(text, metric, revision)
    if suffix in (".csv", ".tsv"):
        return from_csv(file, text, evidence, metric, revision)
    if suffix == ".json":
        return from_json(file, text, evidence, revision)
    if suffix in (".log", ".txt", ".out", ".jsonl"):
        return from_log(file, text, evidence, revision)
    return []


def git(paper: Path, *args) -> str:
    done = subprocess.run(["git", "-C", str(paper), *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


def history(paper: Path, file: str) -> list[tuple[str, int, str]]:
    """(revision, commit time, text) of the file in its earlier committed versions,
    newest first, skipping the version the working tree holds."""
    out = []
    for line in git(paper, "log", f"-{HISTORY}", "--format=%H %ct", "--", file).splitlines():
        sha, _, t = line.partition(" ")
        text = git(paper, "show", f"{sha}:{file}")
        if text and len(text) <= MAX_BYTES:
            out.append((sha, int(t or 0), text))
    return out


def source_files(paper: Path, rows: list[dict]) -> list[tuple[str, str]]:
    """(file, evidence id) for each file an evidence row names; a glob names several."""
    out = []
    for r in rows:
        src = (r.get("source") or "").strip()
        if not src or src == RESULTS or "://" in src:
            continue
        paths = sorted(paper.glob(src)) if any(ch in src for ch in "*?[") else [paper / src]
        for p in paths:
            if p.is_file() and p.name != ".env" and p.resolve().is_relative_to(paper.resolve()):
                out.append((p.relative_to(paper).as_posix(), r.get("id", "")))
    return out


def collect(paper: Path, metric: str) -> tuple[list[dict], list[dict], list[str]]:
    """(current and historical entries, evidence rows, unreadable source names)."""
    ev_path = stages.folder(paper) / "evidence.csv"
    ev_rows = checks.own_rows(read_table(ev_path.read_text(encoding="utf-8", errors="ignore"), ".csv"),
                              "evidence.csv") if ev_path.is_file() else []
    entries, unreadable = [], []
    files = [(RESULTS, "")] if (paper / RESULTS).is_file() else []
    files += source_files(paper, ev_rows)
    missing = [r.get("source", "") for r in ev_rows if r.get("source") and r["source"] != RESULTS
               and "://" not in r["source"] and not any(f == r["source"] or Path(f).match(r["source"])
                                                         for f, _ in files)]
    unreadable += sorted(set(missing))
    seen = set()
    for file, evidence in files:
        if file in seen:
            continue
        seen.add(file)
        path = paper / file
        if path.stat().st_size > MAX_BYTES:
            unreadable.append(file)
            continue
        ev = ",".join(e for f, e in files if f == file and e)
        now = parse(file, path.read_text(encoding="utf-8", errors="ignore"), ev, metric)
        entries += now
        for sha, t, text in history(paper, file):
            for e in parse(file, text, ev, metric, sha):
                e["revision_time"] = t
                entries.append(e)
    for r in ev_rows:
        value = r.get("value", "")
        if checks.number(value) is not None:
            run = r.get("method") or r.get("model") or r.get("name") or r.get("id", "")
            entries.append(entry(EVIDENCE, run, r.get("metric", "") or "value", value, seed=seed_of(r),
                                 split=r.get("split", ""), commit=r.get("commit", ""), evidence=r.get("id", ""),
                                 order=len(entries)))
    mark_current(entries)
    entries += seed_means(entries)
    for n, e in enumerate(entries, 1):
        e["sid"] = f"S{n}"
    return entries, ev_rows, unreadable


def mark_current(entries: list[dict]) -> None:
    """The working-tree entry of each identity that stands: its latest kept row, else its
    latest row. Every other entry, and every entry of an earlier revision, is history."""
    groups: dict = {}
    for e in entries:
        if not e["revision"]:
            groups.setdefault(key(e), []).append(e)
    for es in groups.values():
        kept = [e for e in es if e["status"] == "keep"] or es
        max(kept, key=lambda e: e["order"])["current"] = True


def lower_is_better(metric: str, direction: str = "") -> bool:
    if direction:
        return direction.strip().lower() == "lower"
    return bool(LOWER_BETTER.search(metric or ""))


def file_time(paper: Path, file: str) -> tuple[int | None, str]:
    """When the file last changed: its last commit, or None with "uncommitted" when the
    working tree differs."""
    if git(paper, "status", "--porcelain", "--", file).strip():
        return None, "uncommitted"
    out = git(paper, "log", "-1", "--format=%ct %h", "--", file).split()
    return (int(out[0]), out[1]) if out else (None, "")


def descends(paper: Path, later: str, earlier: str) -> bool:
    done = subprocess.run(["git", "-C", str(paper), "merge-base", "--is-ancestor", earlier, later],
                          capture_output=True, text=True)
    return done.returncode == 0


def line_time(paper: Path, file: str, line: int) -> tuple[int | None, str]:
    """When the paper line last changed (git blame): (time, short commit), or (None,
    "uncommitted")."""
    out = git(paper, "blame", "--porcelain", "-L", f"{line},{line}", "--", file)
    if not out:
        return None, ""
    sha = out.split()[0]
    if set(sha) == {"0"}:
        return None, "uncommitted"
    t = re.search(r"^committer-time (\d+)", out, re.M)
    return (int(t.group(1)) if t else None), sha[:7]

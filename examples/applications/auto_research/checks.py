"""Checks of the tasks added after the first ones: the experiment loop and its plan,
baselines, findings, the literature tables, the survey's taxonomy, the rebuttal and the
dataset release. Each reads the task's own files under workflow/ and refers to others
(claims, evidence, the bibliography) without judging them. Plain code, no model calls;
a missing file is created from its template when the task runs, as in stages.py.
"""

import csv
import io
import re
import subprocess
from pathlib import Path

from . import brief, prereg, stages
from .tasks.revision.steps.prepare import plans

FIELD = re.compile(r"^([A-Z][A-Za-z ]*):[ \t]*(.*)$", re.M)
EXPERIMENT_FIELDS = ("Edit", "Command", "Metric", "Direction", "Budget", "Stop")
RESULT_COLUMNS = ("commit", "metric", "status", "seconds", "description")
RESULT_STATUS = ("keep", "discard", "crash", "merge")
PLAN_COLUMNS = ("claim", "experiment", "kind", "evidence", "status")
PLAN_KINDS = ("main", "ablation", "robustness", "significance", "baseline", "analysis")
PLAN_STATUS = ("planned", "running", "done", "dropped")
BASELINE_COLUMNS = ("name", "source", "setting", "reported", "reproduced", "tolerance", "status", "log")
BASELINE_STATUS = ("reproduced", "gap", "failed")
FINDING_FIELDS = ("Hypothesis", "Run", "Result", "Verdict")
VERDICTS = ("supported", "refuted", "inconclusive")
STANCES = ("supports", "contradicts", "related")
STUDIES = ("empirical", "theoretical", "survey", "position")   # what kind of study a work is, as Consensus marks it
CONCERN_COLUMNS = ("id", "reviewer", "concern", "type", "answer", "evidence", "promise")
CONCERN_TYPES = ("clarify", "experiment", "misunderstanding", "limitation", "concede")
DATASHEET_PARTS = ("Motivation", "Composition", "Collection process", "Preprocessing", "Uses", "Distribution",
                   "Maintenance")
NOT_REPORTED = ("not reported", "n/a", "—", "-")


def fields(text: str) -> dict[str, str]:
    return {k: v.strip() for k, v in FIELD.findall(text)}


def table(text: str, columns: tuple[str, ...], name: str, delimiter: str = ",") -> tuple[list[str], list[dict]]:
    """The rows of a CSV/TSV artifact, or the columns it lacks."""
    with io.StringIO(text, newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            return [f"{name}: missing columns {', '.join(missing)}"], []
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]
    return [], [r for r in rows if any(r.values())]


def number(value: str) -> float | None:
    try:
        return float(value.replace("%", "").strip())
    except ValueError:
        return None


def template_rows(name: str) -> list[dict]:
    """The example rows of a CSV template: left as they are, they are not work."""
    delimiter = "\t" if name.endswith(".tsv") else ","
    with io.StringIO(stages.template(name), newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f, delimiter=delimiter)]


def own_rows(rows: list[dict], name: str) -> list[dict]:
    examples = template_rows(name)
    return [r for r in rows if r not in examples]


def claim_ids(paper: Path) -> dict[str, str]:
    return stages.claims(paper, None)[1]


def evidence_ids(paper: Path) -> set[str]:
    return set(stages.evidence(paper, None, claim_ids(paper))[1])


def commit_exists(paper: Path, commit: str) -> bool:
    return bool(commit) and subprocess.run(["git", "-C", str(paper), "cat-file", "-e", f"{commit}^{{commit}}"],
                                           capture_output=True).returncode == 0


# The experiment loop: a spec the author approves, then one row per run.

def experiment(paper: Path, created: list[str] | None) -> list[str]:
    """The spec names the files to edit, the command, the metric, its direction, the
    budget and the stop rule, and is approved; every run of results.tsv is well formed,
    every kept run is a commit, and each kept run improves on the one kept before it."""
    spec = stages.read(paper, "experiment.md", created)
    found = []
    if spec == stages.template("experiment.md"):
        return ["experiment.md: still the template; write the loop's spec"]
    values = fields(spec)
    found += [f"experiment.md: {k} is empty" for k in EXPERIMENT_FIELDS if not values.get(k)]
    if values.get("Direction", "").lower() not in ("lower", "higher"):
        found.append("experiment.md: Direction must be lower or higher")
    for name in [n.strip() for n in values.get("Edit", "").split(",") if n.strip()]:
        if not (paper / name).exists():
            found.append(f"experiment.md: file to edit {name} does not exist")
    if not plans.approved({"Approved": values.get("Approved", "")}):
        found.append("experiment.md: not approved by the author")
    problems, rows = table(stages.read(paper, "results.tsv", created), RESULT_COLUMNS, "results.tsv", "\t")
    if problems:
        return found + problems
    lower = values.get("Direction", "lower").lower() == "lower"
    return found + branches(paper, rows, lower)


def branches(paper: Path, rows: list[dict], lower: bool) -> list[str]:
    """The runs of results.tsv, on one line or on parallel branches (AI Scientist v2's
    tree of experiments, AlphaEvolve's population). Optional columns: branch (empty is
    main) and parent (the kept commit a branch starts from). Within a branch each kept run
    improves on the branch's best so far, which starts at its parent's metric. A merge row
    names the commit merged back into main; it must be the best kept run over all
    branches, and when two or more branches kept runs, one of them must be merged."""
    better = (lambda a, b: a < b) if lower else (lambda a, b: a > b)
    found, best, kept_value = [], {}, {}
    merged = []
    for i, row in enumerate(rows, 1):
        where = f"results.tsv row {i}"
        branch = row.get("branch", "") or "main"
        if row["status"] not in RESULT_STATUS:
            found.append(f"{where}: status must be one of {', '.join(RESULT_STATUS)}")
            continue
        if row["status"] == "crash":
            continue
        if row["status"] == "merge":
            merged.append((i, row["commit"]))
            if row["commit"] not in kept_value:
                found.append(f"{where}: merges {row['commit'] or '(empty)'}, which is not a kept run")
            continue
        value = number(row["metric"])
        if value is None:
            found.append(f"{where}: metric {row['metric']!r} is not a number")
            continue
        if row["status"] != "keep":
            continue
        if not commit_exists(paper, row["commit"]):
            found.append(f"{where}: kept run's commit {row['commit'] or '(empty)'} is not in the repository")
        if branch not in best and row.get("parent"):
            if row["parent"] not in kept_value:
                found.append(f"{where}: branch {branch} starts from {row['parent']}, which is not a kept run")
            else:
                best[branch] = kept_value[row["parent"]]
        if branch in best and not better(value, best[branch]):
            on = "" if branch == "main" else f" on branch {branch}"
            found.append(f"{where}: kept, but {value} does not improve on {best[branch]}{on}")
        if branch not in best or better(value, best[branch]):
            best[branch] = value
        kept_value[row["commit"]] = value
    kept_branches = {(r.get("branch") or "main") for r in rows if r["status"] == "keep"}
    if kept_value and len(kept_branches) > 1:
        top = min(kept_value.values()) if lower else max(kept_value.values())
        if not merged:
            leader = next(c for c, v in kept_value.items() if v == top)
            found.append(f"results.tsv: {len(kept_branches)} branches kept runs but none is merged; "
                         f"merge the best one ({leader}, {top})")
        elif kept_value.get(merged[-1][1]) is not None and kept_value[merged[-1][1]] != top:
            found.append(f"results.tsv row {merged[-1][0]}: merged {merged[-1][1]} ({kept_value[merged[-1][1]]}), "
                         f"but the best kept run reached {top}")
    return found


def experiment_plan(paper: Path, created: list[str] | None) -> list[str]:
    """Every claim has a planned experiment; a method paper has an ablation for each
    claim; a done experiment names the evidence that records it."""
    problems, rows = table(stages.read(paper, "experiment_plan.csv", created), PLAN_COLUMNS, "experiment_plan.csv")
    if problems:
        return problems
    rows = own_rows(rows, "experiment_plan.csv")
    claims, evidence = claim_ids(paper), evidence_ids(paper)
    found = []
    if not claims:
        found.append("experiment_plan.csv: claims.md has no claims to plan for")
    for i, row in enumerate(rows, 1):
        where = f"experiment_plan.csv row {i}"
        if row["claim"] not in claims:
            found.append(f"{where}: unknown claim {row['claim']}")
        if row["kind"] not in PLAN_KINDS:
            found.append(f"{where}: kind must be one of {', '.join(PLAN_KINDS)}")
        if row["status"] not in PLAN_STATUS:
            found.append(f"{where}: status must be one of {', '.join(PLAN_STATUS)}")
        if row["status"] == "done" and row["evidence"] not in evidence:
            found.append(f"{where}: done, but evidence {row['evidence'] or '(empty)'} is not in evidence.csv")
    for cid in claims:
        mine = [r for r in rows if r["claim"] == cid and r["status"] != "dropped"]
        if not mine:
            found.append(f"experiment_plan.csv: claim {cid} has no experiment")
        elif brief.read(paper)["kind"] == "method" and not any(r["kind"] == "ablation" for r in mine):
            found.append(f"experiment_plan.csv: claim {cid} has no ablation")
    since = prereg.compare(paper, [r for r in rows if r["status"] != "dropped"])
    found += [f"experiment_plan.csv: {r['claim']} {r['kind']} \"{r['experiment']}\" changed after the plan was "
              f"preregistered on {since['at'][:10]}; lock the plan again and say why" for r in since["changed"]]
    return found


def baselines(paper: Path, created: list[str] | None) -> list[str]:
    """Every baseline was run: reproduced within its tolerance, a gap, or failed, with a log."""
    problems, rows = table(stages.read(paper, "baselines.csv", created), BASELINE_COLUMNS, "baselines.csv")
    if problems:
        return problems
    rows = own_rows(rows, "baselines.csv")
    if not rows:
        return ["baselines.csv: no baseline"]
    found = []
    for row in rows:
        where = f"baselines.csv {row['name'] or '(no name)'}"
        reported, reproduced, tolerance = number(row["reported"]), number(row["reproduced"]), number(row["tolerance"])
        if row["status"] not in BASELINE_STATUS:
            found.append(f"{where}: not run yet (status must be one of {', '.join(BASELINE_STATUS)})")
            continue
        if reported is None:
            found.append(f"{where}: reported {row['reported']!r} is not a number")
        if row["status"] != "failed":
            if reproduced is None:
                found.append(f"{where}: reproduced {row['reproduced']!r} is not a number")
            elif reported is not None and tolerance is not None:
                close = abs(reproduced - reported) <= tolerance
                if close != (row["status"] == "reproduced"):
                    found.append(f"{where}: {reproduced} vs {reported} within {tolerance} is "
                                 f"{'reproduced' if close else 'a gap'}, not {row['status']}")
        if not row["log"] or not (paper / row["log"]).exists():
            found.append(f"{where}: log {row['log'] or '(empty)'} does not exist")
    return found


def findings(paper: Path, created: list[str] | None) -> list[str]:
    """At least one finding; each states its hypothesis, run, result and a verdict."""
    text = stages.read(paper, "findings.md", created)
    entries = re.split(r"^##\s+", text, flags=re.M)[1:]
    if not entries or text == stages.template("findings.md"):
        return ["findings.md: no finding recorded"]
    found = []
    for entry in entries:
        name = entry.split("\n", 1)[0].strip()
        values = fields(entry)
        found += [f"findings.md {name}: {k} is empty" for k in FINDING_FIELDS if not values.get(k)]
        if values.get("Verdict") and values["Verdict"].lower() not in VERDICTS:
            found.append(f"findings.md {name}: Verdict must be one of {', '.join(VERDICTS)}")
    return found


def claim_support(paper: Path, created: list[str] | None) -> list[str]:
    """Each claim is set against at least two works of the bibliography, each with the
    sentence that bears on it; a contradicting work says how the paper answers it."""
    problems, rows = table(stages.read(paper, "claim_support.csv", created),
                           ("claim", "work", "stance", "quote", "note"), "claim_support.csv")
    if problems:
        return problems
    rows = own_rows(rows, "claim_support.csv")
    claims, keys = claim_ids(paper), stages.bib_keys(paper)
    found = []
    for i, row in enumerate(rows, 1):
        where = f"claim_support.csv row {i}"
        if row["claim"] not in claims:
            found.append(f"{where}: unknown claim {row['claim']}")
        if row["work"] not in keys:
            found.append(f"{where}: {row['work']} is not in the .bib files")
        if row["stance"] not in STANCES:
            found.append(f"{where}: stance must be one of {', '.join(STANCES)}")
        if not row["quote"]:
            found.append(f"{where}: no quote")
        if row.get("study") and row["study"] not in STUDIES:
            found.append(f"{where}: study must be one of {', '.join(STUDIES)}")
        if row["stance"] == "contradicts" and not row["note"]:
            found.append(f"{where}: contradicts the claim; say in note how the paper answers it")
    for cid in claims:
        if len({r["work"] for r in rows if r["claim"] == cid}) < 2:
            found.append(f"claim_support.csv: claim {cid} is set against fewer than two works")
        mine = [r for r in rows if r["claim"] == cid and r.get("study") in STUDIES]
        if mine and all(r["study"] in ("position", "survey") for r in mine) and any(r["stance"] == "supports" for r in mine):
            found.append(f"claim_support.csv: claim {cid} is supported only by position or survey works; "
                         "find an empirical or theoretical one")
    return found


def consensus(paper: Path) -> dict[str, dict[str, int]]:
    """Per claim, how many works support, contradict or only relate to it (Consensus's meter)."""
    _, rows = table(stages.read(paper, "claim_support.csv", None), ("claim", "work", "stance"), "claim_support.csv")
    out: dict[str, dict[str, int]] = {}
    for r in own_rows(rows, "claim_support.csv"):
        meter = out.setdefault(r["claim"], {s: 0 for s in STANCES})
        if r["stance"] in meter:
            meter[r["stance"]] += 1
    return out


def literature_table(paper: Path, created: list[str] | None) -> list[str]:
    """A row per compared work of the bibliography; every cell filled or "not reported"."""
    text = stages.read(paper, "literature_table.csv", created)
    problems, rows = table(text, ("key",), "literature_table.csv")
    if problems:
        return problems
    rows = own_rows(rows, "literature_table.csv")
    if len(rows) < 3:
        return [f"literature_table.csv: {len(rows)} works compared; compare at least three"]
    keys = stages.bib_keys(paper)
    found = []
    for row in rows:
        if row["key"] not in keys:
            found.append(f"literature_table.csv: {row['key']} is not in the .bib files")
        empty = [k for k, v in row.items() if k != "key" and not v]
        if empty:
            found.append(f"literature_table.csv {row['key']}: empty {', '.join(empty)} (write \"not reported\" if the work does not say)")
    return found


def taxonomy(paper: Path, created: list[str] | None) -> list[str]:
    """Every surveyed work sits in a category, every category holds a work, every key is in the bibliography."""
    text = stages.read(paper, "taxonomy.md", created)
    if text == stages.template("taxonomy.md"):
        return ["taxonomy.md: still the template; write the categories"]
    parts = re.split(r"^#{2,4}\s+", text, flags=re.M)[1:]
    placed, found, keys = set(), [], stages.bib_keys(paper)
    for part in parts:
        name = part.split("\n", 1)[0].strip()
        works = [w.strip() for w in fields(part).get("Works", "").split(",") if w.strip()]
        if not works and "\n#" not in part:
            found.append(f"taxonomy.md {name}: no works")
        found += [f"taxonomy.md {name}: {w} is not in the .bib files" for w in works if w not in keys]
        placed |= set(works)
    surveyed = [k for k in stages.LITERATURE.findall(stages.read(paper, "literature.md", None)) if k != "key"]
    found += [f"taxonomy.md: surveyed work {k} is in no category" for k in surveyed if k not in placed]
    return found


def rebuttal(paper: Path, created: list[str] | None) -> list[str]:
    """The reviews are in; every concern is answered, typed and backed where it cites
    evidence; the response fits the venue's limit and is approved."""
    reviews = stages.read(paper, "rebuttal/reviews.md", created)
    found = []
    if reviews == stages.template("rebuttal/reviews.md"):
        found.append("rebuttal/reviews.md: paste the reviews")
    problems, rows = table(stages.read(paper, "rebuttal/concerns.csv", created), CONCERN_COLUMNS, "rebuttal/concerns.csv")
    if problems:
        return found + problems
    rows = own_rows(rows, "rebuttal/concerns.csv")
    if not rows:
        found.append("rebuttal/concerns.csv: no concern listed")
    evidence = evidence_ids(paper)
    for row in rows:
        where = f"rebuttal/concerns.csv {row['id']}"
        if row["type"] not in CONCERN_TYPES:
            found.append(f"{where}: type must be one of {', '.join(CONCERN_TYPES)}")
        if not row["answer"]:
            found.append(f"{where}: not answered")
        for eid in plans.ids(row["evidence"]):
            if eid not in evidence:
                found.append(f"{where}: evidence {eid} is not in evidence.csv")
        if row["type"] == "experiment" and not (row["evidence"] or row["promise"]):
            found.append(f"{where}: asks for an experiment; cite its evidence or state the promise")
    response = stages.read(paper, "rebuttal/response.md", created)
    values = fields(response)
    limit = int(number(values.get("Limit", "0")) or 0)
    for part in re.split(r"^##\s+", response, flags=re.M)[1:]:
        name, _, body = part.partition("\n")
        if not body.strip():
            found.append(f"rebuttal/response.md {name.strip()}: empty")
        elif limit and len(body.strip()) > limit:
            found.append(f"rebuttal/response.md {name.strip()}: {len(body.strip())} characters, over the limit of {limit}")
    if not plans.approved({"Approved": values.get("Approved", "")}):
        found.append("rebuttal/response.md: not approved by the author")
    return found


def datasheet(paper: Path, created: list[str] | None) -> list[str]:
    """The dataset has a license, a location that exists, and every part of its datasheet written."""
    text = stages.read(paper, "datasheet.md", created)
    values, parts = fields(text), stages.parts(text)
    found = []
    if not values.get("License"):
        found.append("datasheet.md: License is empty")
    where = values.get("Location", "")
    if not where:
        found.append("datasheet.md: Location is empty")
    elif not where.startswith(("http://", "https://")) and not (paper / where).exists():
        found.append(f"datasheet.md: Location {where} does not exist")
    found += [f"datasheet.md: {p} is not written" for p in DATASHEET_PARTS if not parts.get(p)]
    return found


__all__ = ["baselines", "claim_support", "datasheet", "experiment", "experiment_plan", "findings",
           "literature_table", "rebuttal", "taxonomy"]

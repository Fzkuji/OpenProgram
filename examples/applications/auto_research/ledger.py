"""What the project's model calls have cost: every run's trail (run.json) under drafts/,
added up per workflow. Plain code, no model calls.

The host meters each call (tokens and the model's list price); a Trail adds the calls of
its stages, and this module adds the runs. A run saved before metering existed counts
as a run with no spend.
"""

import json
from pathlib import Path

from .trail import SPEND, add, blank


def runs(paper: Path) -> list[dict]:
    """Every saved run of the project, oldest first: workflow, finished, usage."""
    found = []
    drafts = Path(paper) / "drafts"
    if not drafts.is_dir():
        return found
    for file in drafts.rglob("run.json"):
        try:
            run = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(run, dict) or "workflow" not in run:
            continue
        usage = run.get("usage") or add(blank(), {})
        found.append({"workflow": run["workflow"], "finished": run.get("finished") or "",
                      "usage": {k: usage.get(k, 0) for k in SPEND}})
    return sorted(found, key=lambda r: r["finished"])


def spend(paper: Path) -> dict:
    """The project's spend in total and per workflow, the most expensive first.
    The auto_research runner's own trail only wraps other workflows' runs, so it is left out."""
    total, by = add(blank(), {}), {}
    count = 0
    for run in runs(paper):
        if run["workflow"] == "auto_research":
            continue
        count += 1
        add(total, run["usage"])
        row = by.setdefault(run["workflow"], dict(add(blank(), {}), runs=0))
        row["runs"] += 1
        add(row, run["usage"])
    order = sorted(by.items(), key=lambda kv: (-kv[1]["cost"], -kv[1]["runs"]))
    return {"runs": count, **total, "by_workflow": [dict(workflow=k, **v) for k, v in order]}


__all__ = ["runs", "spend"]

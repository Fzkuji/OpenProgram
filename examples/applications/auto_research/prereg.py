"""Preregistration: the author locks the experiment plan before the results are in, so
a plan changed after seeing the data shows. Learned from data-to-paper, which fixes the
hypothesis and analysis plan before it looks at the data. Plain code, no model calls.

The lock is workflow/preregistration.json: the planned rows (claim, experiment, kind)
at the moment of locking, the commit, and the earlier locks. A row that was locked and
later changed or removed is a problem of the experiment plan until the author locks
again; every lock stays in the history, so the change is still visible. Rows added
after the lock are exploratory: allowed, and listed as such.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from . import stages

KEY = ("claim", "experiment", "kind")


def path(paper: Path) -> Path:
    return stages.folder(paper) / "preregistration.json"


def load(paper: Path) -> dict:
    try:
        return json.loads(path(paper).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def lock(paper: Path, rows: list[dict], reason: str = "") -> dict:
    """Lock the planned rows; an earlier lock moves to the history with the reason."""
    old = load(paper)
    head = subprocess.run(["git", "-C", str(paper), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    history = old.get("history", [])
    if old.get("rows"):
        history.append({k: old.get(k) for k in ("at", "head", "rows")} | {"replaced_because": reason.strip()})
    data = {"at": datetime.now().isoformat(timespec="seconds"), "head": head,
            "rows": [{k: r.get(k, "").strip() for k in KEY} for r in rows if r.get("status") != "dropped"],
            "history": history}
    path(paper).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def compare(paper: Path, rows: list[dict]) -> dict:
    """What changed since the lock: changed (locked rows no longer in the plan as they
    were) and exploratory (rows added after it). Empty when nothing is locked."""
    locked = load(paper).get("rows")
    if not locked:
        return {"locked": False, "changed": [], "exploratory": []}
    now = {tuple(r.get(k, "").strip() for k in KEY) for r in rows}
    was = {tuple(r[k] for k in KEY) for r in locked}
    return {"locked": True, "at": load(paper).get("at", ""),
            "changed": [dict(zip(KEY, r)) for r in sorted(was - now)],
            "exploratory": [dict(zip(KEY, r)) for r in sorted(now - was)]}


__all__ = ["compare", "load", "lock", "path"]

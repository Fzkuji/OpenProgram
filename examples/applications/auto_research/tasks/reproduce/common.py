"""What the reproduction tools share: the experiment spec, results.tsv, reading the metric
from a run's output and comparing two values within a tolerance. Plain code."""

import csv
import io
import math
import re
import subprocess
from pathlib import Path

from auto_research import checks, stages

from ..submission.common import repo, run_folder

DEFAULT_TOLERANCE = "1%"
NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def spec(paper: Path, need: tuple[str, ...] = ("Command", "Metric")) -> dict[str, str]:
    """The fields of workflow/experiment.md; the ones in need must be filled in."""
    values = checks.fields(stages.read(paper, "experiment.md", None))
    empty = [k for k in need if not values.get(k)]
    if empty:
        raise ValueError(f"workflow/experiment.md: {', '.join(empty)} is empty")
    return values


def lower_is_better(values: dict) -> bool:
    return values.get("Direction", "lower").strip().lower() != "higher"


def results_path(paper: Path) -> Path:
    return stages.folder(paper) / "results.tsv"


def results(paper: Path) -> tuple[list[str], list[dict]]:
    """The header and rows of workflow/results.tsv (empty when there is no file)."""
    path = results_path(paper)
    if not path.is_file():
        return list(checks.RESULT_COLUMNS), []
    with io.StringIO(path.read_text(encoding="utf-8"), newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        header = [c for c in (reader.fieldnames or []) if c]
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]
    return header, [r for r in rows if any(r.values())]


def metric_from(output: str, metric: str) -> float | None:
    """The last `<Metric>: <number>` (or `=`) the run printed."""
    found = re.findall(rf"(?:^|[\s,;{{(]){re.escape(metric)}\s*[:=]\s*({NUMBER})", output, re.M)
    return float(found[-1]) if found else None


def tolerance(given: str, values: dict) -> tuple[str, float]:
    """("relative", 0.01) for "1%", ("absolute", 0.002) for "0.002"; the argument wins over
    experiment.md's Tolerance field, which wins over 1%."""
    text = (given or values.get("Tolerance", "") or DEFAULT_TOLERANCE).strip()
    m = re.fullmatch(rf"({NUMBER})\s*(%?)", text)
    if not m:
        raise ValueError(f"tolerance {text!r} is not a number or a percentage")
    value = float(m.group(1))
    if value < 0:
        raise ValueError("tolerance must not be negative")
    return ("relative", value / 100) if m.group(2) else ("absolute", value)


def within(recorded: float, rerun: float, tol: tuple[str, float]) -> bool:
    kind, value = tol
    if kind == "relative":
        return math.isclose(recorded, rerun, rel_tol=value, abs_tol=1e-12)
    return abs(recorded - rerun) <= value + 1e-12


def show_tolerance(tol: tuple[str, float]) -> str:
    return f"{tol[1] * 100:g}%（相对）" if tol[0] == "relative" else f"{tol[1]:g}（绝对）"


def head(paper: Path) -> str:
    done = subprocess.run(["git", "-C", str(paper), "rev-parse", "HEAD"], capture_output=True, text=True)
    if done.returncode != 0:
        raise ValueError(f"{paper} has no commit yet")
    return done.stdout.strip()


def dirty(paper: Path) -> bool:
    done = subprocess.run(["git", "-C", str(paper), "status", "--porcelain", "--untracked-files=no"],
                          capture_output=True, text=True)
    return bool(done.stdout.strip())


def fmt(value) -> str:
    return "—" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


__all__ = ["dirty", "fmt", "head", "lower_is_better", "metric_from", "repo", "results", "results_path",
           "run_folder", "show_tolerance", "spec", "tolerance", "within"]

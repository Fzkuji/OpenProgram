"""Import runs from an experiment tracker into results.tsv: the runs the author already
made in Weights & Biases or MLflow join the experiment loop's ledger instead of being
typed in again. Plain code, no model calls.

Each imported row carries source (wandb:<id> or mlflow:<id>), so a run is imported once
however often the import runs. A run is discard unless the author marked it keep in the
tracker (a keep tag); a failed run is crash.
"""

import csv
import io
import json
import os
import re
from pathlib import Path

from auto_research import checks, stages
from auto_research.trail import Trail

from . import common

ID_COLUMNS = ("id", "run id", "run_id", "runid")
NAME_COLUMNS = ("name", "run name", "run_name", "display name", "tags.mlflow.runname")
COMMIT_COLUMNS = ("commit", "git commit", "git_commit", "git.commit", "tags.mlflow.source.git.commit",
                  "mlflow.source.git.commit", "source.git.commit")
SECONDS_COLUMNS = ("runtime", "_runtime", "duration", "summary._runtime", "seconds")
STATE_COLUMNS = ("state", "status")
TAG_COLUMNS = ("tags", "tag")
KEEP_COLUMNS = ("keep", "tags.keep", "config.keep")
FAILED = ("crashed", "failed", "killed", "4", "5")
TRUE = ("1", "true", "yes", "y", "keep")
MLFLOW_STATUS = {"1": "RUNNING", "2": "SCHEDULED", "3": "FINISHED", "4": "FAILED", "5": "KILLED"}


def import_tracking(paper_dir: str, source: str, out_dir: str = "") -> str:
    """Append the runs of a W&B or MLflow project to workflow/results.tsv.

    source is a CSV exported from the W&B runs table or the MLflow UI (or
    mlflow.search_runs), an mlruns/ folder, or wandb:<entity>/<project> to read through
    the wandb API (needs the wandb package and WANDB_API_KEY). Each run becomes one row:
    commit from the run's git commit, metric from the column named by experiment.md's
    Metric, status keep when the author tagged the run keep, crash when it failed, else
    discard, seconds from its runtime, description with its name and id, and source
    wandb:<id> or mlflow:<id> (the column is added when missing). A run whose source is
    already in results.tsv is skipped. PASS_WITH_NOTES when a run has no metric or a kept
    run's commit is not in the repository. report.md and the trail go to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/import_tracking).
    """
    paper = common.repo(paper_dir)
    metric = common.spec(paper, need=("Metric",))["Metric"]
    kind, runs = read_source(paper, source.strip(), metric)
    out = common.run_folder(paper, out_dir, "import_tracking")
    trail = Trail("import_tracking", {"paper_dir": str(paper), "source": source, "out_dir": out_dir}, {})
    trail.step("read", kind=kind, runs=len(runs))
    header, rows = common.results(paper)
    known = {r.get("source", "") for r in rows if r.get("source")}
    fresh = [r for r in runs if r["source"] not in known]
    skipped = len(runs) - len(fresh)
    notes = []
    for r in fresh:
        if r["metric"] == "" and r["status"] != "crash":
            r["status"] = "crash"
            notes.append(f"{r['source']}：没有 {metric}，记为 crash。")
        if r["status"] == "keep" and not checks.commit_exists(paper, r["commit"]):
            notes.append(f"{r['source']}：标了 keep，但提交 {r['commit'] or '（空）'} 不在仓库里。")
    write(paper, header, rows, fresh)
    trail.step("append", added=len(fresh), skipped=skipped, notes=len(notes))
    status = "PASS_WITH_NOTES" if notes else "PASS"
    (out / "report.md").write_text(report(status, kind, source, metric, fresh, skipped, notes), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", results=common.results_path(paper))
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run), "kind": kind,
                       "added": len(fresh), "skipped": skipped, "notes": notes,
                       "results": str(common.results_path(paper))}, ensure_ascii=False)


# Reading the tracker --------------------------------------------------------------------

def read_source(paper: Path, source: str, metric: str) -> tuple[str, list[dict]]:
    if not source:
        raise ValueError("source is empty: give a CSV export, an mlruns/ folder or wandb:<entity>/<project>")
    tag, _, rest = source.partition(":")
    forced = tag if tag in ("wandb", "mlflow") and rest else ""
    target = rest if forced else source
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = paper / path
    if forced == "wandb" and not path.exists():
        return "wandb", wandb_api(target, metric)
    if path.is_dir():
        if forced == "wandb":
            raise ValueError("a W&B source is a CSV export or wandb:<entity>/<project>")
        return "mlflow", mlruns(path, metric)
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        with io.StringIO(text, newline="") as f:
            reader = csv.DictReader(f)
            table = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
            columns = reader.fieldnames or []
        kind = forced or ("mlflow" if any(c.lower() in ("run id", "run_id") or c.startswith(("metrics.", "tags.mlflow"))
                                          for c in columns) else "wandb")
        return kind, [csv_run(kind, r, metric) for r in table]
    raise ValueError(f"source {source} is not a file, an mlruns/ folder or wandb:<entity>/<project>")


def _get(row: dict, names: tuple[str, ...]) -> str:
    lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if isinstance(v, str)}
    for n in names:
        if lowered.get(n):
            return lowered[n]
    return ""


def csv_run(kind: str, row: dict, metric: str) -> dict:
    run_id = _get(row, ID_COLUMNS) or _get(row, NAME_COLUMNS)
    if not run_id:
        raise ValueError(f"a row of the {kind} export has no run id or name")
    m = metric.lower()
    value = _get(row, (m, f"metrics.{m}", f"summary.{m}", f"summary/{m}", f"{m} (summary)"))
    tags = [t.strip().lower() for t in re.split(r"[,;]", _get(row, TAG_COLUMNS)) if t.strip()]
    keep = "keep" in tags or _get(row, KEEP_COLUMNS).lower() in TRUE
    state = _get(row, STATE_COLUMNS).lower()
    return make_run(kind, run_id, _get(row, NAME_COLUMNS), value, _get(row, COMMIT_COLUMNS),
                    seconds(_get(row, SECONDS_COLUMNS)), keep, state in FAILED)


def make_run(kind: str, run_id: str, name: str, value, commit: str, secs, keep: bool, failed: bool) -> dict:
    number = common.metric_from(f"x: {value}", "x") if str(value).strip() else None
    status = "crash" if failed else "keep" if keep else "discard"
    described = f"{kind} run {name} ({run_id})" if name and name != run_id else f"{kind} run {run_id}"
    return {"commit": commit[:40], "metric": "" if number is None else f"{number:g}", "status": status,
            "seconds": "" if secs is None else f"{secs:g}", "description": clean(described),
            "source": f"{kind}:{run_id}"}


def clean(text: str) -> str:
    return " ".join(str(text).replace("\t", " ").split())


def seconds(text: str) -> float | None:
    """Runtime as W&B (seconds) or MLflow ("1.5min", "12.3s", "2h", "00:01:30") write it."""
    t = text.strip().lower()
    if not t:
        return None
    if re.fullmatch(r"\d+:\d{2}(:\d{2})?", t):
        parts = [int(p) for p in t.split(":")]
        return float(sum(p * 60 ** i for i, p in enumerate(reversed(parts))))
    m = re.fullmatch(rf"({common.NUMBER})\s*(ms|s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hours|d|days)?", t)
    if not m:
        return None
    unit = m.group(2) or "s"
    scale = {"ms": 0.001, "m": 60, "min": 60, "mins": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600,
             "hours": 3600, "d": 86400, "days": 86400}.get(unit, 1)
    return round(float(m.group(1)) * scale, 1)


def mlruns(root: Path, metric: str) -> list[dict]:
    """The runs of an mlruns/ folder: <experiment>/<run>/{meta.yaml, metrics/, tags/}."""
    if not root.is_dir():
        raise ValueError(f"{root} is not an mlruns folder")
    runs = []
    for meta in sorted(root.glob("*/*/meta.yaml")):
        run = meta.parent
        if run.parent.name in (".trash", "models"):
            continue
        fields = dict(re.findall(r"^(\w+):\s*['\"]?(.*?)['\"]?\s*$", meta.read_text(encoding="utf-8"), re.M))
        run_id = fields.get("run_id") or fields.get("run_uuid") or run.name
        if fields.get("lifecycle_stage") == "deleted":
            continue
        value = ""
        points = run / "metrics" / metric
        if points.is_file():
            last = [line.split() for line in points.read_text(encoding="utf-8").splitlines() if line.strip()]
            value = last[-1][1] if last and len(last[-1]) > 1 else ""
        tag = lambda name: _read(run / "tags" / name)     # noqa: E731
        secs = None
        if fields.get("end_time", "").isdigit() and fields.get("start_time", "").isdigit():
            secs = round((int(fields["end_time"]) - int(fields["start_time"])) / 1000, 1)
        status = MLFLOW_STATUS.get(fields.get("status", ""), fields.get("status", "")).lower()
        runs.append(make_run("mlflow", run_id, fields.get("run_name") or tag("mlflow.runName"), value,
                             tag("mlflow.source.git.commit"), secs, tag("keep").lower() in TRUE,
                             status in ("failed", "killed")))
    if not runs:
        raise ValueError(f"{root} has no MLflow runs")
    return runs


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def wandb_api(project: str, metric: str) -> list[dict]:
    """Runs of entity/project through the wandb public API."""
    try:
        import wandb
    except ImportError as e:
        raise ValueError("reading wandb:<entity>/<project> needs the wandb package; or export the runs "
                         "table as CSV and give its path") from e
    if not os.environ.get("WANDB_API_KEY"):
        raise ValueError("reading wandb:<entity>/<project> needs WANDB_API_KEY in the environment")
    runs = []
    for r in wandb.Api().runs(project):
        summary = dict(getattr(r, "summary", {}) or {})
        metadata = getattr(r, "metadata", None) or {}
        commit = getattr(r, "commit", None) or (metadata.get("git") or {}).get("commit") or ""
        tags = [str(t).lower() for t in (getattr(r, "tags", None) or [])]
        secs = summary.get("_runtime")
        runs.append(make_run("wandb", r.id, getattr(r, "name", "") or "", summary.get(metric, ""), str(commit),
                             float(secs) if isinstance(secs, (int, float)) else None, "keep" in tags,
                             str(getattr(r, "state", "")).lower() in FAILED))
    return runs


# Writing results.tsv --------------------------------------------------------------------

def write(paper: Path, header: list[str], rows: list[dict], fresh: list[dict]) -> None:
    if not fresh:
        return
    path = common.results_path(paper)
    if not path.is_file():
        stages.scaffold(paper, "results.tsv", [])
    columns = list(header) + [c for c in (*checks.RESULT_COLUMNS, "source") if c not in header]
    if columns == header and path.read_text(encoding="utf-8").endswith("\n"):
        with path.open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=columns, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore").writerows(fresh)
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows + fresh)


# The report -----------------------------------------------------------------------------

def report(status: str, kind: str, source: str, metric: str, fresh: list[dict], skipped: int,
           notes: list[str]) -> str:
    lines = ["# 导入实验记录", "", f"结论：**{status}**，从 {kind}（`{source}`）导入 {len(fresh)} 次运行，"
             f"跳过已导入的 {skipped} 次。指标取 {metric}。", ""]
    if fresh:
        lines += ["| 来源 | 提交 | 指标 | 状态 | 用时 | 说明 |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['source']} | `{r['commit'][:12] or '—'}` | {r['metric'] or '—'} | {r['status']} | "
                  f"{r['seconds'] or '—'} | {r['description']} |" for r in fresh]
    if notes:
        lines += ["", "## 要处理的", "", *[f"- {n}" for n in notes]]
    lines += ["", "在 tracker 里给要保留的运行打上 keep 标签再导入，状态才是 keep；已导入的运行不会重复导入，"
                  "改状态请直接改 results.tsv。", ""]
    return "\n".join(lines)


__all__ = ["import_tracking"]

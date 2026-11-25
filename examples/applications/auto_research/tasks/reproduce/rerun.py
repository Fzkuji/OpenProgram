"""The clean rerun: what Code Ocean's reproducible run and PaperBench's reproduction step
do, on the paper's own repository. The committed code is cloned into a temporary folder,
the environment is built from what the repository declares, and the kept runs of
results.tsv are run again to see whether their metric comes back. Plain code, no model
calls; the author's working tree is never touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research.trail import Trail

from . import common

INSTALL_TIMEOUT = 1800
RUN_CHOICES = ("best", "all")


def clean_rerun(paper_dir: str, runs: str = "best", tolerance: str = "", timeout: str = "3600",
                out_dir: str = "") -> str:
    """Run the kept experiments of results.tsv again from a clean clone and compare metrics.

    paper_dir is the project's Git repository. Its HEAD is cloned into a temporary folder
    (the working tree is never touched) and a fresh environment is built from what the
    repository declares: requirements.txt or pyproject.toml into a new virtualenv (uv when
    installed, else python -m venv and pip); environment.yml only when conda is installed.
    runs is "best" (the best kept run by experiment.md's Direction), "all" (every kept
    run) or a comma list of commits. Each run checks out its commit in the clone, runs
    experiment.md's Command with timeout seconds and reads `<Metric>: <number>` from the
    output. tolerance is "1%" (relative) or "0.002" (absolute); empty uses experiment.md's
    Tolerance field, else 1%. FAIL when a run does not reproduce, crashes or times out;
    PASS_WITH_NOTES when the environment could not be fully built but every run
    reproduced. Logs, report.md, rerun.json and the trail go to out_dir/<timestamp>/
    (default <paper_dir>/drafts/clean_rerun).
    """
    paper = common.repo(paper_dir)
    values = common.spec(paper)
    tol = common.tolerance(tolerance, values)
    if not str(timeout).strip().isdigit() or int(timeout) <= 0:
        raise ValueError("timeout must be a whole number of seconds")
    limit = int(timeout)
    chosen = choose(common.results(paper)[1], runs, common.lower_is_better(values))
    out = common.run_folder(paper, out_dir, "clean_rerun")
    logs = out / "logs"
    logs.mkdir(exist_ok=True)
    trail = Trail("clean_rerun", {"paper_dir": str(paper), "runs": runs, "tolerance": tolerance,
                                  "timeout": limit, "out_dir": out_dir}, {})
    trail.step("choose", runs=[r["commit"] for r in chosen], tolerance=common.show_tolerance(tol))
    work = Path(tempfile.mkdtemp(prefix="clean_rerun-"))
    try:
        clone = work / "repo"
        done = subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(paper), str(clone)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise ValueError(f"cannot clone {paper}: {done.stderr.strip()}")
        trail.step("clone", head=common.head(paper), dirty=common.dirty(paper))
        check_cancelled()
        env, environment = build_env(clone, work, logs)
        trail.step("environment", **environment)
        checked = []
        for row in chosen:
            check_cancelled()
            checked.append(rerun_one(clone, row, values, tol, limit, env, logs))
        trail.step("rerun", results=[{k: r[k] for k in ("commit", "status", "rerun")} for r in checked])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in checked if r["status"] != "reproduced"]
    status = "FAIL" if failed else "PASS_WITH_NOTES" if not environment["complete"] else "PASS"
    data = {"status": status, "metric": values["Metric"], "command": values["Command"],
            "tolerance": common.show_tolerance(tol), "environment": environment, "runs": checked,
            "dirty": common.dirty(paper)}
    (out / "rerun.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report(data), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", rerun=out / "rerun.json", logs=logs)
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "rerun": str(out / "rerun.json"), "reproduced": len(checked) - len(failed),
                       "failed": len(failed), "environment": environment["kind"],
                       "runs": [{k: r[k] for k in ("commit", "recorded", "rerun", "diff", "status", "log")}
                                for r in checked]}, ensure_ascii=False)


def choose(rows: list[dict], runs: str, lower: bool) -> list[dict]:
    """The rows to rerun: the best kept, every kept, or the named commits."""
    kept = [r for r in rows if r.get("status") == "keep"]
    wanted = (runs or "best").strip()
    if wanted == "best":
        scored = [r for r in kept if _num(r.get("metric", "")) is not None]
        if not scored:
            raise ValueError("results.tsv has no kept run with a metric to rerun")
        pick = min if lower else max
        return [pick(scored, key=lambda r: _num(r["metric"]))]
    if wanted == "all":
        if not kept:
            raise ValueError("results.tsv has no kept run to rerun")
        return kept
    picked = []
    for commit in [c.strip() for c in wanted.split(",") if c.strip()]:
        match = [r for r in rows if r.get("commit") and (r["commit"].startswith(commit) or commit.startswith(r["commit"]))]
        if not match:
            raise ValueError(f"commit {commit} is not a run of results.tsv")
        picked.append(match[-1])
    if not picked:
        raise ValueError(f"runs must be {', '.join(RUN_CHOICES)} or a comma list of commits")
    return picked


def _num(value: str) -> float | None:
    try:
        return float(value.replace("%", "").strip())
    except ValueError:
        return None


# The environment ----------------------------------------------------------------------

def requirements(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def build_env(clone: Path, work: Path, logs: Path) -> tuple[dict, dict]:
    """The process environment the runs use and what was built: kind, declared files,
    complete (False when something declared could not be installed), notes."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    declared = [n for n in ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml", "environment.yaml")
                if (clone / n).is_file()]
    info = {"kind": "none", "declared": declared, "complete": True, "notes": [], "tool": ""}
    log = logs / "environment.log"
    conda_file = next((clone / n for n in ("environment.yml", "environment.yaml") if (clone / n).is_file()), None)
    pip_targets = [n for n in ("requirements.txt", "pyproject.toml", "setup.py") if n in declared]

    if conda_file is not None and shutil.which("conda"):
        prefix = work / "conda-env"
        ok = _install(["conda", "env", "create", "--quiet", "-p", str(prefix), "-f", str(conda_file)], clone, log)
        info.update(kind="conda", tool="conda")
        if ok:
            return _activate(env, prefix), info
        info["complete"] = False
        info["notes"].append(f"conda 按 {conda_file.name} 建环境失败，见 {log.name}；改用 virtualenv。")
    elif conda_file is not None:
        info["complete"] = False
        info["notes"].append(f"仓库声明了 {conda_file.name}，但本机没有 conda，无法按它建环境；"
                             "改用 virtualenv，只装 pip 能装的部分。")

    if not declared:
        info["complete"] = False
        info["notes"].append("仓库没有声明环境（requirements.txt、pyproject.toml、environment.yml 都没有），"
                             "用一个空的 virtualenv 跑；别人照着仓库重建不出你的环境。")
    venv = work / "venv"
    uv = shutil.which("uv")
    if uv:
        ok = _install([uv, "venv", "--quiet", "--python", sys.executable, str(venv)], clone, log)
        info["tool"] = info["tool"] or "uv"
    else:
        ok = False
    if not ok:
        ok = _install([sys.executable, "-m", "venv", str(venv)], clone, log)
        info["tool"] = "venv"
    if not ok:
        info.update(kind="system", complete=False)
        info["notes"].append(f"建不了 virtualenv，见 {log.name}；用系统 Python 跑。")
        return env, info
    info["kind"] = "venv"
    python = venv / "bin" / "python"
    for target in pip_targets:
        if target == "requirements.txt":
            if not requirements(clone / target):
                continue
            args = ["-r", target]
        elif target == "setup.py" and "pyproject.toml" in pip_targets:
            continue
        else:
            args = ["-e", "."]
        command = ([uv, "pip", "install", "--quiet", "--python", str(python), *args] if uv and info["tool"] == "uv"
                   else [str(python), "-m", "pip", "install", "--quiet", *args])
        if not _install(command, clone, log):
            info["complete"] = False
            info["notes"].append(f"按 {target} 安装依赖失败，见 {log.name}；仍按装好的部分运行。")
    return _activate(env, venv), info


def _install(command: list[str], cwd: Path, log: Path) -> bool:
    with log.open("a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(command)}\n")
        try:
            done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            f.write(f"{e}\n")
            return False
        f.write(done.stdout + done.stderr + f"[exit {done.returncode}]\n")
    return done.returncode == 0


def _activate(env: dict, prefix: Path) -> dict:
    env = dict(env, VIRTUAL_ENV=str(prefix))
    env["PATH"] = str(prefix / "bin") + os.pathsep + env.get("PATH", "")
    return env


# One run --------------------------------------------------------------------------------

def rerun_one(clone: Path, row: dict, values: dict, tol: tuple[str, float], limit: int, env: dict,
              logs: Path) -> dict:
    commit = row["commit"]
    log = logs / f"{commit[:12] or 'run'}.log"
    result = {"commit": commit, "description": row.get("description", ""), "recorded": _num(row.get("metric", "")),
              "rerun": None, "diff": None, "seconds": None, "status": "crash", "note": "", "log": str(log)}
    checkout = subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", "--force", commit],
                              capture_output=True, text=True)
    if checkout.returncode != 0:
        result["note"] = f"克隆里检出不了 {commit}：{checkout.stderr.strip()}"
        log.write_text(result["note"] + "\n", encoding="utf-8")
        return result
    subprocess.run(["git", "-C", str(clone), "clean", "-fdxq"], capture_output=True)
    start = time.monotonic()
    try:
        done = subprocess.run(values["Command"], shell=True, cwd=clone, env=env, capture_output=True, text=True,
                              timeout=limit)
        output, code = done.stdout + ("\n" + done.stderr if done.stderr else ""), done.returncode
    except subprocess.TimeoutExpired as e:
        output = _text(e.stdout) + _text(e.stderr)
        code = None
    result["seconds"] = round(time.monotonic() - start, 1)
    log.write_text(f"$ {values['Command']}\n# commit {commit}\n{output}\n[exit {code}]\n", encoding="utf-8")
    if code is None:
        result.update(status="timeout", note=f"超过 {limit} 秒没有跑完。")
        return result
    rerun = common.metric_from(output, values["Metric"])
    result["rerun"] = rerun
    if code != 0:
        result["note"] = f"命令退出码 {code}。"
        return result
    if rerun is None:
        result.update(status="no_metric", note=f"输出里没有 `{values['Metric']}: <数字>`。")
        return result
    if result["recorded"] is None:
        result.update(status="no_record", note="results.tsv 里这次运行没有数字指标，无从比较。")
        return result
    result["diff"] = round(rerun - result["recorded"], 10)
    result["status"] = "reproduced" if common.within(result["recorded"], rerun, tol) else "mismatch"
    if result["status"] == "mismatch":
        result["note"] = "差距超出容差。"
    return result


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


# The report -----------------------------------------------------------------------------

LABELS = {"reproduced": "复现", "mismatch": "不一致", "crash": "崩溃", "timeout": "超时", "no_metric": "没有指标",
          "no_record": "无记录"}
KINDS = {"venv": "virtualenv", "conda": "conda", "system": "系统 Python", "none": "未建"}


def report(data: dict) -> str:
    verdict = {"PASS": "全部复现。", "PASS_WITH_NOTES": "全部复现，但环境没有完全按仓库声明建好。",
               "FAIL": "有运行没能复现。"}[data["status"]]
    env = data["environment"]
    lines = ["# 干净重跑", "", f"结论：**{data['status']}**，{verdict}", "",
             f"- 命令：`{data['command']}`", f"- 指标：{data['metric']}，容差 {data['tolerance']}",
             f"- 环境：{KINDS.get(env['kind'], env['kind'])}（{env['tool'] or '—'}），"
             f"声明文件：{'、'.join(env['declared']) or '无'}"]
    if data["dirty"]:
        lines.append("- 工作区有未提交的改动：重跑只用已提交的代码，这些改动不在里面。")
    lines += [f"- {n}" for n in env["notes"]]
    lines += ["", "| 提交 | 记录值 | 重跑值 | 差 | 结果 | 用时 | 日志 |", "|---|---|---|---|---|---|---|"]
    for r in data["runs"]:
        lines.append(f"| `{r['commit'][:12]}` | {common.fmt(r['recorded'])} | {common.fmt(r['rerun'])} | "
                     f"{common.fmt(r['diff'])} | {LABELS.get(r['status'], r['status'])} | "
                     f"{common.fmt(r['seconds'])}s | `{Path(r['log']).name}` |")
    notes = [f"- `{r['commit'][:12]}`：{r['note']}" for r in data["runs"] if r["note"]]
    if notes:
        lines += ["", "## 说明", "", *notes]
    lines += ["", "环境按 HEAD 声明的文件建一次，所有运行共用；日志在 logs/ 下。", ""]
    return "\n".join(lines)


__all__ = ["clean_rerun"]

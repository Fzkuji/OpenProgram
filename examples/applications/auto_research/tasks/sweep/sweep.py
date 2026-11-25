"""Sweep the experiment's hyperparameters: run experiment.md's Command once per trial
with parameters a sampler proposes, record every trial in results.tsv and say which
setting is best and which parameter matters.

Steps: spec (code: experiment.md's Command, Metric, Direction, Budget and Edit; the
reference is the best kept run of results.tsv, or HEAD when none is kept), delivery
(code: how each parameter reaches the program; a `--name` option of the script's
argparse, found in its syntax tree, a key of a JSON file of Edit, or the script reading
the AR_PARAMS file; a parameter no route reaches raises), trials (one worktree on a
new branch sweep/<date> from the reference; per trial the sampler proposes parameters,
the JSON configs are rewritten, the trial is committed and Command runs with the CLI
options and AR_PARAMS set, within the time left), record (one results.tsv row per trial:
keep when it improves on the branch's best so far, which starts at the reference,
discard otherwise, crash without a metric), report (best parameters, the trials, a
variance-based importance per parameter, a recommendation). The worktree is removed;
the branch stays, as results.tsv names its commits.
Learned from Ax / BoTorch (Bayesian optimization planning the next trial) and Optuna
(TPE with ask-and-tell, parameter importance).
"""

import ast
import json
import os
import re
import shlex
import tempfile
import time
from datetime import date
from pathlib import Path

from auto_research import checks
from auto_research.trail import Trail

from ..discovery.ablation import IDENTITY, branches, budget_seconds, cleanup, git, reference, show_file
from ..discovery.common import out_folder, run
from ..reproduce import common as rc
from .space import at_edge, describe, importance, parse_space, sampler, show

DEFAULT_TRIALS = 12
MOST_TRIALS = 200
DEFAULT_BUDGET = 300
SHELL = ("&&", "||", ";", "|", ">", ">>", "<", "&")


def hparam_sweep(paper_dir: str, space: str, trials: str = "12", budget: str = "300", out_dir: str = "") -> str:
    """Sweep hyperparameters of the experiment and record every trial in results.tsv.

    paper_dir is the project's Git repository with workflow/experiment.md (Command,
    Metric, Direction; Budget limits one trial; JSON files in Edit may hold the
    parameters). space is "name=low..high [log]; name=a,b,c" (a float or integer range,
    log scale with log, or choices). trials (default 12, 1 to 200) is the number of
    trials; budget (default 300) the seconds the whole sweep may take ("10 min" works
    too). Each parameter reaches the program by every route it has: the `--name` option
    when the Command's script declares it with argparse, the key in a JSON file of Edit
    (rewritten in the trial's commit), and the JSON file named by the AR_PARAMS
    environment variable, which is always set. A parameter with no route other than
    AR_PARAMS that the script never reads raises ValueError. Trials run on a new branch
    sweep/<date> from the best kept run (or HEAD); Optuna's TPE proposes them when
    optuna is installed, else a Latin hypercube then local search around the best. Each is
    a results.tsv row: keep when it beats the branch's best so far (starting at the
    reference), else discard, crash without a metric. The report gives the best
    parameters, the trials, an importance estimate and a recommendation, in
    out_dir/<timestamp>/ (default <paper_dir>/drafts/hparam_sweep).
    """
    paper = rc.repo(paper_dir)
    params = parse_space(space)
    n_trials = parse_trials(trials)
    total = parse_budget(budget)
    values = rc.spec(paper, ("Command", "Metric"))
    lower = rc.lower_is_better(values)
    per_trial, budget_note = budget_seconds(values.get("Budget", ""))
    ref = reference(paper, lower)
    routes, configs, script = delivery(paper, ref["commit"], values, params)
    out = out_folder(paper, out_dir, "hparam_sweep")
    (out / "logs").mkdir()
    (out / "params").mkdir()
    trail = Trail("hparam_sweep", {"paper_dir": str(paper), "space": space, "trials": n_trials, "budget": total,
                                   "out_dir": out_dir}, {})
    search = sampler(params, lower, n_trials)
    trail.step("spec", command=values["Command"], metric=values["Metric"], direction="lower" if lower else "higher",
               per_trial=per_trial, reference=ref["commit"][:12], reference_metric=ref["metric"], script=script,
               routes=routes, configs=sorted(configs), sampler=search.name)
    branch = branch_name(paper)
    work = Path(tempfile.mkdtemp(prefix="sweep-"))
    tree = work / "tree"
    rows: list[dict] = []
    stopped = ""
    start = time.monotonic()
    try:
        git(paper, "worktree", "add", "-q", "-b", branch, str(tree), ref["commit"])
        best = ref["metric"] if ref["kept"] else None
        for k in range(1, n_trials + 1):
            left = total - (time.monotonic() - start)
            if left < 1:
                stopped = f"用完了 {total:g} 秒的总预算，只跑了 {k - 1} 次（要求 {n_trials} 次）。"
                break
            proposal, handle = search.ask()
            row = run_trial(paper, tree, k, proposal, routes, configs, values, min(per_trial, left), out)
            search.tell(handle, row["metric"])
            if row["metric"] is None:
                row["status"] = "crash"
            elif best is None or (row["metric"] < best if lower else row["metric"] > best):
                row["status"], best = "keep", row["metric"]
            else:
                row["status"] = "discard"
            rows.append(row)
            trail.step(f"trial {k}", params=proposal, commit=row["commit"], metric=row["metric"],
                       status=row["status"], outcome=row["outcome"], seconds=row["seconds"])
    finally:
        cleanup(paper, work)
    append_rows(paper, rows, branch, ref)
    return save(paper, out, values, lower, params, rows, ref, branch, search.name, routes, script, per_trial,
                budget_note, stopped, total, trail)


# Plain code: no model calls.

def parse_trials(text) -> int:
    t = str(text).strip()
    if not t:
        return DEFAULT_TRIALS
    if not t.isdigit() or not 1 <= int(t) <= MOST_TRIALS:
        raise ValueError(f"trials must be a whole number from 1 to {MOST_TRIALS}")
    return int(t)


def parse_budget(text) -> float:
    t = str(text).strip()
    if not t:
        return float(DEFAULT_BUDGET)
    seconds, note = budget_seconds(t)
    if note:
        raise ValueError(f"budget {t!r} is not a time: write seconds (300) or a unit (10 min)")
    return float(seconds)


def command_script(paper: Path, command: str) -> tuple[str, bool]:
    """The .py file the Command runs (relative to the repository) and whether the
    Command is one simple command, so options can be appended to it."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "", False
    simple = not any(t in SHELL or any(op in t for op in ("&&", "||", "|", ";")) for t in tokens)
    for i, t in enumerate(tokens):
        if t.endswith(".py") and (paper / t).is_file():
            return t, simple
        if t == "-m" and i + 1 < len(tokens):
            path = tokens[i + 1].replace(".", "/") + ".py"
            if (paper / path).is_file():
                return path, simple
    return "", simple


def argparse_options(source: str) -> dict[str, str]:
    """{dest: option} of every `add_argument("--name", ...)` call in the source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    found = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        options = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                   and a.value.startswith("--")]
        if not options:
            continue
        dest = next((k.value.value for k in node.keywords if k.arg == "dest" and isinstance(k.value, ast.Constant)),
                    options[0][2:].replace("-", "_"))
        found[dest] = options[0]
    return found


def lookup(data: dict, dotted: str) -> bool:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def assign(data: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def delivery(paper: Path, commit: str, values: dict, space: list[dict]) -> tuple[dict, dict, str]:
    """({param: [routes]}, {json file: its data at the reference}, the script). Routes
    are "cli --opt", "config <file>" and "env AR_PARAMS"."""
    script, simple = command_script(paper, values["Command"])
    source = (show_file(paper, commit, script) or "") if script else ""
    options = argparse_options(source) if simple else {}
    reads_env = "AR_PARAMS" in source
    configs = {}
    for name in [n.strip() for n in values.get("Edit", "").split(",") if n.strip().endswith(".json")]:
        text = show_file(paper, commit, name)
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            configs[name] = data
    routes, lost = {}, []
    for p in space:
        name = p["name"]
        ways = [f"cli {options[d]}" for d in (name, name.replace("-", "_")) if d in options][:1]
        ways += [f"config {f}" for f, data in configs.items() if lookup(data, name)]
        if reads_env:
            ways.append("env AR_PARAMS")
        if not ways:
            lost.append(name)
        routes[name] = ways
    if lost:
        where = f"{script} has no argparse option" if script else "the Command runs no .py script found here, so no option"
        files = ", ".join(configs) or "no JSON file in Edit"
        raise ValueError(f"{', '.join(lost)} cannot reach the program: {where} of that name, it is not a key of "
                         f"{files}, and the script does not read AR_PARAMS")
    return routes, configs, script


def branch_name(paper: Path) -> str:
    taken = branches(paper)
    base = f"sweep/{date.today().isoformat()}"
    name, k = base, 2
    while name in taken:
        name, k = f"{base}-{k}", k + 1
    return name


def run_trial(paper: Path, tree: Path, k: int, params: dict, routes: dict, configs: dict, values: dict,
              limit: float, out: Path) -> dict:
    """Write the configs, commit, run Command with the options and AR_PARAMS; the row."""
    for name, data in configs.items():
        new = json.loads(json.dumps(data))
        for p, v in params.items():
            if f"config {name}" in routes[p]:
                assign(new, p, v)
        (tree / name).write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    if configs:
        git(tree, "add", "--", *configs)
    who = [] if git(tree, "config", "user.email", check=False) else IDENTITY
    git(tree, *who, "commit", "-q", "--allow-empty", "-m", f"sweep trial {k}: {describe(params)}")
    commit = git(tree, "rev-parse", "--short=12", "HEAD")
    params_file = out / "params" / f"trial-{k}.json"
    params_file.write_text(json.dumps(params, indent=2), encoding="utf-8")
    extra = [f"{r.split(' ', 1)[1]} {shlex.quote(show(params[p]))}" for p, rs in routes.items() for r in rs
             if r.startswith("cli ")]
    command = " ".join([values["Command"], *extra])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", AR_PARAMS=str(params_file))
    done = run(command, tree, limit, env=env, shell=True)
    log = out / "logs" / f"trial-{k}.log"
    log.write_text(f"$ AR_PARAMS={params_file} {command}\n# {commit}\n{done['output']}\n[exit {done['code']}]\n",
                   encoding="utf-8")
    metric = rc.metric_from(done["output"], values["Metric"])
    outcome = ("timeout" if done["code"] is None else "crash" if done["code"] != 0 else
               "no_metric" if metric is None else "ok")
    return {"trial": k, "params": params, "commit": commit, "metric": metric if outcome == "ok" else None,
            "outcome": outcome, "seconds": done["seconds"], "limit": round(limit, 1), "log": str(log),
            "command": command, "status": ""}


def append_rows(paper: Path, rows: list[dict], branch: str, ref: dict) -> None:
    """One results.tsv row per trial; the branch and parent columns are added when missing."""
    if not rows:
        return
    path = rc.results_path(paper)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text.strip():
        text = "\t".join(checks.RESULT_COLUMNS) + "\n"
    lines = text.splitlines()
    header = lines[0].split("\t")
    extra = [c for c in ("branch", "parent") if c not in header]
    if extra:
        lines = [lines[0] + "\t" + "\t".join(extra)] + [x + "\t" * len(extra) if x.strip() else x for x in lines[1:]]
        header += extra
    parent = ref["short"] if ref["kept"] else ""
    for r in rows:
        value = {"commit": r["commit"], "metric": "" if r["metric"] is None else f"{r['metric']:g}",
                 "status": r["status"], "seconds": f"{r['seconds']:.1f}", "branch": branch, "parent": parent,
                 "description": f"sweep: {describe(r['params'])}" + ("" if r["outcome"] == "ok" else f" ({r['outcome']})")}
        lines.append("\t".join(value.get(c, "").replace("\t", " ") for c in header))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recommend(space: list[dict], rows: list[dict], ref: dict, lower: bool, ranked: list[dict], branch: str) -> list[str]:
    ok = [r for r in rows if r["metric"] is not None]
    if not ok:
        return ["没有一次试验拿到指标：先看 logs/ 里的日志，让 Command 在参照提交上跑通再扫。"]
    best = (min if lower else max)(ok, key=lambda r: r["metric"])
    out = []
    if ref["metric"] is not None and (best["metric"] < ref["metric"] if lower else best["metric"] > ref["metric"]):
        out.append(f"最好的组合 {describe(best['params'])} 比参照（{ref['metric']:g}）好，指标 {best['metric']:g}。"
                   f"要采用就把 `{best['commit']}`（分支 {branch}）合回主线，并在 results.tsv 加一行 merge。")
    elif ref["metric"] is not None:
        out.append(f"没有组合超过参照的 {ref['metric']:g}；最好的是 {describe(best['params'])}（{best['metric']:g}）。"
                   "参照可能来自别的设置，先确认它在参照提交上能复现（clean_rerun）。")
    else:
        out.append(f"最好的组合是 {describe(best['params'])}，指标 {best['metric']:g}。")
    for p in space:
        edge = at_edge(p, best["params"][p["name"]])
        if edge:
            out.append(f"{p['name']} 的最优值 {show(best['params'][p['name']])} 落在区间{'下' if edge == 'low' else '上'}沿，"
                       f"最优点可能在区间外，把范围往{'小' if edge == 'low' else '大'}扩一些再扫。")
    top = [x for x in ranked if x["eta2"] is not None]
    if top and top[0]["eta2"] >= 0.5:
        out.append(f"指标的差别主要来自 {top[0]['name']}（解释 {top[0]['eta2']:.0%} 的方差），下一轮只扫它，其余固定在最优值。")
    out.append("每个组合只跑了一次（一个种子）：写进论文前，用几个种子复跑最好的和参照，再做显著性检验（claim_stats）。")
    return out


def status_of(rows: list[dict], stopped: str, budget_note: str) -> str:
    if not any(r["metric"] is not None for r in rows):
        return "FAIL"
    if stopped or budget_note or any(r["metric"] is None for r in rows):
        return "PASS_WITH_NOTES"
    return "PASS"


def save(paper: Path, out: Path, values: dict, lower: bool, space: list[dict], rows: list[dict], ref: dict,
         branch: str, sampler_name: str, routes: dict, script: str, per_trial: int, budget_note: str, stopped: str,
         total: float, trail: Trail) -> str:
    ok = [r for r in rows if r["metric"] is not None]
    best = (min if lower else max)(ok, key=lambda r: r["metric"]) if ok else None
    ranked = importance(space, rows, lower)
    status = status_of(rows, stopped, budget_note)
    record = {"status": status, "metric": values["Metric"], "direction": "lower" if lower else "higher",
              "command": values["Command"], "script": script, "routes": routes, "space": space, "sampler": sampler_name,
              "branch": branch, "budget": total, "per_trial": per_trial, "stopped": stopped, "budget_note": budget_note,
              "reference": {k: ref[k] for k in ("commit", "short", "metric", "kept", "description")},
              "trials": rows, "best": best, "importance": ranked,
              "recommendation": recommend(space, rows, ref, lower, ranked, branch)}
    (out / "sweep.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(record), encoding="utf-8")
    trail.step("report", status=status, best=best["params"] if best else None, metric=best["metric"] if best else None,
               importance={x["name"]: x["eta2"] for x in ranked})
    run_file = trail.save(out, report=out / "report.md", json=out / "sweep.json", results=rc.results_path(paper))
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "sweep.json"),
                       "run": str(run_file), "branch": branch, "sampler": sampler_name, "trials": len(rows),
                       "kept": sum(r["status"] == "keep" for r in rows),
                       "crashed": sum(r["status"] == "crash" for r in rows),
                       "best": {"params": best["params"], "metric": best["metric"], "commit": best["commit"]}
                       if best else None,
                       "importance": {x["name"]: x["eta2"] for x in ranked}}, ensure_ascii=False)


OUTCOMES = {"ok": "", "crash": "崩溃", "timeout": "超时", "no_metric": "没有指标"}
STATUS = {"keep": "保留", "discard": "丢弃", "crash": "崩溃"}


def render(r: dict) -> str:
    ref, best = r["reference"], r["best"]
    names = [p["name"] for p in r["space"]]
    where = "results.tsv 里最好的保留运行" if ref["kept"] else "HEAD（results.tsv 没有保留运行）"
    lines = ["# 超参数搜索", "", f"结论：**{r['status']}**", "",
             f"- 指标：{r['metric']}（{'越低越好' if r['direction'] == 'lower' else '越高越好'}）；命令：`{r['command']}`",
             f"- 参照：{where} `{ref['commit'][:12]}`，{r['metric']} = {rc.fmt(ref['metric'])}",
             f"- 搜索：{r['sampler']}，{len(r['trials'])} 次试验，分支 `{r['branch']}`；总预算 {r['budget']:g} 秒，"
             f"每次最多 {r['per_trial']} 秒",
             "- 参数怎么传进程序：" + "；".join(f"{k} → {'、'.join(v)}" for k, v in r["routes"].items())]
    lines += [f"- 注意：{n}" for n in (r["stopped"], r["budget_note"]) if n]
    lines += ["", "## 最好的组合", ""]
    if best:
        lines += [f"`{describe(best['params'])}` → {r['metric']} = {best['metric']:g}（试验 {best['trial']}，提交 "
                  f"`{best['commit']}`）"]
    else:
        lines.append("没有一次试验拿到指标。")
    lines += ["", "## 全部试验", "", "| # | " + " | ".join(names) + f" | {r['metric']} | 结果 | 秒 | 提交 |",
              "|---|" + "---|" * len(names) + "---|---|---|---|"]
    for t in r["trials"]:
        result = STATUS.get(t["status"], t["status"]) + (f"（{OUTCOMES[t['outcome']]}）" if OUTCOMES.get(t["outcome"]) else "")
        lines.append(f"| {t['trial']} | " + " | ".join(show(t["params"][n]) for n in names) +
                     f" | {rc.fmt(t['metric'])} | {result} | {t['seconds']:.1f} | `{t['commit']}` |")
    lines += ["", "## 参数重要性", "",
              "按每个参数的取值（连续参数按排名分低、中、高三档）分组，看指标的方差有多少落在组间（η²，0 到 1）。"
              "试验少、参数之间相关时只能粗看。", ""]
    if r["importance"]:
        lines += ["| 参数 | η² | 各档平均指标 | 最好的档 |", "|---|---|---|---|"]
        for x in r["importance"]:
            spread = "；".join(f"{k}: {v['mean']:.4g}（{v['n']} 次）" for k, v in x["levels"].items()) or "—"
            eta = "—" if x["eta2"] is None else f"{x['eta2']:.2f}"
            lines.append(f"| {x['name']} | {eta} | {spread} | {x['best'] or '—'} |")
    else:
        lines.append("拿到指标的试验不到 3 次，不做估计。")
    lines += ["", "## 建议", "", *[f"- {x}" for x in r["recommendation"]], "",
              f"每次试验都在 results.tsv 记了一行（分支 {r['branch']}）；工作树已清理，分支保留。日志在本目录 logs/，"
              "每次的参数在 params/。", ""]
    return "\n".join(lines)


__all__ = ["argparse_options", "command_script", "delivery", "hparam_sweep"]

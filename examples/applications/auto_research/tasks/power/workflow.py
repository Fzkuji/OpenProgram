"""How many seeds each claimed comparison needs before the runs, from the pilot runs.

Plain code, no model call. The per-seed numbers and the pairing of methods are read the
way claim_stats reads them (tasks/methods/claims.py); the pilot's paired (or two-group)
variance and the minimum effect worth detecting give the seeds a paired t test needs at
alpha with the target power. The seeds line for experiment.md is proposed as an exact
edit, never written.
"""

import json
import re
from pathlib import Path

from auto_research import checks, stages
from auto_research.trail import Trail

from ..methods import claims as cs
from ..methods import significance as sig
from ..submission.common import repo, run_folder
from . import stats

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*%?"
FROM_TO = re.compile(rf"\bfrom\s+({NUM})\s+to\s+({NUM})", re.I)
ARROW = re.compile(rf"({NUM})\s*(?:→|->|=>)\s*({NUM})")
VERSUS = re.compile(rf"({NUM})\s*(?:vs\.?|versus|against|compared (?:with|to))\s*({NUM})", re.I)
MATCH_LIMIT = 0.5          # summed relative distance for claim numbers to name a pair of groups
SEEDS_FIELD = re.compile(r"^Seeds:[ \t]*(.*)$", re.M)


def power_analysis(paper_dir: str, alpha: str = "0.05", power: str = "0.8", effect: str = "",
                   out_dir: str = "") -> str:
    """Say how many seeds each claimed comparison needs, before the runs.

    paper_dir is the project's Git repository. Every claim of workflow/claims.md gets
    its per-seed pilot numbers as claim_stats reads them (results.tsv rows grouped by
    branch or description without "seed N", evidence.csv method/value rows, CSV/TSV
    sources with method, seed and metric columns). The compared pair is the evidence
    compare column, else the two groups whose means match the claim's "from A to B"
    (or "A -> B", "A vs B") numbers, else the best group against each other. With two
    or more pilot seeds on each side, the standard deviation of the seed-wise
    differences (paired, same seeds) or of each group (unpaired) estimates the noise.
    The minimum effect is `effect` (absolute, or "5%" of the worse group's mean), else
    the difference the claim states, else the pilot's mean difference. The seeds needed
    are the smallest n whose two-sided t test at `alpha` reaches `power` (noncentral t,
    integrated numerically). PASS when every comparison already has enough seeds,
    PASS_WITH_NOTES when one is underpowered or has fewer than two pilot seeds (it asks
    for pilot runs), FAIL when no comparison is found. report.md, power.json and
    edits.json (a "Seeds: N" line for workflow/experiment.md in paper_proofread's edit
    format, not applied) go to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/power_analysis).
    """
    paper = repo(paper_dir)
    a = unit_number(alpha, "alpha")
    target = unit_number(power, "power")
    minimum = parse_effect(effect)
    text = stages.read(paper, "claims.md", None) if (stages.folder(paper) / "claims.md").is_file() else ""
    claims = dict(stages.CLAIM.findall(text))
    if "C1: The claim, stated as a result" in text:
        claims = {}
    spec = cs.experiment_fields(paper)
    higher = spec.get("Direction", "higher").strip().lower() != "lower"
    out = run_folder(paper, out_dir, "power_analysis")
    trail = Trail("power_analysis", {"paper_dir": str(paper), "alpha": a, "power": target, "effect": effect,
                                     "out_dir": out_dir}, {})
    evidence = cs.evidence_rows(paper)
    rows = cs.result_rows(paper)
    results = {}
    for cid, claim in claims.items():
        tables = with_unseeded(cs.claim_tables(paper, cid, evidence, rows, spec.get("Metric", "")), rows, cid,
                               evidence)
        results[cid] = plan_claim(cid, claim, tables, higher, a, target, minimum, cs.compare_of(
            [r for r in evidence if r.get("claim") == cid]))
    comparisons = [c for r in results.values() for c in r["comparisons"]]
    trail.step("power", claims=len(claims), comparisons=len(comparisons),
               needed={f"{c['claim']} {c['a']} > {c['b']}": c["needed"] for c in comparisons})
    if not comparisons:
        status = "FAIL"
    elif all(c["state"] == "enough" for c in comparisons):
        status = "PASS"
    else:
        status = "PASS_WITH_NOTES"
    seeds = max((c["needed"] for c in comparisons if c["needed"]), default=None)
    edits, current = seeds_edit(paper, seeds)
    record = {"status": status, "alpha": a, "power": target, "effect": effect, "higher_is_better": higher,
              "proposed_seeds": seeds, "current_seeds_line": current, "claims": results}
    (out / "power.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "edits.json").write_text(json.dumps({"root": str(paper), "edits": edits}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (out / "report.md").write_text(report(record, edits), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", json=out / "power.json", edits=out / "edits.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "power": str(out / "power.json"), "edits": str(out / "edits.json"), "seeds": seeds,
                       "comparisons": [{k: c[k] for k in ("claim", "a", "b", "have", "needed", "state")}
                                       for c in comparisons]}, ensure_ascii=False)


# Inputs -------------------------------------------------------------------------------------

def unit_number(value: str, name: str) -> float:
    try:
        x = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a number between 0 and 1") from None
    if not 0 < x < 1:
        raise ValueError(f"{name} must be a number between 0 and 1")
    return x


def parse_effect(effect: str) -> dict | None:
    """"" -> None; "0.002" -> absolute; "5%" -> relative to the worse group's mean."""
    text = str(effect or "").strip()
    if not text:
        return None
    rel = text.endswith("%")
    try:
        x = abs(float(text.rstrip("%").strip()))
    except ValueError:
        raise ValueError('effect must be a number such as 0.002, or a percentage such as "5%"') from None
    if x == 0:
        raise ValueError("effect must not be zero")
    return {"value": x / 100 if rel else x, "relative": rel, "text": text}


def claimed_numbers(claim: str) -> tuple[float, float] | None:
    """(before, after) from "from A to B", "A -> B" or "A vs B" in the claim."""
    for pattern, flip in ((FROM_TO, False), (ARROW, False), (VERSUS, True)):
        m = pattern.search(claim)
        if m:
            x, y = (float(g.replace("%", "").strip()) for g in m.groups())
            return (y, x) if flip else (x, y)    # "B vs A" names the new one first
    return None


# Planning -----------------------------------------------------------------------------------

def with_unseeded(tables: list[dict], rows: list[dict], cid: str, evidence: list[dict]) -> list[dict]:
    """claim_stats drops results.tsv runs without a seed once any run has one; a pilot
    keeps them, each unseeded group as one run, so a comparison run once is found and
    asks for more seeds instead of disappearing."""
    whole = any(r.get("claim") == cid and r.get("source") in cs.RESULTS for r in evidence)
    for t in tables:
        if t["source"] != "workflow/results.tsv" or "__best__" in t["groups"]:
            continue
        for r in rows:
            if (r.get("status") not in ("keep", "discard", "") or checks.number(r.get("metric", "")) is None
                    or cs.TAG.search(" ".join(r.values())) or cs.seed_of(r)
                    or not (r.get("claim") == cid if r.get("claim") else whole)):
                continue
            name = cs.group_name(r)
            if name not in t["groups"] or all(k.startswith("run") for k in t["groups"][name]):
                cs.add(t["groups"], name, "", checks.number(r["metric"]))
    return tables


def oriented(groups: dict) -> dict:
    """claim_stats' tables, with its single best kept run unpacked."""
    if "__best__" in groups:
        rows = groups["__best__"]["_rows"]
        return {cs.group_name(r): {"run1": checks.number(r["metric"])} for r in rows[:1]}
    return groups


def matched_pair(means: dict, numbers: tuple[float, float] | None) -> tuple[str, str] | None:
    """The (better, worse) groups whose means lie closest to the claim's (before, after)."""
    if not numbers or len(means) < 2:
        return None
    before, after = numbers
    best, score = None, MATCH_LIMIT
    for a in means:
        for b in means:
            if a == b:
                continue
            s = abs(means[b] - before) / max(abs(before), 1e-12) + abs(means[a] - after) / max(abs(after), 1e-12)
            if s < score:
                best, score = (a, b), s
    return best


def plan_claim(cid: str, claim: str, tables: list[dict], higher: bool, alpha: float, target: float, minimum: dict | None,
               compare: str) -> dict:
    sign = 1 if higher else -1
    numbers = claimed_numbers(claim)
    out, notes = [], []
    for t in tables:
        groups = oriented(t["groups"])
        means = {g: sig.mean(list(v.values())) for g, v in groups.items()}
        pair = None if t["compare"] or compare else matched_pair(means, numbers)
        pairs = [pair] if pair else cs.pairs_of(t["compare"] or compare, means, sign)
        if not pairs:
            notes.append(f"{t['source']}: only {', '.join(groups) or 'no group'}; no baseline to compare with")
        for a, b in pairs:
            plan = plan_pair(t["source"], a, b, groups[a], groups[b], sign, alpha, target, minimum, numbers,
                             matched=bool(pair))
            out.append({"claim": cid} | plan)
    if not tables:
        notes.append("no per-seed numbers found for this claim")
    return {"claim": claim, "numbers": numbers, "comparisons": out, "notes": notes}


def plan_pair(source: str, a: str, b: str, ga: dict, gb: dict, sign: int, alpha: float, target: float,
              minimum: dict | None, numbers: tuple[float, float] | None, matched: bool) -> dict:
    xa = {s: sign * v for s, v in ga.items()}
    xb = {s: sign * v for s, v in gb.items()}
    shared = sorted(set(xa) & set(xb), key=str)
    paired = len(shared) >= 2 and set(xa) == set(xb)
    diff = sig.mean(list(xa.values())) - sig.mean(list(xb.values()))
    c = {"source": source, "a": a, "b": b, "matched_claim": matched, "n_a": len(xa), "n_b": len(xb),
         "mean_a": sig.mean(list(ga.values())), "mean_b": sig.mean(list(gb.values())), "pilot_diff": diff,
         "paired": paired, "have": len(shared) if paired else min(len(xa), len(xb)),
         "sd_diff": None, "sd_a": None, "sd_b": None, "effect": None, "effect_from": None,
         "needed": None, "power_now": None, "state": "pilot"}
    if minimum:
        c["effect"] = minimum["value"] * abs(c["mean_b"]) if minimum["relative"] else minimum["value"]
        c["effect_from"] = "input"
    elif numbers:
        c["effect"], c["effect_from"] = abs(numbers[1] - numbers[0]), "claim"
    elif diff > 0:
        c["effect"], c["effect_from"] = diff, "pilot"
    if len(xa) < 2 or len(xb) < 2:
        return c                                   # state "pilot": run at least two seeds of each
    if paired:
        c["sd_diff"] = sig.sd([xa[s] - xb[s] for s in shared])
    c["sd_a"], c["sd_b"] = sig.sd(list(xa.values())), sig.sd(list(xb.values()))
    if not c["effect"]:
        c["state"] = "no_effect"                   # the pilot goes the wrong way and nothing names an effect
        return c
    kw = dict(effect=c["effect"], paired=paired, sd_diff=c["sd_diff"] or 0.0, sd_a=c["sd_a"], sd_b=c["sd_b"],
              alpha=alpha)
    c["power_now"] = stats.power_at(c["have"], **kw)
    c["needed"] = stats.seeds_needed(target=target, **kw)
    if c["needed"] is None:
        c["state"] = "infeasible"
    else:
        c["state"] = "enough" if c["have"] >= c["needed"] else "underpowered"
    if diff <= 0:
        c["wrong_way"] = True
    return c


def seeds_edit(paper: Path, seeds: int | None) -> tuple[list[dict], str]:
    """An exact edit of workflow/experiment.md setting "Seeds: N": the existing Seeds line
    replaced when it asks for fewer, else the line added after Direction (or Metric, or the
    last field)."""
    path = stages.folder(paper) / "experiment.md"
    if not seeds or not path.is_file():
        return [], ""
    text = path.read_text(encoding="utf-8")
    m = SEEDS_FIELD.search(text)
    rel = path.relative_to(paper).as_posix()
    problem = f"功效分析：每个对比至少要 {seeds} 个种子"
    if m:
        have = re.match(r"\s*(\d+)", m.group(1))
        if have and int(have.group(1)) >= seeds:
            return [], m.group(0)
        if text.count(m.group(0)) != 1:
            return [], m.group(0)
        return [{"id": "E1", "file": rel, "section": "Seeds", "kind": "seeds", "quote": m.group(0),
                 "replacement": f"Seeds: {seeds}", "problem": problem}], m.group(0)
    lines = [ln for ln in text.splitlines() if checks.FIELD.match(ln)]
    anchor = next((ln for key in ("Direction", "Metric") for ln in lines if ln.startswith(f"{key}:")),
                  lines[-1] if lines else "")
    if not anchor or text.count(anchor) != 1:
        return [], ""
    return [{"id": "E1", "file": rel, "section": "Seeds", "kind": "seeds", "quote": anchor,
             "replacement": f"{anchor}\nSeeds: {seeds}", "problem": problem}], ""


# The report -----------------------------------------------------------------------------------

STATES = {"enough": "种子够了", "underpowered": "种子不够", "pilot": "先试跑：两边各至少 2 个种子",
          "no_effect": "试跑方向相反，请用 effect 指定要检测的差值", "infeasible": f"超过 {stats.MAX_SEEDS} 个种子也不够"}
SOURCE = {"input": "输入", "claim": "论断写的差值", "pilot": "试跑均值差"}


def fmt(x, digits: int = 4) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{digits}g}"
    return str(x)


def report(record: dict, edits: list[dict]) -> str:
    status = record["status"]
    verdict = {"PASS": "每个对比现有的种子数都够了。",
               "PASS_WITH_NOTES": "有对比种子不够，或还没有试跑数据。",
               "FAIL": "找不到任何对比（需要论断，以及两组以上的逐种子结果）。"}[status]
    lines = ["# 种子数功效分析", "", f"结论：**{status}**，{verdict}", "",
             f"显著性水平 α = {fmt(record['alpha'])}，目标功效 {fmt(record['power'])}，双侧 t 检验；"
             f"指标{'越大越好' if record['higher_is_better'] else '越小越好'}。"
             "同一组种子两边都有时按配对 t（种子间差值的标准差），否则按两组 t（每组 n 个种子）。", ""]
    rows = [(cid, c) for cid, r in record["claims"].items() for c in r["comparisons"]]
    if rows:
        lines += ["| 论断 | 对比 | 试跑种子 | 均值 | 噪声标准差 | 最小差值 | 需要种子 | 现有功效 | 状态 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for cid, c in rows:
            noise = f"配对 {fmt(c['sd_diff'])}" if c["paired"] else (
                f"{fmt(c['sd_a'])} / {fmt(c['sd_b'])}" if c["sd_a"] is not None else "—")
            effect = f"{fmt(c['effect'])}（{SOURCE[c['effect_from']]}）" if c["effect"] else "—"
            state = STATES[c["state"]] + ("；试跑方向与论断相反" if c.get("wrong_way") else "")
            lines.append(f"| {cid} | {c['a']} > {c['b']} | {c['n_a']} / {c['n_b']} | {fmt(c['mean_a'])} / "
                         f"{fmt(c['mean_b'])} | {noise} | {effect} | {fmt(c['needed'])} | {fmt(c['power_now'], 2)} | "
                         f"{state} |")
        lines.append("")
    for cid, r in record["claims"].items():
        for n in r["notes"]:
            lines.append(f"- {cid}：{n}")
    if any(r["notes"] for r in record["claims"].values()):
        lines.append("")
    pilots = [cid for cid, c in rows if c["state"] == "pilot"]
    if pilots:
        lines += [f"{'、'.join(sorted(set(pilots)))} 还估不出噪声：对比的两组各用同样的种子（如 seed 1、seed 2、seed 3）"
                  "各跑至少两次，写进 results.tsv（描述里带 seed N），再运行本任务。", ""]
    if any(c["needed"] and min(c["n_a"], c["n_b"]) < 5 for _, c in rows):
        lines += ["试跑不到 5 个种子时噪声估计本身不稳，需要的种子数可能偏少，可多留一两个。", ""]
    if record["proposed_seeds"]:
        lines += [f"建议每个方法跑 **{record['proposed_seeds']}** 个种子（所有对比里最多的那个）。"]
        if edits:
            e = edits[0]
            lines += [f"已在 edits.json 里准备好对 `{e['file']}` 的修改（未应用）：", "", "```",
                      f"- {e['quote']}", *[f"+ {x}" for x in e["replacement"].splitlines()], "```", ""]
        elif record["current_seeds_line"]:
            lines += [f"experiment.md 现有 `{record['current_seeds_line']}`，不用改。", ""]
        else:
            lines.append("")
    lines += ["种子数只控制“种子噪声”这一种随机性；数据划分、超参数选择等其他变化不在其中。"
              "同时检验多个对比时，可把 α 除以对比数（Bonferroni）再运行。", ""]
    return "\n".join(lines)


__all__ = ["power_analysis"]

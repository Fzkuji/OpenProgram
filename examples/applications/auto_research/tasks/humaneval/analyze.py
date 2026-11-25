"""Analyze the answers of a human evaluation built by human_eval_build. Plain code, no
model call.

Steps: read (the downloaded CSVs and the survey's key), screen (raters failing half or
more of the attention checks are dropped; consistency on repeated tasks and speed are
reported per rater), agreement (Krippendorff's alpha, ordinal and nominal, and Fleiss'
kappa per criterion over the main tasks of the kept raters), ranking (Bradley-Terry win
rates with item-bootstrap CIs; in rating mode also mean ratings, and each rater's
ratings of one item compared system against system), claims (claims of claims.md that
name a system get its results), output (workflow/human_eval.csv and evidence rows
proposed as an exact edit of workflow/evidence.csv).
"""

import csv
import io
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from auto_research import stages
from auto_research.trail import Trail

from ..present.common import out_folder
from ..submission.common import repo
from . import guide as gd
from . import stats

NEEDED = ("survey_id", "rater", "task", "kind", "item", "criterion", "answer")
TABLE = "human_eval.csv"          # in workflow/
COLUMNS = ["survey_id", "criterion", "system", "comparisons", "wins", "ties", "losses", "raw_win_rate",
           "bt_win_rate", "ci_low", "ci_high", "mean_rating", "rating_ci_low", "rating_ci_high", "alpha_ordinal",
           "alpha_nominal", "fleiss_kappa", "raters", "claims"]
CONSISTENT_OK = 0.7               # a rater agreeing with themself less often than this is flagged
FAST_SECONDS = 3.0                # median seconds per page below this is flagged


def human_eval_analyze(paper_dir: str, responses: str, out_dir: str = "") -> str:
    """Analyze the answers of a human evaluation survey.

    paper_dir is the project's Git repository. responses names the answer CSVs the
    survey page downloaded: files or folders (all .csv inside), comma-separated, in the
    project or absolute. The survey's key comes from workflow/human_eval/<survey_id>.json
    (or a human_eval_build run folder). A rater who fails half or more of the attention
    checks is dropped; each rater's agreement with themself on repeated tasks and the
    median seconds per page are reported. Over the main tasks of the kept raters, per
    criterion: Krippendorff's alpha (ordinal and nominal) and Fleiss' kappa;
    Bradley-Terry win rates per system and pairwise probabilities with 95% CIs from
    1,000 bootstrap resamples of items; in rating mode also mean ratings with CIs.
    Claims of workflow/claims.md naming a system get its results. Writes
    workflow/human_eval.csv, and report.md, analysis.json and edits.json (evidence rows
    for workflow/evidence.csv in paper_proofread's edit format, not applied) to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/human_eval_analyze). PASS: every
    criterion's ordinal alpha is 0.667 or more and every claim naming a system has a CI;
    FAIL: fewer than two raters kept or no criterion reaches 0.667; PASS_WITH_NOTES
    otherwise.
    """
    paper = repo(paper_dir)
    files = response_files(paper, responses)
    rows, notes = read_rows(files)
    survey = rows[0]["survey_id"]
    key = load_key(paper, survey)
    out = out_folder(paper, out_dir, "human_eval_analyze")
    out.mkdir(parents=True, exist_ok=True)
    trail = Trail("human_eval_analyze", {"paper_dir": str(paper), "responses": responses, "out_dir": out_dir}, {})
    names = {code: name for name, code in key["systems"].items()}
    raters = screen(rows, key)
    kept = [r for r, v in raters.items() if v["kept"]]
    trail.step("screen", files=len(files), raters=len(raters), kept=len(kept),
               dropped={r: v["failed"] for r, v in raters.items() if not v["kept"]})
    main = [r for r in rows if r["kind"] == "main" and r["rater"] in kept]
    for rater, share in with_majority(main).items():
        raters[rater]["with_majority"] = share
    criteria = key["criteria"]
    results = {}
    for c in criteria:
        here = [r for r in main if r["criterion"] == c["id"]]
        results[c["id"]] = analyze_criterion(here, key, names)
    trail.step("agreement", alpha={cid: v["alpha_ordinal"] for cid, v in results.items()})
    claims = link_claims(gd.claims(paper), results, criteria, list(key["systems"]))
    table = table_rows(survey, results, criteria, claims, len(kept))
    write_table(stages.folder(paper) / TABLE, table)
    edits = evidence_edits(paper, claims, results, criteria, len(kept), len(raters))
    status = status_of(len(kept), results, claims)
    record = {"status": status, "survey_id": survey, "mode": key["mode"], "files": [str(f) for f in files],
              "raters": raters, "kept": kept, "results": results, "claims": claims, "notes": notes,
              "guide_approved": key.get("guide_approved")}
    (out / "analysis.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "edits.json").write_text(json.dumps({"root": str(paper), "edits": edits}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (out / "report.md").write_text(report(record, criteria, edits), encoding="utf-8")
    trail.step("report", status=status, claims=len(claims), edits=len(edits))
    run = trail.save(out, report=out / "report.md", json=out / "analysis.json", edits=out / "edits.json",
                     table=stages.folder(paper) / TABLE)
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "table": str(stages.folder(paper) / TABLE), "edits": str(out / "edits.json"),
                       "raters": len(raters), "kept": len(kept),
                       "alpha": {cid: v["alpha_ordinal"] for cid, v in results.items()}}, ensure_ascii=False)


# Reading -------------------------------------------------------------------------------------

def response_files(paper: Path, responses: str) -> list[Path]:
    if not str(responses or "").strip():
        raise ValueError("responses is required: the answer CSVs (files or a folder)")
    files = []
    for part in (p.strip() for p in responses.split(",") if p.strip()):
        p = gd.resolve(paper, part)
        if p.is_dir():
            files += sorted(p.glob("*.csv"))
        elif p.is_file():
            files.append(p)
        else:
            raise ValueError(f"{p} does not exist")
    if not files:
        raise ValueError(f"no .csv file in {responses}")
    return files


def read_rows(files: list[Path]) -> tuple[list[dict], list[str]]:
    """All answer rows; one survey id; a rater's later file replaced by the fuller one."""
    by_rater: dict[str, list[dict]] = {}
    notes = []
    for f in files:
        rows = list(csv.DictReader(io.StringIO(f.read_text(encoding="utf-8-sig"))))
        if not rows or any(k not in rows[0] for k in NEEDED):
            notes.append(f"{f.name} 不是问卷答案文件，已跳过")
            continue
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in rows]
        for rater in sorted({r["rater"] for r in rows}):
            mine = [r for r in rows if r["rater"] == rater]
            if rater in by_rater:
                notes.append(f"评分者 {rater} 提交了不止一次，用较完整的那份")
                if len(mine) <= len(by_rater[rater]):
                    continue
            by_rater[rater] = mine
    rows = [r for rs in by_rater.values() for r in rs]
    if not rows:
        raise ValueError("no answer rows in the response files")
    ids = sorted({r["survey_id"] for r in rows})
    if len(ids) > 1:
        raise ValueError(f"the answers come from more than one survey ({', '.join(ids)}); analyze one at a time")
    return rows, notes


def load_key(paper: Path, survey: str) -> dict:
    keep = stages.folder(paper) / "human_eval" / f"{survey}.json"
    candidates = [keep] + sorted((paper / "drafts" / "human_eval_build").glob("*/key.json"), reverse=True)
    for p in candidates:
        if p.is_file():
            key = json.loads(p.read_text(encoding="utf-8"))
            if key.get("survey_id") == survey:
                return key
    raise ValueError(f"no key for survey {survey}: expected workflow/human_eval/{survey}.json")


# Screening -----------------------------------------------------------------------------------

def screen(rows: list[dict], key: dict) -> dict:
    """Per rater: attention checks passed and failed, kept, self-agreement on repeated
    tasks, median seconds per page."""
    out = {}
    for rater in sorted({r["rater"] for r in rows}):
        mine = [r for r in rows if r["rater"] == rater]
        checks = defaultdict(list)
        for r in mine:
            if r["kind"] == "attention":
                checks[r["task"]].append(r["answer"] == key["attention"].get(r["task"]))
        failed = sum(not all(v) for v in checks.values())
        total = len(key["attention"])
        kept = failed < max(1, math.ceil(total / 2))
        first = {(r["task"], r["criterion"]): r for r in mine if r["kind"] == "main"}
        same, near, pairs = 0, 0, 0
        for r in mine:
            if r["kind"] != "repeat" or (r["repeat_of"] or r["task"], r["criterion"]) not in first:
                continue
            a = first[(r["repeat_of"] or r["task"], r["criterion"])]
            pairs += 1
            if key["mode"] == "pairwise":
                same += a["winner"] == r["winner"]
            else:
                same += a["answer"] == r["answer"]
                near += abs(int(a["answer"]) - int(r["answer"])) <= 1
        pages = {}
        for r in mine:
            try:
                pages[r["position"] or r["task"]] = float(r.get("seconds") or 0)
            except ValueError:
                pass
        secs = median(pages.values()) if pages else None
        consistency = same / pairs if pairs else None
        out[rater] = {"checks": len(checks), "failed": failed, "kept": kept, "repeats": pairs,
                      "consistency": consistency, "within_one": near / pairs if pairs and key["mode"] == "rating" else None,
                      "median_seconds": secs, "pages": len(pages),
                      "flags": [f for f, bad in (("inconsistent", consistency is not None and consistency < CONSISTENT_OK),
                                                 ("fast", secs is not None and secs < FAST_SECONDS),
                                                 ("missing_checks", len(checks) < total)) if bad]}
    return out


def with_majority(main: list[dict]) -> dict:
    """Per kept rater: the share of their answers that match the most common answer of
    the other kept raters on the same task and criterion (units where the others tie
    on top are left out). A rater far below the rest reads the guide differently."""
    by_unit: dict = defaultdict(dict)
    for r in main:
        by_unit[(r["task"], r["criterion"])][r["rater"]] = r["winner"] or r["answer"]
    hit, seen = defaultdict(int), defaultdict(int)
    for answers in by_unit.values():
        for rater, mine in answers.items():
            others = Counter(v for k, v in answers.items() if k != rater).most_common()
            if not others or (len(others) > 1 and others[0][1] == others[1][1]):
                continue
            seen[rater] += 1
            hit[rater] += mine == others[0][0]
    return {r: hit[r] / seen[r] for r in seen}


# Agreement and ranking -----------------------------------------------------------------------

def analyze_criterion(rows: list[dict], key: dict, names: dict) -> dict:
    systems = sorted(key["systems"])
    units: dict = defaultdict(list)
    games: dict = defaultdict(list)
    ratings: dict = defaultdict(lambda: defaultdict(list))
    counts = {s: {"comparisons": 0, "wins": 0, "ties": 0, "losses": 0} for s in systems}
    pair_counts: dict = defaultdict(int)
    if key["mode"] == "pairwise":
        for r in rows:
            a, b = names.get(r["shown_1"]), names.get(r["shown_2"])
            if not a or not b:
                continue
            lo, hi = sorted((a, b))
            win = names.get(r["winner"]) if r["winner"] != "tie" else "tie"
            value = 0 if win == "tie" else 1 if win == lo else -1
            units[(r["item"], lo, hi)].append(value)
            games[r["item"]].append((lo, hi, (value + 1) / 2))
            pair_counts[(lo, hi)] += 1
            for s, v in ((lo, value), (hi, -value)):
                counts[s]["comparisons"] += 1
                counts[s]["wins" if v > 0 else "ties" if v == 0 else "losses"] += 1
    else:
        per_rater: dict = defaultdict(dict)
        for r in rows:
            s = names.get(r["shown_1"])
            if not s or not r["answer"].isdigit():
                continue
            units[(r["item"], s)].append(int(r["answer"]))
            ratings[s][r["item"]].append(int(r["answer"]))
            per_rater[(r["rater"], r["item"])][s] = int(r["answer"])
        for (_, item), scores in per_rater.items():
            have = sorted(scores)
            for i, a in enumerate(have):
                for b in have[i + 1:]:
                    value = (scores[a] > scores[b]) - (scores[a] < scores[b])
                    games[item].append((a, b, (value + 1) / 2))
                    pair_counts[(a, b)] += 1
                    for s, v in ((a, value), (b, -value)):
                        counts[s]["comparisons"] += 1
                        counts[s]["wins" if v > 0 else "ties" if v == 0 else "losses"] += 1
    a_ord = stats.krippendorff_alpha(units, "ordinal")
    a_nom = stats.krippendorff_alpha(units, "nominal")
    kappa = stats.fleiss_kappa(units)
    bt = stats.bootstrap_bt(dict(games), systems) if games else None
    per_system = {}
    for s in systems:
        c = counts[s]
        row = dict(c, raw_win_rate=(c["wins"] + c["ties"] / 2) / c["comparisons"] if c["comparisons"] else None,
                   bt_win_rate=None, ci=None, mean_rating=None, rating_ci=None)
        if bt and c["comparisons"]:
            row["bt_win_rate"], row["ci"] = bt["win_rate"][s], bt["ci"][s]
        if key["mode"] == "rating" and ratings[s]:
            row["mean_rating"], row["rating_ci"] = stats.bootstrap_mean(dict(ratings[s]))
        per_system[s] = row
    pairs = {}
    if bt:
        for (a, b), n in pair_counts.items():
            for x, y in ((a, b), (b, a)):
                p, ci = bt["pairs"][(x, y)]
                pairs[f"{x}>{y}"] = {"a": x, "b": y, "comparisons": n, "p": p, "ci": ci}
    pairable = [v for v in units.values() if len(v) >= 2]
    return {"units": len(units), "pairable_units": len(pairable), "values": sum(len(v) for v in units.values()),
            "alpha_ordinal": rnd(a_ord), "alpha_nominal": rnd(a_nom), "fleiss_kappa": rnd(kappa),
            "systems": per_system, "pairs": pairs}


def rnd(x, d: int = 3):
    return None if x is None else round(x, d)


# Claims and outputs --------------------------------------------------------------------------

def link_claims(claims: dict, results: dict, criteria: list[dict], systems: list[str]) -> dict:
    """Claims naming a system: the results of every criterion that tests the claim (all
    criteria when none names it), the named systems in the order the claim names them."""
    out = {}
    for cid, text in claims.items():
        named = sorted((s for s in systems if gd.mentions(text, s)),
                       key=lambda s: re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", text, re.I).start())
        if not named:
            continue
        crits = [c["id"] for c in criteria if c.get("claim") == cid] or [c["id"] for c in criteria]
        entries = []
        for crit in crits:
            res = results[crit]
            for s in named:
                sysr = res["systems"].get(s, {})
                entries.append({"criterion": crit, "system": s, "win_rate": sysr.get("bt_win_rate"), "ci": sysr.get("ci")})
            for other in named[1:]:
                pr = res["pairs"].get(f"{named[0]}>{other}")
                if pr:
                    entries.append({"criterion": crit, "pair": f"{named[0]}>{other}", "p": pr["p"], "ci": pr["ci"],
                                    "comparisons": pr["comparisons"]})
        out[cid] = {"text": text, "systems": named, "criteria": crits, "results": entries,
                    "has_ci": all(e.get("ci") for e in entries) and bool(entries)}
    return out


def table_rows(survey: str, results: dict, criteria: list[dict], claims: dict, kept: int) -> list[dict]:
    rows = []
    for c in criteria:
        res = results[c["id"]]

        def linked(s, crit=c["id"]):
            return " ".join(cid for cid, v in claims.items() if crit in v["criteria"] and s in v["systems"])
        base = {"survey_id": survey, "criterion": c["id"], "alpha_ordinal": res["alpha_ordinal"],
                "alpha_nominal": res["alpha_nominal"], "fleiss_kappa": res["fleiss_kappa"], "raters": kept}
        for s, v in res["systems"].items():
            rows.append(base | {"system": s, "comparisons": v["comparisons"], "wins": v["wins"], "ties": v["ties"],
                                "losses": v["losses"], "raw_win_rate": rnd(v["raw_win_rate"]),
                                "bt_win_rate": rnd(v["bt_win_rate"]), "ci_low": rnd(v["ci"] and v["ci"][0]),
                                "ci_high": rnd(v["ci"] and v["ci"][1]), "mean_rating": rnd(v["mean_rating"]),
                                "rating_ci_low": rnd(v["rating_ci"] and v["rating_ci"][0]),
                                "rating_ci_high": rnd(v["rating_ci"] and v["rating_ci"][1]), "claims": linked(s)})
        for name, p in res["pairs"].items():
            rows.append(base | {"system": name, "comparisons": p["comparisons"], "bt_win_rate": rnd(p["p"]),
                                "ci_low": rnd(p["ci"] and p["ci"][0]), "ci_high": rnd(p["ci"] and p["ci"][1]),
                                "claims": linked(p["a"])})
    return rows


def write_table(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in COLUMNS})
    path.write_text(buf.getvalue(), encoding="utf-8")


def pct(x) -> str:
    return f"{100 * x:.0f}%"


def evidence_edits(paper: Path, claims: dict, results: dict, criteria: list[dict], kept: int, total: int) -> list[dict]:
    """One exact edit adding an evidence row per linked claim and criterion to
    workflow/evidence.csv (kind "create" when the file is missing); rows already there
    are not proposed again."""
    path = stages.folder(paper) / "evidence.csv"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = next(csv.reader([lines[0]])) if lines else ["id", "claim", "statement", "source", "script"]
    numbers = [int(m) for m in re.findall(r"^E(\d+),", text, re.M)]
    nxt = max(numbers, default=0) + 1
    names = {c["id"]: c["name"] for c in criteria}
    new = []
    for cid, cl in claims.items():
        for crit in cl["criteria"]:
            res = results[crit]
            if res["alpha_ordinal"] is None or res["alpha_ordinal"] < stats.ALPHA_OK:
                continue                       # unreliable labels are not evidence
            parts = [e for e in cl["results"] if e["criterion"] == crit and e.get("ci")]
            if not parts:
                continue
            pair = next((e for e in parts if "pair" in e), None)
            if pair:
                a, b = pair["pair"].split(">", 1)
                what = (f"Human raters preferred {a} over {b} with Bradley-Terry probability {pair['p']:.2f} "
                        f"(95% CI {pair['ci'][0]:.2f}-{pair['ci'][1]:.2f}, {pair['comparisons']} comparisons)")
            else:
                e = parts[0]
                what = (f"{e['system']} won {pct(e['win_rate'])} of human comparisons (Bradley-Terry, 95% CI "
                        f"{pct(e['ci'][0])}-{pct(e['ci'][1])})")
            statement = (f"{what} on {names[crit]}; {kept} of {total} raters kept, Krippendorff alpha "
                         f"{res['alpha_ordinal']}")
            if statement in text:
                continue
            values = {"id": f"E{nxt}", "claim": cid, "statement": statement, "source": f"workflow/{TABLE}",
                      "script": ""}
            nxt += 1
            new.append([values.get(h, "") for h in header])
    if not new:
        return []
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(new)
    rows = buf.getvalue().rstrip("\n")
    rel = path.relative_to(paper).as_posix()
    problem = "人工评测结果作为证据"
    if not lines:
        head = io.StringIO()
        csv.writer(head, lineterminator="\n").writerow(header)
        return [{"id": "E1", "file": rel, "section": "evidence", "kind": "create", "quote": "",
                 "replacement": head.getvalue() + rows + "\n", "problem": problem}]
    quote = lines[-1] if text.count(lines[-1]) == 1 else text
    return [{"id": "E1", "file": rel, "section": "evidence", "kind": "evidence", "quote": quote,
             "replacement": f"{quote}\n{rows}", "problem": problem}]


def status_of(kept: int, results: dict, claims: dict) -> str:
    alphas = [v["alpha_ordinal"] for v in results.values()]
    if kept < 2 or not any(a is not None and a >= stats.ALPHA_OK for a in alphas):
        return "FAIL"
    if all(a is not None and a >= stats.ALPHA_OK for a in alphas) and all(c["has_ci"] for c in claims.values()):
        return "PASS"
    return "PASS_WITH_NOTES"


# Report --------------------------------------------------------------------------------------

def f(x, d: int = 2) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def ci(c) -> str:
    return "—" if not c else f"{c[0]:.2f}–{c[1]:.2f}"


def report(r: dict, criteria: list[dict], edits: list[dict]) -> str:
    verdict = {"PASS": "评分一致性达标，每个点名系统的论断都有置信区间。",
               "PASS_WITH_NOTES": "部分标准一致性不足 0.667，或有论断缺少置信区间（见下）。",
               "FAIL": ("保留的评分者不足两人" if len(r["kept"]) < 2 else "没有一个标准的一致性达到 0.667")
               + "，人工评测结果还不能支持论断。"}[r["status"]]
    raters = r["raters"]
    dropped = [k for k, v in raters.items() if not v["kept"]]
    lines = ["# 人工评测分析", "", f"结论：**{r['status']}**，{verdict}", "",
             f"问卷 `{r['survey_id']}`，{'两两比较' if r['mode'] == 'pairwise' else '逐条打分'}；"
             f"{len(raters)} 位评分者，保留 {len(r['kept'])} 位"
             + (f"，剔除 {len(dropped)} 位（注意力检查一半以上未通过）" if dropped else "") + "。", ""]
    if r.get("guide_approved") is False:
        lines += ["注意：生成问卷时指南还未确认（workflow/eval_guide.md 不是 `Approved: yes`）。", ""]
    lines += ["## 评分者", "", "| 评分者 | 注意力检查未通过 | 重复题一致 | 与其他人多数一致 | 每页中位秒数 | 结果 |", "|---|---|---|---|---|---|"]
    for k, v in raters.items():
        flags = {"inconsistent": "前后不一致", "fast": "过快", "missing_checks": "注意力检查不全"}
        note = "、".join(flags[x] for x in v["flags"])
        lines.append(f"| {k} | {v['failed']}/{v['checks']} | "
                     f"{pct(v['consistency']) if v['consistency'] is not None else '—'}（{v['repeats']} 题） | "
                     f"{pct(v['with_majority']) if v.get('with_majority') is not None else '—'} | "
                     f"{f(v['median_seconds'], 1)} | {'保留' if v['kept'] else '剔除'}{('，' + note) if note else ''} |")
    lines += ["", "## 一致性（只算保留者的正式题）", "",
              "| 标准 | Krippendorff α（有序） | α（名义） | Fleiss κ | 可配对单元 |", "|---|---|---|---|---|"]
    for c in criteria:
        res = r["results"][c["id"]]
        ok = res["alpha_ordinal"] is not None and res["alpha_ordinal"] >= stats.ALPHA_OK
        lines.append(f"| {c['name']} | {f(res['alpha_ordinal'], 3)}{'' if ok else ' ⚠'} | {f(res['alpha_nominal'], 3)} | "
                     f"{f(res['fleiss_kappa'], 3)} | {res['pairable_units']} |")
    lines += ["", "α ≥ 0.667 才可用于下结论（Krippendorff）；⚠ 表示未达到。", "", "## 系统排名（Bradley–Terry，按条目自助抽样 1000 次的 95% 区间）", ""]
    for c in criteria:
        res = r["results"][c["id"]]
        lines += [f"**{c['name']}**", "", "| 系统 | 比较次数 | 胜/平/负 | BT 胜率 | 95% 区间 |"
                  + (" 平均分 | 95% 区间 |" if r["mode"] == "rating" else ""),
                  "|---|---|---|---|---|" + ("---|---|" if r["mode"] == "rating" else "")]
        for s, v in sorted(res["systems"].items(), key=lambda kv: -(kv[1]["bt_win_rate"] or 0)):
            extra = f" {f(v['mean_rating'])} | {ci(v['rating_ci'])} |" if r["mode"] == "rating" else ""
            lines.append(f"| {s} | {v['comparisons']} | {v['wins']}/{v['ties']}/{v['losses']} | {f(v['bt_win_rate'])} | "
                         f"{ci(v['ci'])} |{extra}")
        lines.append("")
    lines += ["## 论断", ""]
    if not r["claims"]:
        lines += ["claims.md 里没有论断点名被比较的系统，结果只写进 workflow/human_eval.csv。", ""]
    for cid, cl in r["claims"].items():
        lines.append(f"- **{cid}**（{'、'.join(cl['systems'])}）：{cl['text']}")
        for e in cl["results"]:
            name = next(c["name"] for c in criteria if c["id"] == e["criterion"])
            if "pair" in e:
                a, b = e["pair"].split(">", 1)
                sig = "区间不含 0.5" if e["ci"] and (e["ci"][0] > 0.5 or e["ci"][1] < 0.5) else "区间含 0.5，差异不显著"
                lines.append(f"  - {name}：P({a} 胜 {b}) = {f(e['p'])}，95% 区间 {ci(e['ci'])}，{sig}")
            elif not e.get("ci"):
                lines.append(f"  - {name}：{e['system']} 没有可用的比较，缺少置信区间")
    if edits:
        e = edits[0]
        skip = len(e["quote"].splitlines()) if e["kind"] != "create" else 0
        lines += ["", "edits.json 里准备好了 workflow/evidence.csv 的新增证据行（未应用）：", "", "```"]
        lines += [f"+ {x}" for x in e["replacement"].splitlines()[skip:]] + ["```"]
    elif r["claims"]:
        lines += ["", "一致性未达 0.667 的标准不作为证据，没有提议新增证据行。"]
    lines += ["", "结果表：workflow/human_eval.csv。"]
    if r["notes"]:
        lines += ["", "## 说明", ""] + [f"- {n}" for n in r["notes"]]
    return "\n".join(lines) + "\n"


__all__ = ["human_eval_analyze"]

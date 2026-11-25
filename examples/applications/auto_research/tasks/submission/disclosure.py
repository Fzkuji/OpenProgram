"""A draft AI-use disclosure, in the shape of the Agents4Science AI-involvement checklist:
for each stage of the work, what AI tools did, read from the run records under drafts/
and the repository's commit trailers. The author edits it and decides what goes into the
paper; this never writes into the paper. Plain code, no model calls.
"""

import json
import re
import subprocess
from pathlib import Path

from auto_research.trail import Trail

from . import common

STAGES = {
    "ideation": ("Ideation and literature", "选题与文献"),
    "experiments": ("Experiments", "实验"),
    "writing": ("Writing and review", "写作与审阅"),
    "figures": ("Figures", "图表"),
    "references": ("References", "参考文献"),
    "code": ("Code", "代码"),
    "planning": ("Project tracking", "项目进度"),
    "other": ("Other assistance", "其他"),
}
WORKFLOW_STAGE = {
    "idea_generation": "ideation", "research_survey": "ideation", "related_work_watch": "ideation",
    "field_survey": "ideation", "claim_support": "ideation", "literature_table": "ideation", "taxonomy": "ideation",
    "paper_review": "writing", "paper_proofread": "writing", "paper_revision_check": "writing",
    "rebuttal": "writing", "paper_figures": "figures", "citation_check": "references",
    "citation_graph": "references", "progress": "planning",
}
AI_NAME = re.compile(r"\b(?:AI|Claude|Anthropic|GPT[-\w.]*|ChatGPT|OpenAI|Codex|Copilot|Gemini|Cursor|DeepSeek|"
                     r"Qwen|Llama|Mistral|LLM|Devin|Aider)\b", re.I)
TRAILER = re.compile(r"^\s*Co-Authored-By:\s*(.+)$", re.I | re.M)
CODE_EXTS = {".py", ".sh", ".ipynb", ".js", ".ts", ".cpp", ".c", ".h", ".cu", ".rs", ".go", ".java", ".jl", ".r",
             ".m", ".yaml", ".yml", ".toml", ".cfg"}
FIGURE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps", ".tikz", ".pgf"}
WRITING_EXTS = {".tex", ".bib", ".sty", ".cls", ".bst"}


def ai_disclosure(paper_dir: str, out_dir: str = "") -> str:
    """Draft an AI-use disclosure for the paper from what the repository records.

    paper_dir is the project's Git repository. Reads every drafts/**/run.json (the
    workflows run, their model calls and cost) and the commits whose Co-Authored-By
    trailer names an AI tool, sorted by whether they touched .tex, code or figures.
    Writes ai_disclosure.md (an English paragraph per stage, a Chinese summary for the
    author) and the trail to out_dir/<timestamp>/ (default <paper_dir>/drafts/ai_disclosure).
    Always PASS: it is a draft for the author and never changes the paper.
    """
    paper = common.repo(paper_dir)
    out = common.run_folder(paper, out_dir, "ai_disclosure")
    trail = Trail("ai_disclosure", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    runs = read_runs(paper, out)
    trail.step("runs", runs=len(runs), workflows=sorted({r["workflow"] for r in runs}))
    commits = ai_commits(paper)
    trail.step("commits", ai_commits=len(commits), tools=sorted({t for c in commits for t in c["tools"]}))

    stages = {key: {"workflows": {}, "model_calls": 0, "cost": 0.0, "commits": 0, "tools": set()} for key in STAGES}
    for r in runs:
        s = stages[stage_of(r["workflow"])]
        s["workflows"][r["workflow"]] = s["workflows"].get(r["workflow"], 0) + 1
        s["model_calls"] += r["model_calls"]
        s["cost"] += r["cost"]
    for c in commits:
        for area in c["areas"]:
            stages[area]["commits"] += 1
            stages[area]["tools"] |= set(c["tools"])
    used = {k: v for k, v in stages.items() if v["workflows"] or v["commits"]}
    calls = sum(r["model_calls"] for r in runs)
    cost = round(sum(r["cost"] for r in runs), 4)
    text = draft(used, runs, commits, calls, cost)
    (out / "ai_disclosure.md").write_text(text, encoding="utf-8")
    run = trail.save(out, report=out / "ai_disclosure.md")
    return json.dumps({"status": "PASS", "report": str(out / "ai_disclosure.md"), "run": str(run),
                       "stages": sorted(used), "runs": len(runs), "ai_commits": len(commits),
                       "model_calls": calls, "cost": cost}, ensure_ascii=False)


def stage_of(workflow: str) -> str:
    if workflow in WORKFLOW_STAGE:
        return WORKFLOW_STAGE[workflow]
    if re.search(r"experiment|baseline|finding|ablation|dataset", workflow):
        return "experiments"
    if re.search(r"figure|plot", workflow):
        return "figures"
    if re.search(r"cit|bib|reference", workflow):
        return "references"
    if re.search(r"idea|survey|literature|watch", workflow):
        return "ideation"
    if re.search(r"paper|review|proofread|revision|writ", workflow):
        return "writing"
    return "other"


def read_runs(paper: Path, out: Path) -> list[dict]:
    """One entry per drafts/**/run.json with model calls; runs of this tool are skipped."""
    runs = []
    for path in sorted((paper / "drafts").rglob("run.json")):
        if out in path.parents:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        workflow = str(data.get("workflow") or "")
        if not workflow or workflow in ("ai_disclosure", "submission_check", "arxiv_package"):
            continue
        model = [s for s in data.get("stages") or [] if isinstance(s, dict) and s.get("kind") == "model"]
        usage = data.get("usage") or {}
        calls = int(usage.get("calls") or 0) or len(model)
        runs.append({"workflow": workflow, "finished": str(data.get("finished") or ""),
                     "roles": sorted((data.get("roles") or {}).keys()), "model_stages": len(model),
                     "model_calls": calls, "cost": float(usage.get("cost") or 0),
                     "path": path.relative_to(paper).as_posix()})
    return runs


def ai_commits(paper: Path) -> list[dict]:
    """Commits whose Co-Authored-By trailer names an AI tool, with the areas they touched."""
    done = subprocess.run(["git", "-C", str(paper), "log", "--name-only", "--format=\x1e%H\x1f%B\x1f"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        return []
    found = []
    for block in done.stdout.split("\x1e")[1:]:
        parts = block.split("\x1f")
        if len(parts) < 3:
            continue
        sha, body, names = parts[0], parts[1], parts[2]
        tools = sorted({t.strip() for t in TRAILER.findall(body) if AI_NAME.search(t)})
        if not tools:
            continue
        files = [n.strip() for n in names.splitlines() if n.strip()]
        found.append({"commit": sha[:10], "tools": [re.sub(r"\s*<[^>]*>", "", t) for t in tools],
                      "areas": sorted({area(f) for f in files} - {""}) or ["other"], "files": len(files)})
    return found


def area(path: str) -> str:
    p = Path(path)
    if p.parts and p.parts[0] in ("drafts", "workflow"):
        return ""
    ext = p.suffix.lower()
    if ext in WRITING_EXTS:
        return "writing"
    if ext in FIGURE_EXTS and (ext != ".pdf" or re.search(r"fig|plot|image|img", path, re.I)):
        return "figures"
    if ext in CODE_EXTS:
        return "code"
    return "other"


ENGLISH = {
    "ideation": "AI tools were used to search the literature, summarise related work and propose candidate "
                "research directions",
    "experiments": "AI tools assisted in planning experiments, reproducing baselines and recording findings",
    "writing": "AI tools reviewed and proofread drafts of the manuscript and checked revisions against reviewer "
               "comments",
    "figures": "AI tools helped write and run the plotting scripts that produce the figures",
    "references": "AI tools checked the bibliography entries and citations against public metadata",
    "code": "AI coding assistants contributed to the code used for the experiments",
    "planning": "AI tools read the project's files to summarise which tasks were done and which remained",
    "other": "AI tools assisted with other parts of the project",
}


def draft(stages: dict, runs: list[dict], commits: list[dict], calls: int, cost: float) -> str:
    lines = ["# AI 使用声明（草稿）", "",
             "这是根据仓库记录自动整理的草稿，只给作者参考：请逐段核对、删改，再决定是否以及如何写进论文或投稿系统。"
             "它没有改动论文。", "", "## Draft disclosure (English)", ""]
    if not stages:
        lines += ["No AI tool use was recorded in this repository (no workflow runs under drafts/ and no commit "
                  "trailers naming an AI tool). If AI tools were used outside it, describe them here.", ""]
    for key, s in stages.items():
        title = STAGES[key][0]
        detail = []
        if s["workflows"]:
            detail.append("through the workflows " + ", ".join(f"{w} ({n} run{'s' if n > 1 else ''})"
                                                             for w, n in sorted(s["workflows"].items())))
        if s["commits"]:
            tools = ", ".join(sorted(s["tools"])) or "an AI assistant"
            detail.append(f"in {s['commits']} commit{'s' if s['commits'] > 1 else ''} co-authored with {tools}")
        lines += [f"**{title}.** {ENGLISH[key]}, " + " and ".join(detail) + ". "
                  "The authors reviewed every output, verified the results and take full responsibility for the "
                  "content.", ""]
    lines += ["## 中文摘要（给作者）", "", "| 阶段 | 工作流（运行次数） | AI 合著提交 | 模型调用 | 花费 |", "|---|---|---|---|---|"]
    for key, s in stages.items():
        flows = "、".join(f"{w}（{n}）" for w, n in sorted(s["workflows"].items())) or "—"
        lines.append(f"| {STAGES[key][1]} | {flows} | {s['commits'] or '—'} | {s['model_calls'] or '—'} | "
                     f"{'$%.4f' % s['cost'] if s['cost'] else '—'} |")
    if not stages:
        lines.append("| — | — | — | — | — |")
    lines += ["", f"共 {len(runs)} 次工作流运行、{calls} 次模型调用"
                  + (f"、花费 ${cost:.4f}" if cost else "") + f"；{len(commits)} 个提交带 AI 合著署名。", ""]
    if commits:
        tools = sorted({t for c in commits for t in c["tools"]})
        lines += ["提交署名里出现的 AI 工具：" + "、".join(tools) + "。", ""]
    if runs:
        lines += ["## 依据", "", "| 运行记录 | 工作流 | 完成时间 | 模型调用 |", "|---|---|---|---|"]
        lines += [f"| `{r['path']}` | {r['workflow']} | {r['finished'] or '—'} | {r['model_calls']} |" for r in runs]
        lines.append("")
    return "\n".join(lines)


__all__ = ["ai_disclosure"]

"""What the outreach tools share: the paper repository and its LaTeX text, the theorem
environments, a little LaTeX-to-text, the run folder and the report status. Plain code."""

import json
import re
from datetime import datetime
from pathlib import Path

from auto_research.trail import Trail

from ..revision.steps.prepare.source import body, flatten
from ..submission import common as sub

THEOREM_KINDS = ("theorem", "lemma", "proposition", "corollary", "claim", "conjecture")
NEWTHEOREM = re.compile(r"\\newtheorem\*?\{([^}]+)\}(?:\[[^\]]*\])?\{([^}]+)\}")
LABEL = re.compile(r"\\label\{([^}]+)\}")


def repo(paper_dir: str) -> Path:
    return sub.repo(paper_dir)


def run_folder(paper: Path, out_dir: str, default: str) -> Path:
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / default
    return base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def source(paper: Path) -> tuple[Path, str]:
    """The main .tex and its text with every \\input inlined and comments removed."""
    main = sub.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper} has no main .tex (set main_tex in workflow/rules.json)")
    return main, flatten(paper, main.relative_to(paper).as_posix())


def paper_body(full: str) -> str:
    return body(full).strip()


def theorem_envs(full: str) -> dict[str, str]:
    """{environment name: kind} for the theorem-like environments: the standard names and
    those \\newtheorem declares with a theorem-like title ("\\newtheorem{thm}{Theorem}")."""
    envs = {k: k for k in THEOREM_KINDS}
    for name, title in NEWTHEOREM.findall(full):
        kind = title.strip().lower()
        if kind in THEOREM_KINDS or kind == "definition":
            envs[name.strip()] = kind
    envs.setdefault("definition", "definition")
    return envs


def statements(full: str, kinds=THEOREM_KINDS) -> list[dict]:
    """Each theorem-like environment of the given kinds, in order: env, kind, label, title, text."""
    envs = {e: k for e, k in theorem_envs(full).items() if k in kinds}
    if not envs:
        return []
    pattern = re.compile(r"\\begin\{(" + "|".join(re.escape(e) for e in envs) + r")\*?\}(?:\[([^\]]*)\])?")
    found = []
    for m in pattern.finditer(full):
        env = m.group(1)
        close = re.compile(r"\\end\{" + re.escape(env) + r"\*?\}").search(full, m.end())
        text = full[m.end():close.start() if close else len(full)]
        labels = LABEL.findall(text)
        found.append({"env": env, "kind": envs[env], "label": labels[0] if labels else "",
                      "title": (m.group(2) or "").strip(), "text": LABEL.sub("", text).strip()})
    return found


def detex(text: str) -> str:
    """LaTeX as plain reading text: commands that wrap text keep it, references and
    citations go, math keeps its symbols without backslashes."""
    text = re.sub(r"\\(?:label|cite[tp]?|citep|citet|footnote|thanks)\*?(?:\[[^\]]*\])*\{[^{}]*\}", "", text)
    text = re.sub(r"~?\\(?:ref|eqref|cref|Cref|autoref)\{[^}]*\}", "", text)
    text = text.replace("{,}", ",").replace("\\%", "%").replace("\\&", "&").replace("~", " ")
    for _ in range(3):
        text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)
    text = re.sub(r"[{}$\\]", "", text)
    return " ".join(text.split())


def status(errors: int, notes: int) -> str:
    return "FAIL" if errors else "PASS_WITH_NOTES" if notes else "PASS"


def finding(level: str, where: str, problem: str, fix: str = "") -> dict:
    return {"level": level, "where": where, "problem": problem, "fix": fix}


def save(out: Path, trail: Trail, name: str, record: dict, report: str, **extra_files) -> dict:
    """Write <name>.json, report.md and the trail; the summary every tool returns."""
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report, encoding="utf-8")
    trail.step("report", status=record["status"])
    run = trail.save(out, report=out / "report.md", json=out / f"{name}.json", **extra_files)
    return {"status": record["status"], "report": str(out / "report.md"), "json": str(out / f"{name}.json"),
            "run": str(run)}


def findings_md(found: list[dict]) -> list[str]:
    if not found:
        return ["没有问题。", ""]
    lines = ["| 级别 | 位置 | 问题 | 怎么改 |", "|---|---|---|---|"]
    for f in found:
        level = "错误" if f["level"] == "error" else "提示"
        lines.append(f"| {level} | `{f['where']}` | {cell(f['problem'])} | {cell(f['fix'])} |")
    return lines + [""]


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")

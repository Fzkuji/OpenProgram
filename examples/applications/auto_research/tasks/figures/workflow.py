"""Check a paper's plotting scripts against the publication figure rules, and redraw
every figure from them.

Steps: find (the scripts named by figure_scripts in workflow/rules.json), read (each
script's source checked against the rules of the scientific-figure-making skill kept in
skill/), draw (each script run with an interpreter that has matplotlib, so every figure comes from its
script), report (Markdown and JSON). Plain code: no role and no model call.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research import stages
from auto_research.trail import Trail

from ..revision.rules import paper as paper_rules

ROLES = {}

SKILL = Path(__file__).parent / "skill"
HELPERS = "paperfig.py"                        # templates/paperfig.py, copied to workflow/figures/
FIGURE_FILES = (".pdf", ".svg", ".eps", ".png", ".jpg", ".jpeg", ".tif", ".tiff")
VECTOR = (".pdf", ".svg", ".eps")
MIN_DPI = 300
TIMEOUT = 300                                  # seconds for one script
SKIPPED = {".git", "drafts", "node_modules", "__pycache__", ".venv", "venv"}
# Colors the skill uses besides PALETTE: its neutral grays, its magenta accent, black and white.
EXTRA_COLORS = {"#767676", "#4D4D4D", "#272727", "#EA84DD", "#000000", "#FFFFFF"}
HEX = re.compile(r"#[0-9a-fA-F]{6}")
SPINES = re.compile(r"apply_publication_style\s*\(|axes\.spines\.(top|right)|spines\[[^\]]*\]\.set_visible\(\s*False")
ORDER = ("PASS", "PASS_WITH_NOTES", "FAIL")


def figure_rules() -> dict[str, str]:
    """The skill's rules as text, for a role that edits plotting scripts."""
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted((SKILL / "references").glob("*.md")) if p.name not in ("demos.md", "tutorials.md")}


def palette() -> set[str]:
    """The colors a script may use: PALETTE of the helper module, and the skill's extras."""
    tree = ast.parse((stages.TEMPLATES / HELPERS).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PALETTE":
            return {c.upper() for c in ast.literal_eval(node.value).values()} | EXTRA_COLORS
    raise ValueError(f"{HELPERS} defines no PALETTE")


def scripts(root: Path, patterns) -> list[Path]:
    """The plotting scripts under root that figure_scripts names, without the helper module."""
    found = {p for pattern in patterns for p in root.glob(pattern) if p.is_file() and p.name != HELPERS}
    return sorted(found)


def finding(level: str, check: str, script: str, message: str) -> dict:
    return {"level": level, "check": check, "script": script, "message": message}


def read(source: str, name: str, allowed: set[str]) -> list[dict]:
    """What the source of one script shows against the rules, without running it."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [finding("FAIL", "syntax", name, f"does not parse: {e.msg} at line {e.lineno}")]
    found = []
    styled = bool(SPINES.search(source))
    if not styled:
        found.append(finding("WARN", "style", name, "sets no publication style: call apply_publication_style() "
                                                      "or turn the top and right spines off"))
    sets_dpi = "savefig.dpi" in source or "apply_publication_style" in source
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "attr", getattr(node.func, "id", ""))
        dpi = next((k.value for k in node.keywords if k.arg == "dpi"), None)
        if isinstance(dpi, ast.Constant) and isinstance(dpi.value, (int, float)) and dpi.value < MIN_DPI:
            found.append(finding("FAIL", "dpi", name, f"line {node.lineno}: dpi={dpi.value}; export at {MIN_DPI} or more"))
        if called == "savefig" and dpi is None and not sets_dpi:
            target = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
            if not str(target).lower().endswith(VECTOR):
                found.append(finding("FAIL", "dpi", name, f"line {node.lineno}: savefig without dpi saves at "
                                                           f"matplotlib's default; pass dpi={MIN_DPI} or use finalize_figure"))
        if called == "show":
            found.append(finding("WARN", "show", name, f"line {node.lineno}: show() stops an unattended run; save the figure instead"))
    colors = {n.value.upper() for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str) and HEX.fullmatch(n.value)}
    other = sorted(colors - allowed)
    if other:
        found.append(finding("WARN", "palette", name, f"colors outside the palette: {', '.join(other)}; "
                                                        "use PALETTE so a method keeps one color across figures"))
    return found


def can_plot(program: str) -> bool:
    """Whether the interpreter at this path imports matplotlib."""
    return subprocess.run([program, "-c", "import matplotlib.pyplot"], capture_output=True, timeout=60).returncode == 0


def interpreter(name: str) -> str:
    """The path of the interpreter that draws: figure_python when it is set, otherwise the
    first of this interpreter and every python3 on PATH and in the Homebrew folders that
    imports matplotlib. Empty when none does."""
    if name:
        program = shutil.which(name)
        return program if program and can_plot(program) else ""
    folders = [*os.environ.get("PATH", "").split(os.pathsep), "/opt/homebrew/bin", "/usr/local/bin"]
    found = [sys.executable, *(str(Path(f) / "python3") for f in folders if f)]
    return next((p for p in dict.fromkeys(found) if Path(p).exists() and can_plot(p)), "")


def figure_files(root: Path) -> dict[str, tuple[int, int]]:
    """Every figure file under root with its size and time, outside drafts and caches."""
    found = {}
    for folder, names, files in os.walk(root):
        names[:] = [n for n in names if n not in SKIPPED]
        for f in files:
            if f.lower().endswith(FIGURE_FILES):
                path = Path(folder) / f
                stat = path.stat()
                found[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return found


def draw(root: Path, script: Path, python: str) -> tuple[list[dict], list[str]]:
    """Run one script in its own folder and name the figure files it wrote."""
    name = script.relative_to(root).as_posix()
    before = figure_files(root)
    helpers = str(root / stages.FOLDER / "figures")
    env = {**os.environ, "MPLBACKEND": "Agg",
           "PYTHONPATH": os.pathsep.join(filter(None, [helpers, os.environ.get("PYTHONPATH", "")]))}
    try:
        done = subprocess.run([python, script.name], cwd=script.parent, env=env,
                              capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return [finding("FAIL", "run", name, f"still running after {TIMEOUT} seconds")], []
    if done.returncode:
        return [finding("FAIL", "run", name, "failed: " + " ".join(done.stderr.strip().splitlines()[-3:])[:600])], []
    written = sorted(k for k, v in figure_files(root).items() if before.get(k) != v)
    if not written:
        return [finding("WARN", "output", name, "ran and wrote no figure file")], []
    return [], written


def status_of(findings: list[dict]) -> str:
    levels = {f["level"] for f in findings}
    return "FAIL" if "FAIL" in levels else "PASS_WITH_NOTES" if "WARN" in levels else "PASS"


LABEL = {"FAIL": "必须改", "WARN": "建议改"}


def render(record: dict) -> str:
    lines = ["# 论文图表检查", "", f"结论：{record['status']}", "", f"检查对象：`{record['root']}`", "",
             f"解释器：`{record['python']}`" if record["python"] else "解释器：没有能导入 matplotlib 的，没有重画", "",
             "| 脚本 | 必须改 | 建议改 | 重画出的图 |", "|---|---|---|---|"]
    for s in record["scripts"]:
        mine = [f for f in record["findings"] if f["script"] == s["script"]]
        lines.append(f"| `{s['script']}` | {sum(f['level'] == 'FAIL' for f in mine)} | "
                     f"{sum(f['level'] == 'WARN' for f in mine)} | {'、'.join(f'`{o}`' for o in s['figures']) or '无'} |")
    lines += ["", "## 问题", ""]
    if not record["findings"]:
        lines.append("没有问题。")
    for f in sorted(record["findings"], key=lambda f: (f["level"] != "FAIL", f["script"])):
        lines.append(f"- **{LABEL[f['level']]}** `{f['script'] or '全部'}`（{f['check']}）：{f['message']}")
    lines += ["", "图表规则来自 figures4papers 的 scientific-figure-making skill（Chen Liu，CC BY-NC 4.0）。"
                  "重画出的图要逐张看过再放进论文。", ""]
    return "\n".join(lines)


def paper_figures(paper_dir: str, draft_dir: str = "", out_dir: str = "") -> str:
    """Check the paper's plotting scripts against the figure rules and redraw every figure.

    paper_dir is the paper's Git repository; workflow/rules.json names the scripts
    (figure_scripts) and may name the interpreter that draws them (figure_python). draft_dir is a
    revised copy to check instead; empty checks the working tree. The scripts are run, so
    the figure files they save are replaced. out_dir is the report folder (default
    <paper_dir>/drafts/paper_figures).
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not (paper / ".git").exists():
        raise ValueError(f"{paper} is not a Git repository")
    root = Path(draft_dir).expanduser().resolve() if draft_dir else paper
    if not root.is_dir():
        raise ValueError(f"{root} is not a folder")
    rules = paper_rules.load(paper)
    trail = Trail("paper_figures", {"paper_dir": str(paper), "draft_dir": draft_dir}, ROLES)

    found = scripts(root, rules.figure_scripts)
    names = [s.relative_to(root).as_posix() for s in found]
    trail.step("find", patterns=list(rules.figure_scripts), scripts=names)
    findings = [] if found else [finding("FAIL", "scripts", "", f"no plotting script matches {list(rules.figure_scripts)}")]

    allowed = palette()
    for script, name in zip(found, names):
        findings += read(script.read_text(encoding="utf-8", errors="replace"), name, allowed)
    trail.step("read", findings=[f for f in findings if f["check"] != "scripts"])

    check_cancelled()
    python = interpreter(rules.figure_python)
    if found and not python:
        findings.append(finding("FAIL", "python", "", (f"{rules.figure_python} is missing or cannot import matplotlib" if rules.figure_python
                                                       else "no python3 here can import matplotlib") + "; "
                                                       "set figure_python in workflow/rules.json to one that does"))
    rows = []
    for script, name in zip(found, names):
        figures = []
        if python and not any(f["script"] == name and f["check"] == "syntax" for f in findings):
            check_cancelled()
            problems, figures = draw(root, script, python)
            findings += problems
            trail.step(f"draw {name}", figures=figures, problems=[p["message"] for p in problems])
        rows.append({"script": name, "figures": figures})

    out = Path(out_dir).expanduser() if out_dir else paper / "drafts" / "paper_figures"
    out = out / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    status = status_of(findings)
    counts = {level: sum(f["level"] == level for f in findings) for level in ("FAIL", "WARN")}
    record = {"status": status, "root": str(root), "python": python,
              "scripts": rows, "findings": findings}
    (out / "figures.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(record), encoding="utf-8")
    trail.step("report", status=status, **counts)
    run = trail.save(out, report=out / "report.md", json=out / "figures.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "scripts": len(rows), "figures": [f for r in rows for f in r["figures"]], "counts": counts,
                       "problems": [f"{f['script']}: {f['message']}" if f["script"] else f["message"] for f in findings]},
                      ensure_ascii=False)


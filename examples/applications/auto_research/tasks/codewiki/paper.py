"""The paper side of the wiki, in plain code: the parts of the method a code base should
implement. They are read from the LaTeX with deep_read's structure helpers:

- method subsections: the sections under a method-like section (Method, Approach, Model,
  Architecture, Framework, Algorithm, Training, ...) that have text of their own; the
  section itself when it has no subsection;
- equations: every numbered display row inside a method section, and every display row
  with a \\label anywhere;
- algorithms: every algorithm float, with its caption, label and body.
"""

import re
from pathlib import Path

from ..deepread import source as dsource
from ..deepread import structure
from ..submission.common import main_tex

METHOD = re.compile(r"\b(?:method|methods|methodology|approach|approaches|model|models|architecture|framework|"
                    r"algorithm|algorithms|proposed|training|learning|our|technique|formulation|design|"
                    r"implementation|pipeline|system|objective|optimization|inference)\b", re.I)
NOT_METHOD = re.compile(r"\b(?:related|background|introduction|conclusion|conclusions|discussion|experiment|experiments|"
                        r"experimental|results|evaluation|limitations|acknowledg\w*|abstract|appendix|front matter|"
                        r"why|ethics|broader|impact|future)\b", re.I)
ALGORITHM = re.compile(r"\\begin\{(algorithm\*?)\}(.*?)\\end\{\1\}", re.S)
TEXT_CHARS = 2_500       # of a unit's text shown to the mapper
LATEX_FILES = (".tex", ".ltx", ".bbl", ".bib")
SKIP = {".git", "drafts", "workflow", "node_modules", ".venv", "venv"}


def paper_files(paper: Path) -> dict[str, str]:
    files = {}
    for path in sorted(paper.rglob("*")):
        rel = path.relative_to(paper)
        if path.suffix in LATEX_FILES and path.is_file() and not set(rel.parts[:-1]) & SKIP \
                and path.stat().st_size < 4_000_000:
            files[rel.as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return files


def read(paper: Path) -> dict:
    """The flattened LaTeX of the paper's main file and its document structure."""
    main = main_tex(paper)
    if main is None:
        raise ValueError(f"{paper} has no main .tex file (rules.json main_tex, or a top-level .tex with \\documentclass)")
    files = paper_files(paper)
    tex = dsource.flatten(files, main.relative_to(paper).as_posix())
    if "\\begin{document}" not in tex:
        raise ValueError(f"{main} has no \\begin{{document}}")
    doc = structure.latex_document({"tex": tex, "files": files})
    doc["tex"] = tex
    doc["all_sections"] = structure.Latex(tex).sections     # with the sections that have no text of their own
    doc["main"] = main.relative_to(paper).as_posix()
    return doc


def method_sections(sections: list[dict]) -> set[str]:
    """Ids of the sections inside a method-like top-level section."""
    out, inside = set(), False
    for s in sections:
        if s["level"] <= 1:
            title = s["title"]
            inside = bool(METHOD.search(title)) and not NOT_METHOD.search(title)
        if inside:
            out.add(s["id"])
    return out


def name(s: dict) -> str:
    return f"{s['number']} {s['title']}".strip()


def units(doc: dict) -> list[dict]:
    """The method subsections, equations and algorithms, each with the text the mapper
    reads and the text a paper quote is checked against."""
    sections = doc.get("all_sections") or doc["sections"]
    method = method_sections(sections)
    paras = {}
    for p in doc["paragraphs"]:
        paras.setdefault(p["section"], []).append(p)
    disp = {}
    for d in doc["displays"]:
        disp.setdefault(d["section"], []).append(d)
    own = {s["id"] for s in sections if paras.get(s["id"]) or disp.get(s["id"])}
    out = []
    for k, s in enumerate(sections):
        if s["id"] not in method or s["id"] not in own:
            continue
        deeper = []
        for t in sections[k + 1:]:
            if t["level"] <= s["level"]:
                break
            deeper.append(t)
        if any(t["id"] in own for t in deeper) and s["level"] <= 1:
            continue                       # a method section's overview text, when it has subsections
        text = " ".join(p["text"] for p in paras.get(s["id"], []))
        latex = [d["latex"] for d in disp.get(s["id"], [])]
        out.append({"id": f"sec:{s['number'] or s['id']}", "kind": "subsection", "title": name(s), "section": name(s),
                    "label": s.get("label", ""), "text": text[:TEXT_CHARS], "latex": latex,
                    "check": [p["text"] for p in paras.get(s["id"], [])] + latex})
    titles = {s["id"]: name(s) for s in sections}
    by_pid = {p["id"]: p for p in doc["paragraphs"]}
    order = doc.get("order", [])
    for d in doc["displays"]:
        for row in d["rows"]:
            if not row["number"] or not (row["label"] or d["section"] in method):
                continue
            at = order.index(d["id"]) if d["id"] in order else -1
            before = next((by_pid[x]["text"] for x in reversed(order[:at]) if x in by_pid), "") if at >= 0 else ""
            after = next((by_pid[x]["text"] for x in order[at + 1:] if x in by_pid), "") if at >= 0 else ""
            out.append({"id": f"eq:{row['number']}", "kind": "equation", "title": f"Equation ({row['number']})",
                        "section": titles.get(d["section"], ""), "label": row["label"], "latex": [row["latex"]],
                        "text": (before[-700:] + " [EQUATION] " + after[:500]).strip(),
                        "check": [row["latex"], d["latex"], before, after]})
    for n, m in enumerate(ALGORITHM.finditer(doc["tex"]), 1):
        body = m.group(2)
        cap = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*(?=\{)", body)
        caption = structure.readable(structure.group(body, cap.end())[0], {}, {}) if cap else ""
        label = re.search(r"\\label\{([^}]*)\}", body)
        steps = re.sub(r"\\caption\s*(?:\[[^\]]*\])?\s*\{.*?\}\s*|\\label\{[^}]*\}", " ", body, flags=re.S)
        out.append({"id": f"alg:{n}", "kind": "algorithm", "title": f"Algorithm {n}" + (f": {caption}" if caption else ""),
                    "section": "", "label": label.group(1) if label else "", "latex": [steps.strip()[:TEXT_CHARS]],
                    "text": caption, "check": [caption, body, structure.readable(steps, {}, {})]})
    return out


def brief(u: dict) -> dict:
    """What the mapper sees of one unit."""
    out = {"id": u["id"], "kind": u["kind"], "title": u["title"], "section": u["section"], "text": u["text"]}
    if u["latex"]:
        out["latex"] = [x[:1_500] for x in u["latex"]][:6]
    return out


__all__ = ["brief", "method_sections", "read", "units"]

"""What the three submission tools share: the paper repository, its main .tex, the files
that .tex actually uses, LaTeX comment stripping and the venue text. Plain code."""

import re
import subprocess
from datetime import datetime
from pathlib import Path

from ..revision.rules import paper as paper_rules

GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".svg")
VERBATIM = ("verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "alltt")
# Commands whose argument names a file, and how each is resolved.
FILE_COMMANDS = re.compile(
    r"\\(input|include|subfile|includegraphics|includepdf|lstinputlisting|bibliography|bibliographystyle|"
    r"addbibresource|usepackage|RequirePackage|documentclass)\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")
GRAPHICSPATH = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^}]*\})+)\s*\}")
DOUBLE_BLIND = re.compile(r"double[- ]blind|anonymi[sz]ed (?:submission|review)|anonymous (?:submission|review)", re.I)


def repo(paper_dir: str) -> Path:
    """The paper's Git repository; anything else raises ValueError."""
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"{paper} is not a directory")
    done = subprocess.run(["git", "-C", str(paper), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if done.returncode != 0:
        raise ValueError(f"{paper} is not a Git repository")
    return paper


def run_folder(paper: Path, out_dir: str, default: str) -> Path:
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / default
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    return out


def main_tex(paper: Path) -> Path | None:
    """rules.json's main_tex when it exists, else the one top-level .tex with \\documentclass."""
    try:
        named = paper / paper_rules.load(paper).main_tex
    except ValueError:
        named = None
    if named and named.is_file():
        return named
    for tex in sorted(paper.glob("*.tex")):
        if re.search(r"^\s*\\documentclass", tex.read_text(encoding="utf-8", errors="ignore"), re.M):
            return tex
    return None


def venue_text(paper: Path) -> str:
    """workflow/venue.md, the venue part of rules.json's notes and its writing rules."""
    path = paper / "workflow" / "venue.md"
    text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    try:
        rules = paper_rules.load(paper)
        text += "\n" + "\n".join(str(v) for v in rules.notes.values()) + "\n" + rules.writing_rules
    except ValueError:
        pass
    return text


def double_blind(paper: Path) -> bool:
    return bool(DOUBLE_BLIND.search(venue_text(paper)))


def page_limit(paper: Path) -> int | None:
    """The page limit written in venue.md ("8 pages", "page limit: 9"), else rules.json's
    body_page_limit when the paper's rules.json sets it."""
    text = venue_text(paper)
    for pattern in (r"page[- ]limit\s*(?:is|:|of)?\s*(\d{1,2})", r"(\d{1,2})\s*(?:content\s+|main\s+|body\s+)?pages?\b"):
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    path = paper / paper_rules.FILE
    if path.is_file() and '"body_page_limit"' in path.read_text(encoding="utf-8", errors="ignore"):
        return int(paper_rules.load(paper).body_page_limit)
    return None


# Comments --------------------------------------------------------------------------

def _comment_at(line: str) -> int:
    """Index of the % that starts a comment, or -1. \\% is text; so is % inside \\url{} or \\href{}."""
    i = 0
    while True:
        i = line.find("%", i)
        if i < 0:
            return -1
        slashes = len(line[:i]) - len(line[:i].rstrip("\\"))
        if slashes % 2 == 0 and not _in_url(line[:i]):
            return i
        i += 1


def _in_url(prefix: str) -> bool:
    start = max(prefix.rfind("\\url{"), prefix.rfind("\\href{"))
    return start >= 0 and prefix.count("{", start) > prefix.count("}", start)


def strip_comments(text: str) -> str:
    """LaTeX source without comments, as arxiv_latex_cleaner does it: a whole-line comment
    goes, a trailing one leaves its % (which keeps the line break suppressed), \\% stays,
    verbatim-like environments stay as written and \\begin{comment} blocks go."""
    out, verbatim, comment = [], "", False
    for line in text.splitlines(keepends=True):
        if comment:
            comment = not re.search(r"\\end\{comment\}", line)
            continue
        if verbatim:
            out.append(line)
            if f"\\end{{{verbatim}}}" in line:
                verbatim = ""
            continue
        if re.match(r"\s*\\begin\{comment\}", line):
            comment = not re.search(r"\\end\{comment\}", line)
            continue
        cut = _comment_at(line)
        code = line if cut < 0 else line[:cut]
        begin = re.search(r"\\begin\{(" + "|".join(re.escape(v) for v in VERBATIM) + r")\}", code)
        if begin and f"\\end{{{begin.group(1)}}}" not in line:
            out.append(line)
            verbatim = begin.group(1)
            continue
        if cut < 0:
            out.append(line)
        elif code.strip():
            out.append(code + "%\n")
        # a whole-line comment is dropped
    return "".join(out)


# The files the main .tex uses --------------------------------------------------------

def _find(paper: Path, name: str, exts: tuple[str, ...], dirs: list[str]) -> Path | None:
    name = name.strip()
    for d in dirs or [""]:
        base = paper / d / name
        for candidate in [base] + [base.with_name(base.name + e) for e in exts]:
            if candidate.is_file() and paper in candidate.resolve().parents:
                return candidate
    return None


def used_files(paper: Path, main: Path) -> tuple[dict[str, str], list[str]]:
    """{relative path: what uses it} for every repository file the main .tex reaches, and the
    files it names that are missing. Packages, classes and styles that are not in the
    repository are the TeX installation's and are not missing."""
    used: dict[str, str] = {main.relative_to(paper).as_posix(): "main"}
    missing: list[str] = []
    graphics_dirs: list[str] = [""]
    queue = [main]
    while queue:
        tex = queue.pop(0)
        text = strip_comments(tex.read_text(encoding="utf-8", errors="ignore"))
        for group in GRAPHICSPATH.findall(text):
            graphics_dirs += [d.strip() for d in re.findall(r"\{([^}]*)\}", group)]
        for command, arg in FILE_COMMANDS.findall(text):
            for name in [n.strip() for n in arg.split(",") if n.strip()]:
                kind, exts, required, dirs = _kind(command, graphics_dirs)
                found = _find(paper, name, exts, dirs)
                if found is None:
                    if required:
                        missing.append(f"{tex.relative_to(paper).as_posix()}: \\{command}{{{name}}}")
                    continue
                rel = found.relative_to(paper).as_posix()
                if rel not in used:
                    used[rel] = kind
                    if found.suffix in (".tex", ".sty", ".cls") and kind in ("tex", "style"):
                        queue.append(found)
    bbl = main.with_suffix(".bbl")
    if bbl.is_file():
        used.setdefault(bbl.relative_to(paper).as_posix(), "bbl")
    return used, missing


def _kind(command: str, graphics_dirs: list[str]):
    if command in ("input", "include", "subfile"):
        return "tex", (".tex",), True, [""]
    if command in ("includegraphics", "includepdf"):
        return "figure", GRAPHIC_EXTS, True, list(dict.fromkeys(graphics_dirs))
    if command == "lstinputlisting":
        return "listing", (), True, [""]
    if command in ("bibliography", "addbibresource"):
        return "bib", (".bib",), False, [""]
    if command == "bibliographystyle":
        return "style", (".bst",), False, [""]
    if command == "documentclass":
        return "style", (".cls",), False, [""]
    return "style", (".sty",), False, [""]


def sources(paper: Path, main: Path) -> str:
    """The comment-free text of every .tex the main file reaches."""
    used, _ = used_files(paper, main)
    return "\n".join(strip_comments((paper / p).read_text(encoding="utf-8", errors="ignore"))
                     for p, kind in used.items() if p.endswith(".tex") and kind in ("main", "tex"))


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 100_000 else f"{n / 1000:.1f} KB"

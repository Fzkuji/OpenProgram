"""The material a lab meeting reads, and the checks on what its members say. Plain code.

Sources are the project files with line numbers; a citation is `path:line`,
`path:start-end` or a row id (C1, E1, a results.tsv commit, X1 for the first
experiment_plan.csv row, F1 for a finding). A statement's numbers must occur in the
lines it cites; a quote must be found in them.
"""

import re
from pathlib import Path

from auto_research import stages

from ..submission.common import main_tex

WORKFLOW_FILES = ("project.md", "claims.md", "evidence.csv", "results.tsv", "experiment.md", "experiment_plan.csv",
                  "findings.md", "baselines.csv")
FILE_CHARS = 30_000         # characters of one source shown to the members
PAPER_CHARS = 60_000
MEETING_CHARS = 20_000
CODE_CHARS = 20_000         # one file of experiment.md's Edit (the code and configs the runs use)
CODE_FILES = 5
MOST_LINES = 30             # a citation spans at most this many lines
MEETINGS = "meetings"

# A number as a statement writes it: not part of a word, an id or a path (C1, ResNet-152, ViT-H/14).
STATED = re.compile(r"(?<![\w.\-/])(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?!\w)")
# Every number a source line holds, however it is written.
WRITTEN = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
CITE = re.compile(r"^(?P<path>[^:\s]+?):(?P<start>\d+)(?:\s*-\s*(?P<end>\d+))?$")
REMOVED = "[number removed]"


def meetings_folder(paper: Path) -> Path:
    return stages.folder(paper) / MEETINGS


def last_meeting(paper: Path) -> Path | None:
    """The newest meeting file of workflow/meetings/ (by modification time, then name)."""
    folder = meetings_folder(paper)
    files = [p for p in folder.glob("*.md") if p.is_file()] if folder.is_dir() else []
    return max(files, key=lambda p: (p.stat().st_mtime, p.name)) if files else None


def summary_of(text: str) -> str:
    """The summary part of a meeting file (from its "## Summary" heading on)."""
    at = text.find("\n## Summary")
    return text[at + 1:].strip() if at >= 0 else ""


def gather(paper: Path) -> tuple[dict[str, list[str]], Path | None]:
    """{relative path: lines} of the project files that exist (with the code and config
    files experiment.md's Edit names), and the last meeting."""
    found: dict[str, list[str]] = {}
    for name in WORKFLOW_FILES:
        path = stages.folder(paper) / name
        if path.is_file():
            found[f"{stages.FOLDER}/{name}"] = cut(path.read_text(encoding="utf-8", errors="replace"), FILE_CHARS)
    for name in edit_files(found.get(f"{stages.FOLDER}/experiment.md", [])):
        path = paper / name
        if name not in found and path.is_file() and path.stat().st_size <= 4 * CODE_CHARS:
            found[name] = cut(path.read_text(encoding="utf-8", errors="replace"), CODE_CHARS)
    tex = main_tex(paper)
    if tex is not None:
        found[tex.relative_to(paper).as_posix()] = cut(tex.read_text(encoding="utf-8", errors="replace"), PAPER_CHARS)
    last = last_meeting(paper)
    if last is not None:
        found[last.relative_to(paper).as_posix()] = cut(last.read_text(encoding="utf-8", errors="replace"),
                                                        MEETING_CHARS)
    return found, last


def edit_files(spec: list[str]) -> list[str]:
    """The files experiment.md's Edit line names, at most CODE_FILES."""
    line = next((x for x in spec if x.startswith("Edit:")), "")
    names = [n.strip() for n in line[len("Edit:"):].split(",") if n.strip()]
    return [n for n in names if ".." not in Path(n).parts and not Path(n).is_absolute()][:CODE_FILES]


def cut(text: str, limit: int) -> list[str]:
    lines, used = [], 0
    for line in text.splitlines():
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def numbered(sources: dict[str, list[str]]) -> str:
    return "\n\n".join(f"=== {path}\n" + "\n".join(f"{i}| {line}" for i, line in enumerate(lines, 1))
                       for path, lines in sources.items())


def row_ids(sources: dict[str, list[str]]) -> dict[str, tuple[str, int]]:
    """Row ids and where they are: claims C1, evidence ids, results.tsv commits, X1... for
    experiment_plan.csv rows in order, F1... for findings."""
    ids: dict[str, tuple[str, int]] = {}
    for path, lines in sources.items():
        name = path.rsplit("/", 1)[-1]
        if not path.startswith(stages.FOLDER + "/"):
            continue
        for i, line in enumerate(lines, 1):
            if name == "claims.md" and (m := re.match(r"^(C\d+):", line)):
                ids.setdefault(m.group(1), (path, i))
            elif name == "findings.md" and (m := re.match(r"^##\s+(F\d+)\b", line)):
                ids.setdefault(m.group(1), (path, i))
            elif name in ("evidence.csv", "results.tsv") and i > 1 and line.strip():
                key = re.split(r"[,\t]", line, maxsplit=1)[0].strip()
                if key:
                    ids.setdefault(key, (path, i))
        if name == "experiment_plan.csv":
            rows = [i for i, line in enumerate(lines, 1) if i > 1 and line.strip()]
            for k, i in enumerate(rows, 1):
                ids.setdefault(f"X{k}", (path, i))
    return ids


def resolve(cite: str, sources: dict[str, list[str]], ids: dict[str, tuple[str, int]]) -> tuple[str, int, int] | None:
    """(path, first line, last line) of a citation, or None when it points nowhere."""
    cite = cite.strip().strip("`[]()")
    if cite in ids:
        path, line = ids[cite]
        return path, line, line
    m = CITE.match(cite)
    if not m:
        return None
    path = source_path(m.group("path"), sources)
    if path is None:
        return None
    start = int(m.group("start"))
    end = int(m.group("end") or start)
    if not 1 <= start <= end <= len(sources[path]) or end - start >= MOST_LINES:
        return None
    return path, start, end


def source_path(given: str, sources: dict[str, list[str]]) -> str | None:
    given = given.lstrip("./")
    if given in sources:
        return given
    matches = [p for p in sources if p.endswith("/" + given) or p.rsplit("/", 1)[-1] == given]
    return matches[0] if len(matches) == 1 else None


def cited_text(spans: list[tuple[str, int, int]], sources: dict[str, list[str]]) -> str:
    return "\n".join("\n".join(sources[p][s - 1:e]) for p, s, e in spans)


def squash(text: str) -> str:
    return " ".join(text.replace("~", " ").replace("\\", "").split()).casefold()


def decimals(stated: str) -> int | None:
    if re.search(r"[eE]", stated):
        return None
    return len(stated.split(".", 1)[1]) if "." in stated else 0


def number_found(stated: str, written: list[str]) -> bool:
    """The stated number is one of the written ones, or one of them rounded to its digits."""
    value, places = float(stated), decimals(stated)
    for w in written:
        source = float(w)
        if abs(source - value) <= 1e-12 * max(1.0, abs(value)):
            return True
        if places is not None and places > 0 and round(source, places) == value:
            return True
    return False


def check_statement(item: dict, sources: dict[str, list[str]], ids: dict[str, tuple[str, int]],
                    agenda: str) -> dict:
    """One statement after the checks: its valid citations, the quote only when found
    in them, numbers that its citations (or the agenda) do not hold replaced, and why."""
    kind = item.get("kind", "fact")
    text = " ".join(str(item.get("text", "")).split())
    cites = [str(c) for c in item.get("cites", []) if str(c).strip()]
    quote = " ".join(str(item.get("quote", "")).split())
    flags, spans, good = [], [], []
    for c in cites:
        span = resolve(c, sources, ids)
        if span is None:
            flags.append(f"citation {c} points to no line of the project files")
        else:
            spans.append(span)
            good.append(c.strip().strip("`[]()"))
    body = cited_text(spans, sources)
    if quote and squash(quote) not in squash(body):
        flags.append("the quote is not in the cited lines")
        quote = ""
    if kind == "fact" and not spans:
        flags.append("a factual statement without a valid citation")
    if kind != "proposal":
        written = WRITTEN.findall(body) + WRITTEN.findall(agenda)
        missing = [m.group(1) for m in STATED.finditer(text) if not number_found(m.group(1), written)]
        if missing:
            flags.append(f"numbers not in the cited lines removed: {', '.join(dict.fromkeys(missing))}")
            text = STATED.sub(lambda m: m.group(0) if number_found(m.group(1), written) else REMOVED, text)
    return {"kind": kind, "text": text, "cites": good, "quote": quote, "flags": flags}


__all__ = ["REMOVED", "check_statement", "gather", "last_meeting", "meetings_folder", "numbered", "resolve",
           "row_ids", "summary_of"]

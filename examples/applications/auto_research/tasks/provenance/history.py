"""What the repository records about each line of the paper. Plain code, no model calls.

Commits are read once from git log and classed: ai when a Co-Authored-By trailer names
an AI tool (ai_disclosure's detection) or the commit was made by an Auto Research run
(its auto_research identity, or an ablation / sweep trial / campaign message); human
otherwise. git blame --porcelain -w -M -C gives each line of the working tree its
commit (all zeros for an uncommitted line). The edits of paper_proofread,
writing_style and caption_check runs (drafts/**/edits.json) are matched to the
commit that applied them: its version of the file has the new text, its parent's has
the old one.
"""

import json
import re
import subprocess
from pathlib import Path

from ..submission.disclosure import AI_NAME, TRAILER

UNCOMMITTED = "0" * 40
AUTO_NAME = "auto_research"
AUTO_MESSAGE = re.compile(r"^(?:ablation: |sweep trial \d+:|Auto Research\b)", re.I)
EDIT_WORKFLOWS = {"paper_proofread": "论文校对", "writing_style": "写作风格", "caption_check": "图表标题检查"}
FILE_LOG_LIMIT = 300


def git(paper: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(paper), *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def commits(paper: Path) -> dict[str, dict]:
    """Every commit reachable from HEAD: author, date, subject, AI tools and class."""
    out = git(paper, "log", "--format=\x1e%H\x1f%an\x1f%ae\x1f%aI\x1f%B") or ""
    found = {}
    for block in out.split("\x1e")[1:]:
        parts = block.split("\x1f")
        if len(parts) < 5:
            continue
        sha, name, email, date, body = parts
        tools = sorted({re.sub(r"\s*<[^>]*>", "", t).strip() for t in TRAILER.findall(body) if AI_NAME.search(t)})
        subject = body.strip().splitlines()[0] if body.strip() else ""
        auto = name == AUTO_NAME or email.startswith(AUTO_NAME + "@") or bool(AUTO_MESSAGE.match(subject))
        if auto:
            tools = sorted(set(tools) | {"Auto Research"})
        found[sha] = {"sha": sha, "short": sha[:7], "author": name, "date": date, "subject": subject,
                      "tools": tools, "auto": auto, "class": "ai" if tools else "human"}
    return found


def blame(paper: Path, rel: str, lines: int) -> list[dict]:
    """For each line of the working-tree file: {sha, file} of the commit that last
    changed it (whitespace ignored, moves and copies followed). A file Git does not
    know is uncommitted throughout."""
    out = git(paper, "blame", "--porcelain", "-w", "-M", "-C", "--", rel)
    result = [{"sha": UNCOMMITTED, "file": rel} for _ in range(lines)]
    if out is None:
        return result
    names: dict[str, str] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("\t"):
            continue
        head = re.match(r"^([0-9a-f]{40}) (\d+) (\d+)(?: \d+)?$", line)
        if head:
            current = {"sha": head.group(1), "final": int(head.group(3)), "file": names.get(head.group(1), rel)}
            if 0 < current["final"] <= lines:
                result[current["final"] - 1] = current
            continue
        if current is not None and line.startswith("filename "):
            current["file"] = names[current["sha"]] = line[len("filename "):]
    return [{"sha": r["sha"], "file": r["file"]} for r in result]


def dirty(paper: Path, files: list[str]) -> list[str]:
    """The paper files with uncommitted changes (modified, added or untracked)."""
    out = git(paper, "status", "--porcelain", "--", *files) or ""
    return sorted({line[3:].strip() for line in out.splitlines() if line.strip()})


class Files:
    """File contents at commits, cached."""

    def __init__(self, paper: Path):
        self.paper = paper
        self.cache: dict[tuple[str, str], str | None] = {}
        self.parents: dict[str, str | None] = {}
        self.ancestors: dict[tuple[str, str], bool] = {}
        self.splits: dict[tuple[str, str], list] = {}    # paragraphs of a file at a commit, for the replay

    def at(self, sha: str, rel: str) -> str | None:
        key = (sha, rel)
        if key not in self.cache:
            self.cache[key] = git(self.paper, "show", f"{sha}:./{rel}")
        return self.cache[key]

    def parent(self, sha: str) -> str | None:
        if sha not in self.parents:
            out = git(self.paper, "rev-parse", "--verify", "--quiet", f"{sha}^")
            self.parents[sha] = out.strip() if out else None
        return self.parents[sha]

    def ancestor(self, older: str, newer: str) -> bool:
        """older is a proper ancestor of newer."""
        key = (older, newer)
        if key not in self.ancestors:
            done = subprocess.run(["git", "-C", str(self.paper), "merge-base", "--is-ancestor", older, newer],
                                  capture_output=True, text=True)
            self.ancestors[key] = older != newer and done.returncode == 0
        return self.ancestors[key]

    def log(self, rel: str, start: str = "HEAD") -> list[str]:
        """Commits from start back that changed the file, newest first."""
        out = git(self.paper, "log", f"-n{FILE_LOG_LIMIT}", "--format=%H", start, "--", rel) or ""
        return [l.strip() for l in out.splitlines() if l.strip()]


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_edits(paper: Path) -> list[dict]:
    """Every edit of a proofread, writing_style or caption_check run under drafts/:
    {workflow, run, id, file, quote, replacement}."""
    found = []
    drafts = paper / "drafts"
    if not drafts.is_dir():
        return found
    for path in sorted(drafts.rglob("edits.json")):
        workflow = ""
        run = path.parent / "run.json"
        if run.is_file():
            try:
                workflow = str(json.loads(run.read_text(encoding="utf-8")).get("workflow") or "")
            except ValueError:
                workflow = ""
        if not workflow:
            workflow = path.relative_to(drafts).parts[0]
        if workflow not in EDIT_WORKFLOWS:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for e in data.get("edits") or [] if isinstance(data, dict) else []:
            if not isinstance(e, dict) or not e.get("quote") or not e.get("replacement"):
                continue
            found.append({"workflow": workflow, "run": path.parent.relative_to(paper).as_posix(),
                          "id": str(e.get("id") or ""), "file": str(e.get("file") or ""),
                          "quote": str(e["quote"]), "replacement": str(e["replacement"])})
    return found


def applied(files: Files, sha: str, rel: str, edits: list[dict]) -> list[dict]:
    """The edits this commit applied to rel: the new text is in its version and the
    old text, without the new, in its parent's."""
    if sha == UNCOMMITTED:
        return []
    mine = [e for e in edits if not e["file"] or e["file"] == rel or Path(e["file"]).name == Path(rel).name]
    if not mine:
        return []
    now = collapse(files.at(sha, rel))
    parent = files.parent(sha)
    before = collapse(files.at(parent, rel)) if parent else ""
    out = []
    for e in mine:
        new, old = collapse(e["replacement"]), collapse(e["quote"])
        if new and new in now and old in before and new not in before:
            out.append(e)
    return out


__all__ = ["Files", "UNCOMMITTED", "applied", "blame", "commits", "dirty", "load_edits"]

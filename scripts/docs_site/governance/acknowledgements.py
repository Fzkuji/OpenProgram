"""Decisions that a change needs no doc update.

An acknowledgement is keyed by page, changed file and the blobs the decision
went from and to. It survives a commit, a further edit asks again, and a range
counts only when acknowledged steps chain from the range's base to its tip.
The store sits in the git common directory: shared by worktrees, never
committed, and harmless to lose.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

STORE = "openprogram-docs/acknowledgements.jsonl"
DELETED = "deleted"
MINIMUM_REASON = 10


def _run(root: Path, *arguments: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, input=stdin, capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {arguments[0]} failed: {done.stderr.strip()}")
    return done.stdout


def store_path(root: Path) -> Path:
    common = Path(_run(root, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = root / common
    return common / STORE


def blobs(scope) -> dict[str, str]:
    """The tip blob of every changed non-docs file."""
    root = Path(scope.root)
    changed = [path for path in scope.changed if not path.startswith("docs/")]
    if scope.mode == "staged":
        found: dict[str, str] = {}
        for path in changed:
            entry = _run(root, "ls-files", "-s", "--", path).split()
            found[path] = entry[1] if entry else DELETED
        return found
    if scope.reads == "head":
        return _at_revision(root, "HEAD", changed)
    present = [path for path in changed if (root / path).is_file()]
    hashes = _run(root, "hash-object", "--stdin-paths", stdin="\n".join(present) + "\n").split() if present else []
    found = dict(zip(present, hashes))
    return {path: found.get(path, DELETED) for path in changed}


def _at_revision(root: Path, revision: str, paths: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for start in range(0, len(paths), 500):
        listing = _run(root, "ls-tree", "-r", "-z", revision, "--", *paths[start:start + 500])
        for row in listing.split("\0"):
            if row:
                info, name = row.split("\t", 1)
                found[name] = info.split()[2]
    return {path: found.get(path, DELETED) for path in paths}


def base_blobs(scope) -> dict[str, str]:
    """The blob each changed non-docs file had where this change starts."""
    changed = [path for path in scope.changed if not path.startswith("docs/")]
    return _at_revision(Path(scope.root), scope.base or "HEAD", changed)


def settled(known: set[tuple[str, str, str, str]], page: str, changed: str, start: str, tip: str) -> bool:
    """Whether acknowledged steps lead from ``start`` to ``tip`` for this page and file."""
    steps = {(old, new) for p, c, old, new in known if p == page and c == changed}
    reached: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        for old, new in steps:
            if old == current and new not in reached:
                reached.add(new)
                pending.append(new)
    return tip in reached


def acknowledged(scope) -> set[tuple[str, str, str, str]]:
    """Every (page, changed file, from blob, to blob) already decided."""
    path = store_path(Path(scope.root))
    keys: set[tuple[str, str, str, str]] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return keys
    for line in lines:
        try:
            entry = json.loads(line)
            keys.add((entry["page"], entry["changed"], entry["from"], entry["to"]))
        except (ValueError, KeyError, TypeError):
            continue
    return keys


def _page_key(root: Path, page: str) -> str:
    name = page if page.startswith("docs/") else f"docs/{page}"
    name = name.removesuffix(".zh.html") + ".html" if name.endswith(".zh.html") else name
    if not name.endswith(".html") or not (root / name).is_file():
        raise ValueError(f"{page} is not a documentation page")
    return name


def record(scope, pages: list[str], reason: str, listed: dict[str, set[str]] | None = None) -> int:
    """Record that each named page was checked for this change. Returns how many records were added.

    ``listed`` is the impact report, page -> changed files: a page it does not
    name is refused, and only the files that point at a page are recorded.
    """
    root = Path(scope.root)
    reason = reason.strip()
    if len(reason) < MINIMUM_REASON:
        raise ValueError(f"a reason of at least {MINIMUM_REASON} characters is required")
    if not pages:
        raise ValueError("name each page that was checked; there is no blanket form")
    names = [_page_key(root, page) for page in pages]
    tips = blobs(scope)
    if not tips:
        raise ValueError("this change has no code file to acknowledge")
    if listed is not None:
        unlisted = [page for page in names if page not in listed]
        if unlisted:
            raise ValueError(f"not in this change's impact report: {', '.join(unlisted)}")
    starts = base_blobs(scope)
    known = acknowledged(scope)
    moment = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    for page in dict.fromkeys(names):
        for changed, blob in sorted(tips.items()):
            if listed is not None and changed not in listed[page]:
                continue
            start = starts.get(changed, DELETED)
            if (page, changed, start, blob) not in known:
                lines.append(json.dumps({"page": page, "changed": changed, "from": start, "to": blob,
                                         "reason": reason, "at": moment}, ensure_ascii=False))
    if lines:
        path = store_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Binary: a text-mode descriptor on Windows would end each row CRLF.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
        try:
            os.write(descriptor, ("\n".join(lines) + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)
    return len(lines)

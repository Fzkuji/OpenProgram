"""Load the pages a check reads, from disk or from the git index."""
from __future__ import annotations

import subprocess
import importlib.util
import re
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

from .. import html_source, nav
from . import policy
from .model import Corpus, Page, Scope

_TEXT = (".html", ".md", ".svg")
_NOT_SOURCES = {"_site", "_site.tmp", "_site.old", "node_modules"}


def is_generated(rel: str) -> bool:
    return rel.startswith(policy.GENERATED_PREFIXES)


def _disk_files(docs_root: Path) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    if not docs_root.is_dir():
        return found
    for path in docs_root.rglob("*"):
        rel = PurePosixPath(path.relative_to(docs_root).as_posix())
        if rel.parts[0] in _NOT_SOURCES or not path.is_file():
            continue
        found[rel.as_posix()] = (path.read_text(encoding="utf-8", errors="replace")
                                 if path.suffix in _TEXT else None)
    return found


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True,
                          check=True).stdout


def _tree_names(root: Path, revision: str | None) -> list[str]:
    listing = (_git(root, "ls-files", "-z", "--", "docs") if revision is None
               else _git(root, "ls-tree", "-r", "-z", "--name-only", revision, "--", "docs"))
    return [name.decode() for name in listing.split(b"\0") if name]


def _snapshot_files(root: Path, revision: str | None = None) -> dict[str, str | None]:
    """Every file under docs/ from the index or a revision, read through one ``cat-file --batch``."""
    names = _tree_names(root, revision)
    found: dict[str, str | None] = {}
    wanted: list[str] = []
    for name in names:
        rel = name[len("docs/"):]
        if PurePosixPath(rel).parts[0] in _NOT_SOURCES:
            continue
        found[rel] = None
        if PurePosixPath(rel).suffix in _TEXT:
            wanted.append(name)
    if wanted:
        spec = f"{revision}:" if revision else ":"
        output = _git(root, "cat-file", "--batch", stdin="".join(f"{spec}{n}\n" for n in wanted).encode())
        offset = 0
        for name in wanted:
            end = output.index(b"\n", offset)
            header = output[offset:end].split()
            offset = end + 1
            if header[-1] == b"missing":
                continue
            size = int(header[2])
            found[name[len("docs/"):]] = output[offset:offset + size].decode("utf-8", "replace")
            offset += size + 1
    return found


def read_file(scope: Scope, path: str) -> str:
    if scope.reads == "disk":
        return (scope.root / path).read_text(encoding="utf-8", errors="replace")
    spec = f":{path}" if scope.reads == "index" else f"HEAD:{path}"
    return _git(scope.root, "show", spec).decode("utf-8", "replace")


def read_many(root: Path, revision: str, paths: list[str]) -> dict[str, str | None]:
    """Files at a revision; None for one the revision does not hold, or with no such history."""
    found: dict[str, str | None] = dict.fromkeys(paths)
    try:
        output = _git(root, "cat-file", "--batch", stdin="".join(f"{revision}:{p}\n" for p in paths).encode())
    except (subprocess.CalledProcessError, OSError):
        return found
    offset = 0
    for path in paths:
        end = output.index(b"\n", offset)
        header = output[offset:end].split()
        offset = end + 1
        if header[-1] in {b"missing", b"ambiguous"}:
            continue
        size = int(header[2])
        found[path] = output[offset:offset + size].decode("utf-8", "replace")
        offset += size + 1
    return found


def dirty_docs(root: Path) -> frozenset[str]:
    """Files under docs/ that differ from HEAD on disk, untracked ones included."""
    try:
        listed = _git(root, "status", "--porcelain", "--no-renames", "-z", "--", "docs")
    except (subprocess.CalledProcessError, OSError):
        return frozenset()
    return frozenset(item[3:].decode() for item in listed.split(b"\0") if item)


def visible_words(source: str) -> str:
    """A page's reader-visible text, whitespace and attributes gone: what an edit must change."""
    parts: list[str] = []
    html_source.visible_text(html_source.parse(source), parts)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


NAV_FILE = "scripts/docs_site/nav.py"


def snapshot_module(scope: Scope, path: str, name: str) -> ModuleType | None:
    """The version of a Python file this scope judges, or None when the disk already holds it.

    Staged and committed checks must not depend on another session's unsaved
    edit of the navigation or the CLI parser.
    """
    if scope.reads == "disk":
        return None
    try:
        text = read_file(scope, path)
        if (scope.root / path).read_text(encoding="utf-8", errors="replace") == text:
            return None
    except (subprocess.CalledProcessError, OSError):
        return None
    private = f"_docs_snapshot_{name}"
    spec = importlib.util.spec_from_loader(private, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(scope.root / path)
    sys.modules[private] = module  # dataclasses look their module up while the source runs
    try:
        exec(compile(text, str(scope.root / path), "exec"), module.__dict__)
    finally:
        del sys.modules[private]
    return module


def module_at(root: Path, revision: str, path: str, name: str) -> ModuleType | None:
    """A Python file as it was at a revision; None when the revision does not hold it."""
    text = read_many(root, revision, [path])[path]
    if text is None:
        return None
    private = f"_docs_snapshot_{name}"
    spec = importlib.util.spec_from_loader(private, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(root / path)
    sys.modules[private] = module
    try:
        exec(compile(text, str(root / path), "exec"), module.__dict__)
    finally:
        del sys.modules[private]
    return module


def load(scope: Scope, docs_root: Path | None = None) -> Corpus:
    """Every page, whatever the scope: rules on one page can read the others."""
    docs_root = docs_root or scope.root / "docs"
    files = (_snapshot_files(scope.root) if scope.reads == "index"
             else _snapshot_files(scope.root, "HEAD") if scope.reads == "head" else _disk_files(docs_root))
    judged_nav = snapshot_module(scope, NAV_FILE, "nav") or nav
    registered = judged_nav._registered_paths()
    markdown = {rel for rel in files if rel.endswith(".md")}
    pages: list[Page] = []
    orphans: list[str] = []
    for rel in sorted(files):
        if not rel.endswith(".html") or judged_nav.is_excluded(Path(rel)):
            continue
        if rel.endswith(".zh.html"):
            if rel[:-len(".zh.html")] + ".html" not in files:
                orphans.append("docs/" + rel)
            continue
        zh_rel = rel[:-len(".html")] + ".zh.html"
        zh_text = files.get(zh_rel) or ""
        text = files[rel] or ""
        pages.append(Page(
            key=judged_nav._registry_key(rel, registered, markdown),
            path="docs/" + rel,
            zh_path="docs/" + zh_rel if zh_rel in files else "",
            text=text,
            zh_text=zh_text,
            tree=html_source.parse(text),
            zh_tree=html_source.parse(zh_text) if zh_rel in files else None,
            prototype=rel in policy.PROTOTYPE_PAGES,
        ))
    texts = {"docs/" + rel: value for rel, value in files.items() if value is not None}
    return Corpus(scope=scope, pages=pages, files=frozenset(files), orphans=tuple(orphans),
                  docs_root=docs_root, nav=judged_nav, _texts=texts)

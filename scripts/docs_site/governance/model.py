"""The data every docs check reads and reports."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .. import nav as nav_module
from ..html_source import Node

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    rule: str       # "format.style-attribute"
    severity: str   # ERROR or WARNING
    path: str       # repository-relative
    line: int       # 1-based; 0 for the whole file
    message: str
    fix: str
    subject: str = ""  # stable text; baseline key = rule + path + sha1(subject)
    measure: float | None = None  # the size a page-level rule measured; the baseline caps it

    @property
    def key(self) -> tuple[str, str, str]:
        return self.rule, self.path, subject_hash(self.subject)

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.severity} {self.rule}: {self.message} Fix: {self.fix}"


def subject_hash(subject: str) -> str:
    return hashlib.sha1(subject.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Page:
    key: str          # navigation key
    path: str         # docs/x.html
    zh_path: str      # docs/x.zh.html, "" when missing
    text: str
    zh_text: str
    tree: Node        # html_source.Node, with Node.line
    zh_tree: Node | None
    prototype: bool

    @property
    def zh_counterpart(self) -> str:
        """Where the Chinese page lives or lived, whether or not it still exists."""
        return self.path[:-len(".html")] + ".zh.html"

    @property
    def rel(self) -> str:
        """The page's path inside docs/."""
        return self.path[len("docs/"):]


@dataclass(frozen=True)
class Scope:
    mode: str         # paths, worktree, shared, staged, range, all
    root: Path
    changed: tuple[str, ...] = ()  # repository-relative
    base: str | None = None
    committed: bool = False     # a range read from HEAD: the main checkout's dirty tree is not this range
    shared_tree: bool = False   # named paths in the main checkout, beside other sessions' dirty files

    @property
    def reads(self) -> str:
        """Which version of the repository this scope judges: "index", "head" or "disk"."""
        return "index" if self.mode == "staged" else "head" if self.committed else "disk"


@dataclass
class Corpus:
    scope: Scope
    pages: list[Page]
    files: frozenset[str] = frozenset()         # every source under docs/, relative to it
    orphans: tuple[str, ...] = ()               # docs/x.zh.html with no docs/x.html
    docs_root: Path | None = None
    nav: ModuleType = nav_module                # the navigation this scope judges against
    _texts: dict[str, str] = field(default_factory=dict)
    _base_texts: dict[str, str | None] = field(default_factory=dict)
    _foreign: frozenset[str] | None = None

    def in_scope(self) -> list[Page]:
        if self.scope.mode == "all":
            return self.pages
        changed = set(self.scope.changed)
        return [page for page in self.pages if page.path in changed or page.zh_counterpart in changed]

    def path_in_scope(self, path: str) -> bool:
        return self.scope.mode == "all" or path in self.scope.changed

    def orphan_in_scope(self, zh_path: str) -> bool:
        """A Chinese page with no English page is in scope when either of the pair changed."""
        return self.path_in_scope(zh_path) or zh_path[:-len(".zh.html")] + ".html" in self.scope.changed

    def removed_pages(self) -> list[str]:
        """Docs pages this change deleted or moved away, relative to docs/."""
        return [path[len("docs/"):] for path in self.scope.changed
                if path.startswith("docs/") and path.endswith(".html")
                and path[len("docs/"):] not in self.files]

    def read(self, path: str) -> str:
        """A repository file as this scope sees it: the index when staged, else disk."""
        if path not in self._texts:
            from . import corpus as loader
            self._texts[path] = loader.read_file(self.scope, path)
        return self._texts[path]

    def base_text(self, path: str) -> str | None:
        """A repository file before this change; None when it did not exist."""
        return self.base_texts([path])[path]

    def base_texts(self, paths: list[str]) -> dict[str, str | None]:
        missing = [path for path in paths if path not in self._base_texts]
        if missing:
            from . import corpus as loader
            self._base_texts.update(loader.read_many(self.scope.root, self.scope.base or "HEAD", missing))
        return {path: self._base_texts[path] for path in paths}

    def foreign_dirty(self) -> frozenset[str]:
        """Pages another session has unsaved, when this check is named paths in the main checkout."""
        if self._foreign is None:
            from . import corpus as loader
            self._foreign = (loader.dirty_docs(self.scope.root) - set(self.scope.changed)
                             if self.scope.shared_tree else frozenset())
        return self._foreign

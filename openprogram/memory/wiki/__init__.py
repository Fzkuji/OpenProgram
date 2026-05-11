"""Wiki subsystem — an Obsidian-native, LLM-maintained knowledge vault.

Self-contained subpackage. The only hard dependencies are:

* the Python standard library
* ``openprogram.memory.store`` for path resolution
* (for agentic ops) an injected ``runtime`` or LLM callable

Designed to be liftable: copy the ``wiki/`` directory into another
project, replace the four-line ``..store`` shim with that project's
path helper, and the wiki keeps working.

═══════════════════════════════════════════════════════════════
PUBLIC API
═══════════════════════════════════════════════════════════════

The :class:`Wiki` class is the recommended seam — pass it a vault
root and (optionally) a runtime / LLM callable, and use methods.

Free functions in ``access``, ``ops``, ``ingest``, ``enrich`` are
re-exported here for callers that prefer module-level functions
backed by the default openprogram vault.

Example::

    from openprogram.memory.wiki import Wiki

    w = Wiki(root="~/some/vault")
    w.tree()
    w.lint()
    w.backlinks("Foo")
    # Agentic ops require a runtime:
    w.ingest_session(session_id, messages, runtime=my_runtime)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import access, enrich, helpers, ingest, ops

# Re-export module-level functions for back-compat / convenience.
# Existing call sites that did `from openprogram.memory.wiki import find`
# keep working.
from .access import (  # noqa: F401
    find, read, tree, iter_pages, page_type, pages_of_type, root,
)
from .ops import (  # noqa: F401
    lint, rename, relink, prune_broken_links, backlinks,
    unlinked_mentions, survey, refactor, git_commit,
)
from .ingest import (  # noqa: F401
    ingest_session, ingest_session_by_id,
)
from .enrich import (  # noqa: F401
    enrich_page, enrich_pages, enrich_inbound_for_new_page,
)


class Wiki:
    """Bound view of a single vault. The portable entry point.

    Args:
        root: Vault root directory. If omitted, uses
            :func:`openprogram.memory.store.wiki_dir`.
        runtime: Optional Runtime instance for agentic ops (ingest,
            survey, refactor). If None, agentic ops will try to build
            one via ``runtime_registry._build_autodetect``.
        llm: Optional ``(system, user) -> str`` callable for non-
            agentic LLM calls (enrich). If None and ``runtime`` is
            set, derived from the runtime.

    Most methods are thin wrappers over the module-level functions
    that pin them to ``self.root``.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        runtime: Any | None = None,
        llm: Callable[[str, str], str] | None = None,
    ) -> None:
        if root is None:
            from .. import store
            self.root: Path = store.wiki_dir()
        else:
            self.root = Path(root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)
        self._runtime = runtime
        self._llm = llm

    # ── Read ────────────────────────────────────────────────────────────
    def find(self, name: str) -> Path | None:
        return helpers.find_node(self.root, name)

    def read(self, target: str | Path) -> str | None:
        return access.read(target)

    def tree(self, *, max_depth: int = 8) -> str:
        return helpers.folder_tree(self.root, max_depth=max_depth)

    def iter_pages(self):
        yield from helpers.iter_md_files(self.root)

    def page_type(self, path: Path) -> str | None:
        return access.page_type(path)

    def pages_of_type(self, t: str) -> list[Path]:
        return access.pages_of_type(t)

    # ── Lint + link ops ─────────────────────────────────────────────────
    def lint(self) -> str:
        return ops.lint()

    def rename(self, old: str, new: str) -> dict[str, Any]:
        return ops.rename(old, new)

    def relink(self, old: str, new: str) -> dict[str, Any]:
        return ops.relink(old, new)

    def prune_broken_links(self, *, dry_run: bool = True) -> dict[str, Any]:
        return ops.prune_broken_links(dry_run=dry_run)

    def backlinks(self, name: str) -> list[dict[str, str]]:
        return ops.backlinks(name)

    def unlinked_mentions(self, name: str, *, max_per_page: int = 3) -> list[dict[str, Any]]:
        return ops.unlinked_mentions(name, max_per_page=max_per_page)

    # ── Agentic ops (need a runtime) ────────────────────────────────────
    def survey(self, topic: str) -> dict[str, Any]:
        return ops.survey(topic)

    def refactor(self, topic: str) -> dict[str, Any]:
        return ops.refactor(topic)

    def ingest_session(
        self, session_id: str, messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return ingest.ingest_session(session_id, messages, runtime=self._runtime)

    def ingest_session_by_id(self, session_id: str) -> dict[str, Any]:
        return ingest.ingest_session_by_id(session_id)

    # ── Git ─────────────────────────────────────────────────────────────
    def git_commit(self, message: str) -> dict[str, Any]:
        return ops.git_commit(message)


def default() -> Wiki:
    """Return a Wiki bound to the openprogram default vault."""
    return Wiki()


__all__ = [
    # Class
    "Wiki", "default",
    # Submodules
    "access", "helpers", "ops", "ingest", "enrich",
    # Free-function re-exports
    "find", "read", "tree", "iter_pages", "page_type", "pages_of_type", "root",
    "lint", "rename", "relink", "prune_broken_links",
    "backlinks", "unlinked_mentions", "survey", "refactor", "git_commit",
    "ingest_session", "ingest_session_by_id",
    "enrich_page", "enrich_pages", "enrich_inbound_for_new_page",
]

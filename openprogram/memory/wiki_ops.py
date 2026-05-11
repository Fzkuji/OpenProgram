"""Wiki operations — pure-Python + agentic ops on the vault.

Five operations, mixing both upstream patterns:

* :func:`lint`     — pure Python; structural health report (RAH)
* :func:`rename`   — pure Python; move folder + rewrite [[old]]→[[new]] (RAH)
* :func:`tree`     — convenience re-export
* :func:`survey`   — agentic; rewrite a topic page from its children (RAH)
* :func:`refactor` — agentic; split an overgrown topic into sub-clusters (RAH)

Agentic ops use a Runtime built from autodetect, the same way ingest
does. They drive the standard file toolset (``read``/``write``/
``edit``) — no custom block protocol.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import store
from . import wiki_helpers as h

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def lint() -> str:
    """Walk the vault and report structural issues. Returns markdown."""
    root = store.wiki_dir()
    pages: list[Path] = list(h.iter_md_files(root))
    if not pages:
        return "# Wiki lint report\n\n(empty vault — nothing to lint)"

    stems = {p.stem.lower(): p for p in pages}

    missing_type: list[str] = []
    bad_type: list[tuple[str, str]] = []
    missing_status: list[str] = []        # query pages without status:
    stem_mismatch: list[tuple[str, str]] = []
    broken_links: list[tuple[str, str]] = []
    inbound: dict[str, int] = {}
    outbound: dict[str, int] = {}
    folder_children: dict[Path, list[Path]] = {}

    for p in pages:
        rel = p.relative_to(root)
        text = p.read_text(encoding="utf-8")
        fm, body = h.parse_frontmatter(text)
        t = fm.get("type")
        if not t:
            missing_type.append(str(rel))
        elif t not in store.WIKI_PAGE_TYPES:
            bad_type.append((str(rel), str(t)))
        if t == "query" and not fm.get("status"):
            missing_status.append(str(rel))

        # Folder-form check
        if p.parent != root and p.parent.name != p.stem:
            stem_mismatch.append((p.parent.name, p.stem))

        # Folder-children bookkeeping
        if p.parent != root and p.parent.name == p.stem:
            folder_children[p.parent] = [
                c for c in p.parent.iterdir() if c.is_dir()
            ]

        stem_l = p.stem.lower()
        outbound.setdefault(stem_l, 0)
        for target in h.extract_wikilinks(body):
            outbound[stem_l] += 1
            inbound[target] = inbound.get(target, 0) + 1
            if target not in stems:
                broken_links.append((str(rel), target))

    orphans = [
        p.stem for p in pages
        if inbound.get(p.stem.lower(), 0) == 0
        and outbound.get(p.stem.lower(), 0) == 0
    ]

    refactor_candidates = [
        f.name for f, children in folder_children.items() if len(children) >= 6
    ]

    out: list[str] = [
        "# Wiki lint report",
        "",
        f"Pages: {len(pages)}",
        f"Missing `type:` frontmatter:           {len(missing_type)}",
        f"Unknown `type:` value:                 {len(bad_type)}",
        f"Query pages missing `status:`:         {len(missing_status)}",
        f"Folder/stem mismatches:                {len(stem_mismatch)}",
        f"Broken `[[wikilinks]]`:                {len(broken_links)}",
        f"Orphan pages:                          {len(orphans)}",
        f"Topics with ≥6 children (refactor?):   {len(refactor_candidates)}",
        "",
    ]

    def _section(title: str, rows: list[str]) -> None:
        if not rows:
            return
        out.append(f"## {title}")
        out.extend(f"- {r}" for r in rows[:30])
        if len(rows) > 30:
            out.append(f"- ... and {len(rows) - 30} more")
        out.append("")

    _section("Missing type", missing_type)
    _section("Unknown type", [f"`{p}` → `{t}`" for p, t in bad_type])
    _section("Query missing status", missing_status)
    _section("Folder/stem mismatch",
             [f"folder=`{f}`, stem=`{s}`" for f, s in stem_mismatch])
    _section("Broken wikilinks",
             [f"`{p}` → `[[{t}]]`" for p, t in broken_links])
    _section("Orphans", orphans)
    _section("Refactor candidates", refactor_candidates)

    return "\n".join(out).rstrip()


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


def rename(old: str, new: str) -> dict[str, Any]:
    """Rename a node by stem. Moves folder-form or leaf-form, rewrites
    every ``[[old]]`` to ``[[new]]`` across the vault.
    """
    root = store.wiki_dir()
    target = h.find_node(root, old)
    if target is None:
        return {"ok": False, "error": f"no page named {old!r}"}

    if target.parent != root and target.parent.name == target.stem:
        new_folder = target.parent.parent / new
        if new_folder.exists():
            return {"ok": False, "error": f"destination {new_folder} exists"}
        target.parent.rename(new_folder)
        (new_folder / f"{target.stem}.md").rename(new_folder / f"{new}.md")
    else:
        new_path = target.with_name(f"{new}.md")
        if new_path.exists():
            return {"ok": False, "error": f"destination {new_path} exists"}
        target.rename(new_path)

    rewrites = 0
    for p in h.iter_md_files(root):
        before = p.read_text(encoding="utf-8")
        after = h.rewrite_wikilinks(before, old, new)
        if after != before:
            p.write_text(after, encoding="utf-8")
            rewrites += 1
    return {"ok": True, "rewrites": rewrites}


def tree(*, max_depth: int = 8) -> str:
    return h.folder_tree(store.wiki_dir(), max_depth=max_depth)


# ---------------------------------------------------------------------------
# Survey (agentic — rewrite a topic page from its children)
# ---------------------------------------------------------------------------


SURVEY_PROMPT = """\
You are rewriting a topic page in our wiki as a coherent
Wikipedia-style article from its child pages.

Topic file:  {topic_path}
Vault root:  {vault_root}

═══════════════════════════════════════════════════════════════
YOUR JOB
═══════════════════════════════════════════════════════════════

1. Read `{topic_path}` — that's the topic page to rewrite.
2. Read every child page in `{topic_folder}/` (the same folder).
3. Cluster the children into 2-5 natural sub-areas if there are
   enough; if fewer than 4 children, just write one cohesive article.
4. Use `edit` (preferred) or `write` to update `{topic_path}` so its
   BODY (after the YAML frontmatter) is a coherent prose article
   discussing the children with `[[wikilinks]]` to them.
5. Preserve the frontmatter verbatim. Keep the `# <Title>` heading.
6. Don't touch any of the child pages.

═══════════════════════════════════════════════════════════════
STYLE
═══════════════════════════════════════════════════════════════

- Paragraphs, not bullet lists.
- Reference each child page by `[[<child filename>]]`.
- One short opening paragraph; then 2-5 `##` sections if needed.
- No invented facts — only what the child pages support.

Return: one-line confirmation of what you did.
"""


def survey(topic: str) -> dict[str, Any]:
    """Run :data:`SURVEY_PROMPT` over the named topic. Topic is the
    filename stem (e.g. ``"Tools"``).
    """
    root = store.wiki_dir()
    target = h.find_node(root, topic)
    if target is None:
        return {"ok": False, "error": f"no page named {topic!r}"}
    if target.parent == root or target.parent.name != target.stem:
        return {"ok": False, "error": f"page {topic!r} is a leaf, not a topic folder"}

    runtime = _build_runtime()
    if runtime is None:
        return {"ok": False, "error": "no runtime configured"}

    prompt = SURVEY_PROMPT.format(
        topic_path=str(target),
        topic_folder=str(target.parent),
        vault_root=str(root),
    )
    try:
        report = runtime.exec(
            content=[{"type": "text", "text": prompt}],
            max_iterations=20,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"exec: {e}"}
    return {"ok": True, "report": report}


# ---------------------------------------------------------------------------
# Refactor (agentic — split an overgrown topic into sub-clusters)
# ---------------------------------------------------------------------------


REFACTOR_PROMPT = """\
You are refactoring an overgrown topic in our wiki. The topic has
≥6 direct children — propose 2-4 sub-clusters and reorganise.

Topic file:    {topic_path}
Topic folder:  {topic_folder}
Vault root:    {vault_root}

═══════════════════════════════════════════════════════════════
YOUR JOB
═══════════════════════════════════════════════════════════════

1. Read `{topic_path}` and every child page in `{topic_folder}/`.
2. Decide on 2-4 sub-cluster names that group the children naturally.
3. For each cluster, create a new subtopic folder + page:
   `{topic_folder}/<Cluster Name>/<Cluster Name>.md`
   with `type: topic` frontmatter and a short opening paragraph.
4. Move each child page's folder under the right sub-cluster (use
   `bash` for `mv` since `write`/`edit` only handle file contents).
5. Edit `{topic_path}` to mention each new sub-cluster with a
   `[[wikilink]]`.
6. Wikilinks to the moved pages STAY VALID because they use
   filename stems, not paths.

═══════════════════════════════════════════════════════════════
SAFETY
═══════════════════════════════════════════════════════════════

- Don't delete any page content.
- Don't rename child pages — only move them.
- If you're unsure how to cluster, leave it and return early.

Return: one-line confirmation of what you did, or an error.
"""


def refactor(topic: str) -> dict[str, Any]:
    root = store.wiki_dir()
    target = h.find_node(root, topic)
    if target is None:
        return {"ok": False, "error": f"no page named {topic!r}"}
    if target.parent == root or target.parent.name != target.stem:
        return {"ok": False, "error": f"page {topic!r} is a leaf, not a topic folder"}

    children = [c for c in target.parent.iterdir() if c.is_dir()]
    if len(children) < 6:
        return {"ok": False, "error": f"only {len(children)} children — not enough for refactor"}

    runtime = _build_runtime()
    if runtime is None:
        return {"ok": False, "error": "no runtime configured"}

    prompt = REFACTOR_PROMPT.format(
        topic_path=str(target),
        topic_folder=str(target.parent),
        vault_root=str(root),
    )
    try:
        report = runtime.exec(
            content=[{"type": "text", "text": prompt}],
            max_iterations=30,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"exec: {e}"}
    return {"ok": True, "report": report}


# ---------------------------------------------------------------------------
# Runtime helper
# ---------------------------------------------------------------------------


def _build_runtime() -> Any | None:
    try:
        from openprogram.agents.runtime_registry import _build_autodetect
    except Exception:
        return None
    try:
        return _build_autodetect()
    except Exception as e:  # noqa: BLE001
        logger.warning("wiki_ops: no runtime available (%s)", e)
        return None


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------


def git_commit(message: str) -> dict[str, Any]:
    """Stage all changes in the vault and commit. Returns
    ``{ok, committed, hash, error}``. If there's nothing to commit,
    returns ``{ok: True, committed: False}``.
    """
    import subprocess
    root = store.wiki_dir()
    if not (root / ".git").exists():
        return {"ok": False, "error": "vault is not a git repo"}

    try:
        subprocess.run(
            ["git", "-C", str(root), "add", "-A"],
            check=True, capture_output=True, timeout=15,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True, capture_output=True, timeout=15, text=True,
        ).stdout.strip()
        if not status:
            return {"ok": True, "committed": False}
        subprocess.run(
            ["git", "-C", str(root), "commit", "-m", message,
             "--author", "OpenProgram Memory <memory@openprogram.local>"],
            check=True, capture_output=True, timeout=15,
        )
        h = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, timeout=15, text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"git: {e.stderr.decode() if e.stderr else e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "committed": True, "hash": h}

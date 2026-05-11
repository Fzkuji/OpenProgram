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

from .. import store
from . import helpers as h

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
    every ``[[old]]`` to ``[[new]]`` across the vault, and updates the
    persistent link index.
    """
    root = store.wiki_dir()
    target = h.find_node(root, old)
    if target is None:
        return {"ok": False, "error": f"no page named {old!r}"}
    old_path_for_index = target

    if target.parent != root and target.parent.name == target.stem:
        new_folder = target.parent.parent / new
        if new_folder.exists():
            return {"ok": False, "error": f"destination {new_folder} exists"}
        target.parent.rename(new_folder)
        (new_folder / f"{target.stem}.md").rename(new_folder / f"{new}.md")
        new_path_for_index = new_folder / f"{new}.md"
    else:
        new_path = target.with_name(f"{new}.md")
        if new_path.exists():
            return {"ok": False, "error": f"destination {new_path} exists"}
        target.rename(new_path)
        new_path_for_index = new_path

    rewrites = 0
    changed_paths: list[Path] = []
    for p in h.iter_md_files(root):
        before = p.read_text(encoding="utf-8")
        after = h.rewrite_wikilinks(before, old, new)
        if after != before:
            p.write_text(after, encoding="utf-8")
            rewrites += 1
            changed_paths.append(p)

    try:
        from .. import index as _idx
        _idx.remove_wiki_page(old_path_for_index)
        _idx.update_wiki_page(new_path_for_index)
        for p in changed_paths:
            _idx.update_wiki_page(p)
    except Exception:
        pass

    return {"ok": True, "rewrites": rewrites}


def tree(*, max_depth: int = 8) -> str:
    return h.folder_tree(store.wiki_dir(), max_depth=max_depth)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_page(name: str, *, prune_refs: bool = True) -> dict[str, Any]:
    """Delete a page (leaf or folder form) and optionally strip
    ``[[name]]`` references across the vault.

    Refuses to delete a topic folder that still has subtopic children —
    caller should delete the children first or run :func:`refactor`.

    Returns ``{ok, deleted, refs_stripped, error}``.
    """
    root = store.wiki_dir()
    target = h.find_node(root, name)
    if target is None:
        return {"ok": False, "error": f"no page named {name!r}"}

    deleted: list[str] = []
    if target.parent != root and target.parent.name == target.stem:
        import shutil
        children = [c for c in target.parent.iterdir() if c.is_dir()]
        if children:
            return {
                "ok": False,
                "error": f"topic {name!r} still has {len(children)} subtopic children",
            }
        shutil.rmtree(target.parent)
        deleted.append(str(target.parent.relative_to(root)))
    else:
        target.unlink()
        deleted.append(str(target.relative_to(root)))

    try:
        from .. import index as _idx
        _idx.remove_wiki_page(target)
    except Exception:
        pass

    refs_stripped = 0
    if prune_refs:
        import re
        name_l = name.lower().removesuffix(".md")
        link_re = re.compile(r"\[\[([^\]|#]+?)(\|[^\]]+?)?(#[^\]]+?)?\]\]")
        for p in h.iter_md_files(root):
            before = p.read_text(encoding="utf-8")
            masked, repls = h.mask_code(before)

            def _sub(m: "re.Match[str]") -> str:
                if m.group(1).strip().lower() != name_l:
                    return m.group(0)
                alias = (m.group(2) or "")[1:] if m.group(2) else ""
                anchor = m.group(3) or ""
                return (alias or m.group(1).strip()) + anchor

            after = h.unmask_code(link_re.sub(_sub, masked), repls)
            if after != before:
                p.write_text(after, encoding="utf-8")
                refs_stripped += 1
                try:
                    from .. import index as _idx
                    _idx.update_wiki_page(p)
                except Exception:
                    pass

    return {"ok": True, "deleted": deleted, "refs_stripped": refs_stripped}


# ---------------------------------------------------------------------------
# Backlinks — what links TO this page
# ---------------------------------------------------------------------------


def backlinks(name: str) -> list[dict[str, str]]:
    """Find every page that has a ``[[name]]`` wikilink to ``name``.

    Fast path via the persistent link index — O(rows) instead of
    O(full-vault scan). The snippet is still fetched from disk for
    each hit so callers see context.
    """
    import re
    root = store.wiki_dir()
    name_l = name.lower().removesuffix(".md")
    link_re = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?(?:#[^\]]+?)?\]\]")
    out: list[dict[str, str]] = []
    try:
        from .. import index as _idx
        rel_paths = _idx.inbound(name)
    except Exception:
        rel_paths = []

    if not rel_paths:  # Index might be stale on first use — fall back to scan
        for p in h.iter_md_files(root):
            if p.stem.lower() == name_l:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            masked, _ = h.mask_code(text)
            for m in link_re.finditer(masked):
                if m.group(1).strip().lower() == name_l:
                    start = max(0, m.start() - 60)
                    end = min(len(text), m.end() + 60)
                    snippet = text[start:end].replace("\n", " ").strip()
                    out.append({"page": str(p.relative_to(root)), "snippet": snippet})
                    break
        return out

    for rel in rel_paths:
        p = root / rel
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        masked, _ = h.mask_code(text)
        for m in link_re.finditer(masked):
            if m.group(1).strip().lower() == name_l:
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                snippet = text[start:end].replace("\n", " ").strip()
                out.append({"page": rel, "snippet": snippet})
                break
    return out


# ---------------------------------------------------------------------------
# Unlinked mentions — plain-text occurrences not yet wikilinked
# ---------------------------------------------------------------------------


def unlinked_mentions(name: str, *, max_per_page: int = 3) -> list[dict[str, Any]]:
    """Find pages that mention ``name`` in plain text without a
    `[[wikilink]]`. Pure Python, no LLM.

    Mirrors Obsidian's "Unlinked mentions" panel — surfaces pages
    that ought to link to ``name`` but don't.

    Args:
        name: page filename stem to search for.
        max_per_page: cap occurrences reported per page (rest hidden).

    Returns a list of
    ``{"page": str, "occurrences": [snippet, ...]}``.
    """
    import re
    root = store.wiki_dir()
    name_l = name.lower().removesuffix(".md")
    # Word-boundary, case-insensitive, exact-token match
    pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.IGNORECASE)
    link_re = re.compile(r"\[\[[^\]]+\]\]")

    out: list[dict[str, Any]] = []
    for p in h.iter_md_files(root):
        if p.stem.lower() == name_l:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Strip frontmatter so we don't match the YAML
        _fm, body = h.parse_frontmatter(text)
        masked, _ = h.mask_code(body)
        # Blank out every existing [[...]] block so we don't double-count
        masked = link_re.sub(lambda m: " " * len(m.group(0)), masked)

        snippets: list[str] = []
        for m in pattern.finditer(masked):
            start = max(0, m.start() - 50)
            end = min(len(masked), m.end() + 50)
            ctx = body[start:end].replace("\n", " ").strip()
            snippets.append(ctx)
            if len(snippets) >= max_per_page:
                break
        if snippets:
            out.append({"page": str(p.relative_to(root)), "occurrences": snippets})
    return out


# ---------------------------------------------------------------------------
# Relink — rewrite wikilinks only (no file move)
# ---------------------------------------------------------------------------


def relink(old: str, new: str) -> dict[str, Any]:
    """Rewrite ``[[old]]`` → ``[[new]]`` across the vault without
    touching any file's location or name.

    Use case: user (or Obsidian without auto-rewrite) renamed a file
    on disk and the wikilinks to it are now broken. ``relink`` does
    just the cascade rewrite half of :func:`rename`.

    Args:
        old: the previously-linked filename stem.
        new: the new filename stem the page now lives under.

    Returns ``{ok, rewrites, pages}`` — number of pages changed and
    their relative paths.
    """
    root = store.wiki_dir()
    rewrites = 0
    changed: list[str] = []
    changed_paths: list[Path] = []
    for p in h.iter_md_files(root):
        before = p.read_text(encoding="utf-8")
        after = h.rewrite_wikilinks(before, old, new)
        if after != before:
            p.write_text(after, encoding="utf-8")
            rewrites += after.lower().count(f"[[{new.lower()}") - before.lower().count(f"[[{new.lower()}")
            changed.append(str(p.relative_to(root)))
            changed_paths.append(p)
    try:
        from .. import index as _idx
        for p in changed_paths:
            _idx.update_wiki_page(p)
    except Exception:
        pass
    return {"ok": True, "rewrites": rewrites, "pages": changed}


# ---------------------------------------------------------------------------
# Prune broken wikilinks
# ---------------------------------------------------------------------------


def prune_broken_links(*, dry_run: bool = True) -> dict[str, Any]:
    """Find every ``[[wikilink]]`` whose target page doesn't exist
    and (if ``dry_run`` is False) strip the brackets, leaving the
    plain text in place.

    Returns ``{ok, broken: [...], applied}``. With ``dry_run=True``
    the report shows what would change but nothing is written.
    """
    import re
    root = store.wiki_dir()
    pages = list(h.iter_md_files(root))
    stems = {p.stem.lower() for p in pages}

    wikilink_re = re.compile(r"\[\[([^\]|#]+?)(\|[^\]]+?)?(#[^\]]+?)?\]\]")
    broken: list[tuple[str, str]] = []
    applied = 0
    for p in pages:
        before = p.read_text(encoding="utf-8")
        masked, repls = h.mask_code(before)

        def _sub(m: "re.Match[str]") -> str:
            target = m.group(1).strip()
            if target.lower() in stems:
                return m.group(0)
            broken.append((str(p.relative_to(root)), target))
            if dry_run:
                return m.group(0)
            # Strip brackets, keep alias text if present else the target text.
            alias = (m.group(2) or "")[1:] if m.group(2) else ""
            anchor = m.group(3) or ""
            return (alias or target) + anchor

        after = h.unmask_code(wikilink_re.sub(_sub, masked), repls)
        if not dry_run and after != before:
            p.write_text(after, encoding="utf-8")
            applied += 1
            try:
                from .. import index as _idx
                _idx.update_wiki_page(p)
            except Exception:
                pass

    return {"ok": True, "broken": broken, "applied": applied, "dry_run": dry_run}


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


# ---------------------------------------------------------------------------
# Review queue — list / resolve items flagged by ingest
# ---------------------------------------------------------------------------


def review_list(*, only_pending: bool = True) -> list[dict[str, Any]]:
    """Return items in the review queue. Items are flagged by ingest
    when it spots contradictions, duplicates, missing pages, or
    suggestions worth human judgement.
    """
    import json
    qpath = store.review_queue_path()
    if not qpath.exists():
        return []
    try:
        items = json.loads(qpath.read_text(encoding="utf-8"))
    except Exception:
        return []
    if only_pending:
        items = [it for it in items if not it.get("resolved")]
    return items


def review_resolve(item_id: int, *, action: str = "ack", note: str = "") -> dict[str, Any]:
    """Mark a review item as resolved.

    Args:
        item_id: the integer id returned by ``review_list``.
        action: free-form label ("ack" / "skip" / "applied" / ...).
        note: optional human note.

    Returns ``{ok, item, error}``. Does NOT delete — the resolved
    item stays in the file with ``resolved=True`` for audit.
    """
    import json
    from datetime import datetime
    qpath = store.review_queue_path()
    if not qpath.exists():
        return {"ok": False, "error": "review queue is empty"}
    try:
        items = json.loads(qpath.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"parse: {e}"}
    for it in items:
        if it.get("id") == item_id:
            it["resolved"] = True
            it["resolved_at"] = datetime.now().isoformat(timespec="seconds")
            it["resolution"] = {"action": action, "note": note}
            qpath.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "item": it}
    return {"ok": False, "error": f"no item with id={item_id}"}


# ---------------------------------------------------------------------------
# Stats — counts useful for `memory_status` tool
# ---------------------------------------------------------------------------


def stats() -> dict[str, Any]:
    """Return a snapshot of the vault state.

    Counts are cheap (file listing + index queries), so this is safe
    to call from a tool / lint pass / web UI.
    """
    root = store.wiki_dir()
    pages = list(h.iter_md_files(root))
    by_type: dict[str, int] = {}
    for p in pages:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = h.parse_frontmatter(text)
        t = fm.get("type") or "?"
        by_type[t] = by_type.get(t, 0) + 1

    try:
        from .. import index as _idx
        idx_stats = _idx.stats()
    except Exception:
        idx_stats = {}

    pending_reviews = len(review_list(only_pending=True))

    return {
        "pages_total": len(pages),
        "pages_by_type": by_type,
        "pending_reviews": pending_reviews,
        "fts_wiki_rows": idx_stats.get("wiki_pages", 0),
        "fts_short_rows": idx_stats.get("short_entries", 0),
        "last_reindex": idx_stats.get("last_reindex"),
        "vault_root": str(root),
    }

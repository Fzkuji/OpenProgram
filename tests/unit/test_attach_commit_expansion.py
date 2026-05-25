"""Attach pointer → ContextCommit expansion + cross-turn dedup.

Verifies the behaviour added in docs/design/context-attach-merge.md
scenarios B / C:

* When an attach pointer node carries ``source_commit_id``, the
  generator loads that ContextCommit and expands its non-summarized
  items into the receiving commit as a delimited block.
* Each expanded item carries ``attached_from = source_commit_id``.
* Parent commits already containing items with that
  ``attached_from`` are dedup-checked: a second attach pointer
  pointing at the same source is a no-op.
* When ``source_commit_id`` is missing or the commit can't be
  loaded, the generator falls back to the legacy single-item
  attach text rendering.
"""
from __future__ import annotations

import json
import time

import pytest

from openprogram.context.commit.generator import generate_commit
from openprogram.context.commit.store import save_commit
from openprogram.context.commit.types import (
    ContextCommit,
    ContextItem,
    CURRENT_RULES_VERSION,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )
    s.create_session("src", "main", title="source")
    s.create_session("dst", "main", title="dest")
    return s


def _seed_source_commit(store) -> ContextCommit:
    """Persist a small ContextCommit on the source session — three
    items the attach expansion should be able to copy out."""
    commit = ContextCommit(
        id="src_commit_aa",
        session_id="src",
        parent_id=None,
        created_at=time.time(),
        head_node_id="src_node_1",
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=30,
        items=[
            ContextItem(
                source_node_id="src_u1", role="user",
                rendered="src user msg", tokens=10,
                state="full", locked=False, reason="new",
            ),
            ContextItem(
                source_node_id="src_a1", role="assistant",
                rendered="src assistant reply", tokens=10,
                state="full", locked=False, reason="new",
            ),
            # Already summarized — should NOT be copied across.
            ContextItem(
                source_node_id="src_old", role="user",
                rendered="", tokens=0,
                state="summarized", locked=True,
                reason="merged_into:sm_xx",
                merged_into="sm_xx",
            ),
        ],
        summary="seeded",
    )
    save_commit(store, commit)
    return commit


def _attach_node(node_id: str, *, source_commit_id: str | None,
                 label: str = "Source", head_id: str = "src_node_1") -> dict:
    """Shape a DAG-msg-dict for an attach pointer in the form the
    generator + ensure path consume. ``extra`` mirrors what
    ws_actions/branch.py writes."""
    blob = {
        "session_id": "src",
        "head_id": head_id,
        "label": label,
        "manual": True,
    }
    if source_commit_id is not None:
        blob["source_commit_id"] = source_commit_id
    return {
        "id": node_id,
        "role": "assistant",
        "function": "attach",
        "content": "(preview text)",
        "extra": json.dumps({"attach": blob}),
    }


def test_attach_with_source_commit_expands_into_items(store):
    """generate_commit on the dest session, with one attach pointer
    referencing the source's commit_aa, should produce one open
    marker, one item per non-summarized source item, and one close
    marker — all carrying ``attached_from`` = source commit id."""
    _seed_source_commit(store)
    attach_node = _attach_node("att_1", source_commit_id="src_commit_aa")

    commit = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=[attach_node],
        head_node_id="att_1",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )

    # Expected layout: open marker + 2 source items (skipping the
    # summarized one) + close marker = 4 items.
    assert len(commit.items) == 4
    assert commit.items[0].reason == "attached_open"
    assert commit.items[-1].reason == "attached_close"
    # All four items tag the source commit id.
    for it in commit.items:
        assert it.attached_from == "src_commit_aa"
    # Middle two items carry the source content verbatim, unlocked
    # and reset to state=full so the rule pipeline can re-evaluate
    # them on the receiving branch.
    assert commit.items[1].rendered == "src user msg"
    assert commit.items[1].locked is False
    assert commit.items[1].state == "full"
    assert commit.items[2].rendered == "src assistant reply"
    # Markers reference the short hex of the source commit id.
    assert "src_comm" in commit.items[0].rendered


def test_second_attach_to_same_source_is_dedup_noop(store):
    """A subsequent commit whose new_nodes include another attach
    pointer to the SAME source_commit_id should produce no extra
    items — the parent commit's already-attached items satisfy the
    dedup check."""
    _seed_source_commit(store)

    first = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=[_attach_node("att_1", source_commit_id="src_commit_aa")],
        head_node_id="att_1",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )
    item_count_first = len(first.items)

    # Second turn: a new attach pointer to the same source_commit_id
    # plus a regular user message.
    second_attach = _attach_node("att_2", source_commit_id="src_commit_aa")
    user_node = {
        "id": "u1", "role": "user", "content": "follow-up",
    }
    second = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=first,
        new_nodes=[second_attach, user_node],
        head_node_id="u1",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )

    # All of first's items copied through + only the new user msg
    # (the second attach was deduped to zero items).
    assert len(second.items) == item_count_first + 1
    new_items = [it for it in second.items if it.state_set_at == second.id]
    assert len(new_items) == 1
    assert new_items[0].role == "user"
    assert new_items[0].rendered == "follow-up"
    assert new_items[0].attached_from is None


def test_attach_missing_source_commit_id_uses_legacy_fallback(store):
    """Old attach rows written before the expansion refactor have no
    source_commit_id field. The generator must fall through to the
    legacy single-item rendering so transcripts keep working."""
    # No source commit seeded; node lacks source_commit_id.
    attach_node = _attach_node("att_legacy", source_commit_id=None)
    # Make the legacy fallback observable by giving the node a
    # custom content string the marker wraps around.
    attach_node["content"] = "[legacy preview body]"

    commit = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=[attach_node],
        head_node_id="att_legacy",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )

    # Single item (no open/close markers, no copied source items).
    assert len(commit.items) == 1
    only = commit.items[0]
    assert only.reason == "attached_legacy"
    # attached_from stays None — the fallback path can't tag it
    # because there's no source_commit_id.
    assert only.attached_from is None
    assert "legacy preview body" in only.rendered
    assert "[Attached from branch" in only.rendered


def test_attach_with_unloadable_source_commit_id_falls_back(store):
    """source_commit_id present but the commit file doesn't exist —
    same fallback path as a totally missing field."""
    attach_node = _attach_node(
        "att_dangling", source_commit_id="commit_does_not_exist",
    )
    commit = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=[attach_node],
        head_node_id="att_dangling",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )
    assert len(commit.items) == 1
    assert commit.items[0].reason == "attached_legacy"


def test_two_attaches_to_different_sources_both_expand(store):
    """Two attach pointers, two different source_commit_ids → two
    independent attach blocks in the receiving commit. Dedup is
    per-source-id, not "first attach wins"."""
    _seed_source_commit(store)
    # A second source commit on the same source session.
    second_src = ContextCommit(
        id="src_commit_bb",
        session_id="src",
        parent_id=None,
        created_at=time.time(),
        head_node_id="src_node_2",
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=10,
        items=[
            ContextItem(
                source_node_id="src_u2", role="user",
                rendered="another source msg", tokens=10,
                state="full", locked=False, reason="new",
            ),
        ],
        summary="seeded #2",
    )
    save_commit(store, second_src)

    new_nodes = [
        _attach_node("att_a", source_commit_id="src_commit_aa",
                     label="A"),
        _attach_node("att_b", source_commit_id="src_commit_bb",
                     label="B"),
    ]
    commit = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=new_nodes,
        head_node_id="att_b",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )

    sources = {it.attached_from for it in commit.items if it.attached_from}
    assert sources == {"src_commit_aa", "src_commit_bb"}
    # Each block has its own open + close markers.
    opens = [it for it in commit.items if it.reason == "attached_open"]
    closes = [it for it in commit.items if it.reason == "attached_close"]
    assert len(opens) == 2
    assert len(closes) == 2


def test_attach_is_base_locks_expanded_items(store):
    """The merge path writes attach pointers with is_base=True on
    the chosen base peer. The generator locks all items in that
    block so summarize/aging can't fold them out."""
    _seed_source_commit(store)
    attach_node = _attach_node(
        "att_base", source_commit_id="src_commit_aa",
    )
    # Stamp the is_base flag into the attach blob.
    blob = json.loads(attach_node["extra"])
    blob["attach"]["is_base"] = True
    attach_node["extra"] = json.dumps(blob)

    commit = generate_commit(
        store=store,
        session_id="dst",
        parent_commit=None,
        new_nodes=[attach_node],
        head_node_id="att_base",
        budget_total=200_000,
        budget_summarize_threshold=160_000,
    )

    # Every item in the block (markers + copied source items) is
    # locked so summarize won't touch them.
    block = [it for it in commit.items if it.attached_from]
    assert block
    for it in block:
        assert it.locked is True
        assert it.reason in ("attached_open", "attached_close", "attached_base")


def test_summarize_does_not_split_attach_block():
    """summarize.rule_summarize must treat an attach block (contiguous
    items sharing one attached_from) as atomic — never folding only
    half of it. Either the whole block (open marker + items + close
    marker) goes into the summary, or none of it does."""
    from openprogram.context.rules.summarize import _pick_merge_range

    af = "src_commit_aa"
    items = [
        # 0: an oldest regular item — eligible for merge.
        ContextItem(
            source_node_id="old_u", role="user",
            rendered="oldest", tokens=100,
            state="full", locked=False, reason="new",
        ),
        # 1-3: an attach block. Together = 30 tokens.
        ContextItem(
            source_node_id="att", role="user",
            rendered="[open]", tokens=10,
            state="full", locked=False, reason="attached_open",
            attached_from=af,
        ),
        ContextItem(
            source_node_id="att_item1", role="user",
            rendered="attached body", tokens=10,
            state="full", locked=False, reason="attached",
            attached_from=af,
        ),
        ContextItem(
            source_node_id="att_close", role="user",
            rendered="[close]", tokens=10,
            state="full", locked=False, reason="attached_close",
            attached_from=af,
        ),
        # 4: a more recent item — leave it out of the merge range.
        ContextItem(
            source_node_id="newest", role="assistant",
            rendered="newest", tokens=50,
            state="full", locked=False, reason="new",
        ),
    ]
    start, end = _pick_merge_range(items)
    # Merge range must start at 0 (the eligible item) and END at
    # either 1 (just the oldest), or 4 (oldest + entire attach
    # block), but NEVER between 2 and 3 — that would split the
    # block.
    assert start == 0
    assert end in (1, 4)


def test_summarize_skips_attach_block_with_any_locked_member():
    """If any item inside an attach block is locked (or already
    summarized), the whole block must be skipped — we can't fold
    half a block."""
    from openprogram.context.rules.summarize import _pick_merge_range

    af = "src_commit_aa"
    items = [
        # An attach block whose middle item is already locked.
        ContextItem(
            source_node_id="att", role="user",
            rendered="[open]", tokens=10,
            state="full", locked=False, reason="attached_open",
            attached_from=af,
        ),
        ContextItem(
            source_node_id="att_locked", role="user",
            rendered="locked body", tokens=10,
            state="full", locked=True, reason="anchor_for:sm_xx",
            attached_from=af,
        ),
        ContextItem(
            source_node_id="att_close", role="user",
            rendered="[close]", tokens=10,
            state="full", locked=False, reason="attached_close",
            attached_from=af,
        ),
        # A regular item AFTER the block — that's the only thing
        # that could be merged.
        ContextItem(
            source_node_id="after", role="user",
            rendered="post-attach msg", tokens=10,
            state="full", locked=False, reason="new",
        ),
    ]
    start, end = _pick_merge_range(items)
    # Should skip the whole block (indices 0-2) and start at index 3.
    assert start == 3
    # With only one mergeable item, the range may be empty (total
    # token count too small for the 50%-target heuristic to bite).
    assert end in (3, 4)

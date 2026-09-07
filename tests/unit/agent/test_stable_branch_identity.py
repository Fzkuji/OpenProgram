from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


def _lineage(db, sid):
    writer = SessionNodeWriter(db, sid)
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    writer.append(Call(id="a2retry", role="llm", predecessor="u2", seq=5))
    writer.append(Call(id="u3", role="user", predecessor="a2retry", seq=6))
    writer.append(Call(id="a3", role="llm", predecessor="u3", seq=7))
    return writer


def test_original_path_keeps_fixed_identity_when_head_moves(tmp_path):
    from openprogram.browser_resources import current_branch, resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    first, _ = resolve_stable_branch("conversation", "a1", session_store=db)
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    later, _ = resolve_stable_branch("conversation", "a2", session_store=db)
    assert first == later
    assert first != "conversation:a2"
    db.set_head("conversation", "a2")
    current, _ = current_branch("conversation", session_store=db)
    assert current == first


def test_nested_fork_uses_deepest_divergence(tmp_path):
    from openprogram.browser_resources import resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    writer.append(Call(id="a2retry", role="llm", predecessor="u2", seq=5))
    writer.append(Call(id="u3", role="user", predecessor="a2retry", seq=6))
    writer.append(Call(id="a3", role="llm", predecessor="u3", seq=7))
    writer.append(Call(id="a3b", role="llm", predecessor="u3", seq=8))
    parent_fork, _ = resolve_stable_branch("conversation", "a3", session_store=db)
    nested, _ = resolve_stable_branch("conversation", "a3b", session_store=db)
    assert parent_fork.endswith(":a2retry")
    assert nested.endswith(":a3b")
    assert parent_fork != nested


def test_later_sibling_gets_its_own_fixed_anchor(tmp_path):
    from openprogram.browser_resources import resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    _lineage(db, "conversation")
    original, _ = resolve_stable_branch("conversation", "a2", session_store=db)
    sibling, _ = resolve_stable_branch("conversation", "a3", session_store=db)
    retry, _ = resolve_stable_branch("conversation", "a2retry", session_store=db)
    assert original != sibling
    assert sibling == retry
    assert sibling.endswith(":a2retry")
    assert original.endswith(":u1")


def test_branch_refs_are_reused_when_they_name_the_same_lineage(tmp_path):
    from openprogram.browser_resources import resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    writer.append(Call(id="a2", role="llm", predecessor="u1", seq=3))
    pair = db._open("conversation")
    git, idx = pair
    meta = dict(idx.meta)
    meta["branch_refs"] = {
        "branch_keep": {"branch_id": "branch_keep", "head_id": "a1"},
        "branch_fork": {"branch_id": "branch_fork", "head_id": "a2"},
    }
    git.write_meta(meta)
    idx.meta = meta
    keep, _ = resolve_stable_branch("conversation", "a1", session_store=db)
    fork, _ = resolve_stable_branch("conversation", "a2", session_store=db)
    assert keep == "branch_keep"
    assert fork == "branch_fork"

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
    db.set_branch_name("conversation", "a1", "五页计数器发布验收")
    first, first_name = resolve_stable_branch("conversation", "a1", session_store=db)
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    later, later_name = resolve_stable_branch("conversation", "a2", session_store=db)
    assert first == later
    assert first != "conversation:a2"
    assert first_name == "五页计数器发布验收"
    assert later_name == "五页计数器发布验收"
    db.set_head("conversation", "a2")
    current, current_name = current_branch("conversation", session_store=db)
    assert current == first
    assert current_name == "五页计数器发布验收"


def test_later_explicit_rename_wins_on_the_same_origin(tmp_path):
    from openprogram.browser_resources import resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    db.set_branch_name("conversation", "a1", "first-label")
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    db.set_branch_name("conversation", "a2", "renamed-label")
    branch_id, name = resolve_stable_branch("conversation", "a2", session_store=db)
    assert branch_id.endswith(":u1")
    assert name == "renamed-label"


def test_distinct_fork_names_stay_separate(tmp_path):
    from openprogram.browser_resources import resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    _lineage(db, "conversation")
    db.set_branch_name("conversation", "a2", "trunk-label")
    db.set_branch_name("conversation", "a2retry", "fork-label")
    original_id, original_name = resolve_stable_branch(
        "conversation", "a2", session_store=db,
    )
    sibling_id, sibling_name = resolve_stable_branch(
        "conversation", "a2retry", session_store=db,
    )
    later_id, later_name = resolve_stable_branch(
        "conversation", "a3", session_store=db,
    )
    earlier_id, earlier_name = resolve_stable_branch(
        "conversation", "a1", session_store=db,
    )
    assert original_id.endswith(":u1")
    assert sibling_id.endswith(":a2retry")
    assert later_id == sibling_id
    assert earlier_id == original_id
    assert original_name == "trunk-label"
    assert earlier_name == "trunk-label"
    assert sibling_name == "fork-label"
    assert later_name == "fork-label"


def test_missing_branch_name_stays_null(tmp_path):
    from openprogram.browser_resources import current_branch, resolve_stable_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    branch_id, name = resolve_stable_branch("conversation", "a1", session_store=db)
    db.set_head("conversation", "a1")
    current_id, current_name = current_branch("conversation", session_store=db)
    assert branch_id.endswith(":u1")
    assert name is None
    assert current_id == branch_id
    assert current_name is None


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
    db.set_branch_name("conversation", "a1", "keep-name")
    db.set_branch_name("conversation", "a2", "fork-name")
    keep, keep_name = resolve_stable_branch("conversation", "a1", session_store=db)
    fork, fork_name = resolve_stable_branch("conversation", "a2", session_store=db)
    assert keep == "branch_keep"
    assert fork == "branch_fork"
    assert keep_name == "keep-name"
    assert fork_name == "fork-name"


def test_active_branch_ref_keeps_name_when_ref_head_is_not_a_tip(tmp_path):
    from openprogram.browser_resources import current_branch

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "conversation")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    db.set_branch_name("conversation", "a1", "五页计数器发布验收")
    pair = db._open("conversation")
    git, idx = pair
    meta = dict(idx.meta)
    meta["branch_refs"] = {
        "branch_keep": {"branch_id": "branch_keep", "head_id": "a1"},
    }
    meta["active_branch_id"] = "branch_keep"
    git.write_meta(meta)
    idx.meta = meta
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    db.set_head("conversation", "a2")
    current, name = current_branch("conversation", session_store=db)
    assert current == "branch_keep"
    assert name == "五页计数器发布验收"

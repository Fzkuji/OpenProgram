from __future__ import annotations

from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


def _conversation(tmp_path):
    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    db.set_head("parent", "a1")
    return db


def test_incarnation_marks_live_pages_restoring_not_closed(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:oldinc:1", window_id="win", tab_id="tab-a",
        title="arXiv", target="https://arxiv.org/abs/1",
        connection_generation=4, session_id="parent",
        conversation_session_id="parent", execution_id="exec-1", live=True,
    )
    store.set_control_state("page:oldinc:1", "active", execution_id="exec-1")
    monkeypatch.setattr(br, "_PROCESS_INCARNATION", "newprocess01")
    restarted = br.BrowserResourceStore()
    row = restarted.get_resource("page:oldinc:1")
    assert row["lifecycle"] == "unavailable"
    assert int(row["live"] or 0) == 0
    assert row["control_state"] == "idle"
    assert row["title"] == "arXiv"
    assert row["target"] == "https://arxiv.org/abs/1"
    assert int(row["connection_generation"] or 0) == 0
    listed = restarted.list_rows(
        "parent", executions=[], parents={}, session_store=_conversation(tmp_path),
    )
    assert listed[0]["status"] == "unknown"
    assert listed[0]["control_state"] == "idle"
    restarted.retain(
        page_key="page:oldinc:1", window_id="win", tab_id="tab-a",
        title="Hacked", target="https://evil.test/", connection_generation=9,
        session_id="parent", conversation_session_id="parent", live=True,
    )
    frozen = restarted.get_resource("page:oldinc:1")
    assert frozen["lifecycle"] == "unavailable"
    assert int(frozen["live"] or 0) == 0
    assert frozen["title"] == "arXiv"


def test_adopt_transfers_associations_and_hides_predecessor(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:oldinc:1", window_id="win", tab_id="tab-a",
        title="arXiv", target="https://arxiv.org/abs/1",
        connection_generation=4, session_id="parent",
        conversation_session_id="parent", execution_id="exec-1", live=True,
    )
    store.set_control_state("page:oldinc:1", "paused", execution_id="exec-1")
    store.mark_restoring("page:oldinc:1")
    store.adopt_successor(
        successor_id="page:newinc:8", window_id="win", tab_id="tab-a",
        connection_generation=1, title="", target="",
    )
    successor = store.get_resource("page:newinc:8")
    pred = store.get_resource("page:oldinc:1")
    assert successor["lifecycle"] == "connected"
    assert int(successor["live"] or 0) == 1
    assert successor["control_state"] == "idle"
    assert successor["title"] == "arXiv"
    assert successor["target"] == "https://arxiv.org/abs/1"
    assert pred["lifecycle"] == "superseded"
    rows = store.list_rows("parent", executions=[], parents={}, session_store=db)
    ids = {row["resource_id"] for row in rows}
    assert "page:newinc:8" in ids
    assert "page:oldinc:1" not in ids
    store.adopt_successor(
        successor_id="page:newinc:8", window_id="win", tab_id="tab-a",
        connection_generation=1,
    )
    again = store.list_rows("parent", executions=[], parents={}, session_store=db)
    assert len([row for row in again if row["resource_id"] == "page:newinc:8"]) == 1


def test_closed_and_missing_tab_are_not_adopted(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:old:1", window_id="win", tab_id="tab-closed",
        title="Gone", target="https://example.test/gone",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_closed("page:old:1")
    store.adopt_successor(
        successor_id="page:new:2", window_id="win", tab_id="tab-closed",
        connection_generation=1, title="X", target="https://example.test/new",
    )
    closed = store.get_resource("page:old:1")
    assert closed["lifecycle"] == "closed"
    assert store.associations_for_page("page:new:2") == []
    assert all(row["resource_id"] != "page:old:1" or row["status"] == "closed" for row in store.list_rows(
        "parent", executions=[], parents={}, session_store=db,
    ))


def test_same_url_distinct_tabs_stay_distinct(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:old:a", window_id="win", tab_id="tab-a",
        title="One", target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.retain(
        page_key="page:old:b", window_id="win", tab_id="tab-b",
        title="Two", target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_restoring("page:old:a")
    store.mark_restoring("page:old:b")
    store.adopt_successor(
        successor_id="page:new:a", window_id="win", tab_id="tab-a",
        connection_generation=1,
    )
    store.adopt_successor(
        successor_id="page:new:b", window_id="win", tab_id="tab-b",
        connection_generation=1,
    )
    assert store.get_resource("page:new:a")["tab_id"] == "tab-a"
    assert store.get_resource("page:new:b")["tab_id"] == "tab-b"
    assert store.get_resource("page:old:a")["lifecycle"] == "superseded"
    assert store.get_resource("page:old:b")["lifecycle"] == "superseded"


def test_adopt_keeps_association_id_and_advances_generation(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:oldinc:1", window_id="win", tab_id="tab-a",
        title="Pinned", target="https://example.test/",
        connection_generation=40, session_id="parent",
        conversation_session_id="parent", execution_id="exec-1", live=True,
    )
    store.retain(
        page_key="page:oldinc:1", window_id="win", tab_id="tab-a",
        title="Pinned", target="https://example.test/",
        connection_generation=40, session_id="child",
        conversation_session_id="child", execution_id="exec-child", live=True,
    )
    pred = store.get_resource("page:oldinc:1")
    store.set_control_state("page:oldinc:1", "idle")
    # Force listed generation 40
    with store.connect() as dbn:
        dbn.execute(
            "UPDATE browser_resources SET generation=40 WHERE resource_id=?",
            ("page:oldinc:1",),
        )
    pred_ids = {item["id"] for item in store.associations_for_page("page:oldinc:1")}
    listed_before = store.list_rows("parent", executions=[], parents={}, session_store=db)
    public_id = listed_before[0]["id"]
    assert public_id.endswith(":unassigned")
    store.mark_restoring("page:oldinc:1")
    store.adopt_successor(
        successor_id="page:newinc:8", window_id="win", tab_id="tab-a",
        connection_generation=1,
    )
    successor = store.get_resource("page:newinc:8")
    assert int(successor["generation"] or 0) > 40
    assert int(successor["connection_generation"] or 0) == 1
    moved = store.associations_for_page("page:newinc:8")
    assert {item["id"] for item in moved} == pred_ids
    assert {item["conversation_session_id"] for item in moved} == {"parent", "child"}
    rows = store.list_rows("parent", executions=[], parents={}, session_store=db)
    assert len(rows) == 1
    assert rows[0]["id"] == public_id
    assert rows[0]["resource_id"] == "page:newinc:8"
    assert rows[0]["generation"] > 40
    late = store.get_resource("page:oldinc:1")
    assert late["lifecycle"] == "superseded"


def test_two_executions_same_branch_one_card_before_and_after_restore(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:old:1", window_id="win", tab_id="tab-a",
        title="Same", target="https://example.test/",
        session_id="parent", conversation_session_id="parent",
        execution_id="exec-1", live=True,
    )
    store.retain(
        page_key="page:old:1", window_id="win", tab_id="tab-a",
        title="Same", target="https://example.test/",
        session_id="parent", conversation_session_id="parent",
        execution_id="exec-2", live=True,
    )
    before = store.list_rows("parent", executions=[], parents={}, session_store=db)
    assert len(before) == 1
    public_id = before[0]["id"]
    store.mark_restoring("page:old:1")
    store.adopt_successor(
        successor_id="page:new:2", window_id="win", tab_id="tab-a",
        connection_generation=1,
    )
    after = store.list_rows("parent", executions=[], parents={}, session_store=db)
    assert len(after) == 1
    assert after[0]["id"] == public_id
    assert after[0]["resource_id"] == "page:new:2"


def test_restore_failed_status(tmp_path, monkeypatch):
    import openprogram.browser_resources as br

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = br.BrowserResourceStore()
    store.retain(
        page_key="page:old:1", window_id="win", tab_id="tab-a",
        title="X", target="https://example.test/",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_restore_failed("page:old:1")
    rows = store.list_rows("parent", executions=[], parents={}, session_store=db)
    assert rows[0]["status"] == "restore_failed"
    assert rows[0]["control_state"] == "idle"

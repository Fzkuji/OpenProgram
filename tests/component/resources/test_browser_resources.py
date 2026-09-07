from types import SimpleNamespace

from openprogram.browser_resources import sanitize_operation

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


def _conversation(tmp_path):
    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    writer.append(Call(id="u2", role="user", predecessor="a1", seq=3))
    writer.append(Call(id="a2", role="llm", predecessor="u2", seq=4))
    writer.append(Call(id="a2retry", role="llm", predecessor="u2", seq=5))
    db.set_head("parent", "a2retry")
    return db


def _exec(eid, session_id, parent=None):
    return SimpleNamespace(
        execution_id=eid, session_id=session_id, parent_execution_id=parent,
        status=SimpleNamespace(value="running"), status_version=1,
        capabilities=SimpleNamespace(pause=True),
        current_attempt_id="attempt-1",
    )


def test_retained_browser_page_survives_invocation_and_is_listed(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore
    from openprogram.webui.routes import processes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:1", window_id="win", tab_id="tab-a",
        title="Plans", target="https://user:secret@example.test/path?token=1",
        connection_generation=3, session_id="parent", execution_id="exec-1",
        user_message_id="u1", assistant_message_id="a1", agent_name="Research",
        conversation_session_id="parent",
    )
    store.clear_execution_use("exec-1")
    monkeypatch.setattr(processes, "_authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: None)
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_executions",
        lambda *args: [_exec("exec-1", "parent")],
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_parent_ids",
        lambda *args: {"exec-1": None},
    )
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        result = client.get("/api/session/parent/resources")
    assert result.status_code == 200
    body = result.json()
    browser = [row for row in body["items"] if row.get("source") == "browser"]
    assert len(browser) == 1
    row = browser[0]
    assert row["resource_id"] == "page:1"
    assert row["session_id"] == "parent"
    assert row["conversation_session_id"] == "parent"
    assert row["kind"] == "web"
    assert "token=" not in row["target"]
    assert "secret" not in row["target"]
    assert row["branch_id"].endswith(":u1")
    assert body["current_branch_id"] != row["branch_id"]
    assert body["current_branch_id"].endswith(":a2retry")


def test_same_url_does_not_merge_distinct_pages(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    url = "https://example.test/shared"
    store.retain(
        page_key="page:1", window_id="win", tab_id="tab-a", title="A",
        target=url, connection_generation=1, session_id="parent",
        execution_id="exec-1", user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent",
    )
    store.retain(
        page_key="page:2", window_id="win", tab_id="tab-b", title="B",
        target=url, connection_generation=1, session_id="parent",
        execution_id="exec-1", user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent",
    )
    rows = store.list_rows(
        "parent", executions=[_exec("exec-1", "parent")],
        parents={"exec-1": None}, session_store=db,
    )
    assert {row["resource_id"] for row in rows} == {"page:1", "page:2"}


def test_child_execution_projects_to_parent_origin_branch_not_checkout(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:child", window_id="win", tab_id="tab-c", title="Child",
        target="https://example.test/child", connection_generation=1,
        session_id="child", execution_id="child-exec",
        user_message_id="child-user", assistant_message_id="child-assistant",
        conversation_session_id="parent", agent_name="Child Agent",
    )
    rows = store.list_rows(
        "parent",
        executions=[_exec("exec-1", "parent"), _exec("child-exec", "child", parent="exec-1")],
        parents={"exec-1": None, "child-exec": "exec-1"},
        session_store=db,
        execution_inputs={
            "exec-1": SimpleNamespace(user_message_id="u1", assistant_message_id="a1"),
            "child-exec": SimpleNamespace(
                user_message_id="child-user", assistant_message_id="child-assistant",
            ),
        },
    )
    assert len(rows) == 1
    assert rows[0]["session_id"] == "child"
    assert rows[0]["conversation_session_id"] == "parent"
    assert rows[0]["branch_id"].endswith(":u1")
    assert not rows[0]["branch_id"].endswith(":a2retry")


def test_legacy_page_without_anchor_is_unassigned(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:legacy", window_id="win", tab_id="tab-l", title="Legacy",
        target="https://example.test/legacy", connection_generation=1,
        session_id="parent", execution_id=None, conversation_session_id="parent",
    )
    rows = store.list_rows(
        "parent", executions=[], parents={}, session_store=db,
    )
    assert rows[0]["branch_id"] is None
    assert rows[0]["id"].endswith(":unassigned")


def test_disconnect_leaves_unavailable_descriptor_without_live_lease(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:1", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=3,
        session_id="parent", execution_id="exec-1",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    store.mark_unavailable("page:1")
    rows = store.list_rows(
        "parent", executions=[_exec("exec-1", "parent")],
        parents={"exec-1": None}, session_store=db,
    )
    assert rows[0]["status"] in {"closed", "unknown"}
    assert rows[0]["control_state"] == "closed"
    assert rows[0]["generation"] >= 3


def test_resource_route_denies_other_conversation_before_reading(tmp_path, monkeypatch):
    from openprogram.webui.routes import processes

    monkeypatch.setattr(processes, "_actor_and_session", lambda request: ({}, "allowed"))
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        listed = client.get("/api/session/other/resources")
        paused = client.post(
            "/api/session/other/resources/page:1/control",
            json={"action": "pause", "command_id": "cmd-1", "generation": 1},
        )
    assert listed.status_code == 404
    assert paused.status_code == 404


def test_control_rejects_stale_generation_and_does_not_label_paused_from_request(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import BrowserResourceStore
    from openprogram.webui.routes import processes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:1", window_id="win", tab_id="tab-a", title="Plans",
        target="https://example.test/", connection_generation=4,
        session_id="parent", execution_id="exec-1",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    monkeypatch.setattr(processes, "_authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(processes, "_actor_and_session", lambda request: ({"authority_tier": "owner"}, None))
    monkeypatch.setattr("openprogram.execution.default_store", lambda: None)
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_executions",
        lambda *args: [_exec("exec-1", "parent")],
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_parent_ids",
        lambda *args: {"exec-1": None},
    )
    pauses = []

    class _Service:
        async def request_pause(self, **kwargs):
            pauses.append(kwargs)
            return SimpleNamespace(
                execution=SimpleNamespace(status=SimpleNamespace(value="pausing"), status_version=2),
                delivered=True,
            )

        @property
        def effects(self):
            return SimpleNamespace(list_unresolved=lambda eid: [])

        @property
        def executions(self):
            return SimpleNamespace(get_execution=lambda eid: _exec(eid, "parent"))

    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: _Service())
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        stale = client.post(
            "/api/session/parent/resources/page:1/control",
            json={"action": "pause", "command_id": "old", "generation": 1},
        )
        pause = client.post(
            "/api/session/parent/resources/page:1/control",
            json={"action": "pause", "command_id": "pause-1", "generation": 4},
        )
        resume = client.post(
            "/api/session/parent/resources/page:1/control",
            json={"action": "resume", "command_id": "resume-1", "generation": 4},
        )
    assert stale.status_code == 409
    assert pause.status_code == 200
    body = pause.json()
    assert "item" not in body
    assert body["control_state"] != "paused"
    assert body["control_state"] in {"yielding", "stop_unconfirmed"}
    assert resume.status_code == 409


def test_click_receipt_uses_viewport_dimensions_and_target_center():
    dispatched = sanitize_operation(
        action="click",
        arguments={"ref": "e1"},
        result={"ok": True, "point": {"x": 10, "y": 20, "width": 40, "height": 10}},
        phase="dispatched",
        viewport={"width": 800, "height": 600},
        operation_id="op_1",
        frame_id="frame-1",
    )
    assert dispatched["id"] == "op_1"
    assert dispatched["point"] == {"x": 30, "y": 25, "width": 800, "height": 600}
    coordinate = sanitize_operation(
        action="click",
        arguments={"x": 12, "y": 48},
        result={"ok": True},
        phase="acknowledged",
        viewport={"width": 1024, "height": 768},
        operation_id="op_1",
    )
    assert coordinate["point"] == {"x": 12, "y": 48, "width": 1024, "height": 768}
    assert sanitize_operation(
        action="click", arguments={"x": 1, "y": 1}, result={"ok": True},
        phase="dispatched",
    ).get("point") is None


def test_operation_event_uses_operating_execution_branch_not_first_association(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import (
        BrowserResourceStore, report_browser_operation, sanitize_operation,
    )

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    store = BrowserResourceStore()
    store.retain(
        page_key="page:shared", window_id="win", tab_id="tab-a", title="Shared",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id="exec-old",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    store.retain(
        page_key="page:shared", window_id="win", tab_id="tab-a", title="Shared",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id="exec-new",
        user_message_id="u2", assistant_message_id="a2retry",
        conversation_session_id="parent", live=True,
    )
    captured = []

    def _emit(row, *, page_key):
        captured.append(dict(row))

    monkeypatch.setattr("openprogram.browser_resources.emit_browser_resource", _emit)
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_executions",
        lambda *args: [
            SimpleNamespace(
                execution_id="exec-old", session_id="parent", parent_execution_id=None,
                status=SimpleNamespace(value="completed"),
            ),
            SimpleNamespace(
                execution_id="exec-new", session_id="parent", parent_execution_id=None,
                status=SimpleNamespace(value="running"),
            ),
        ],
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_parent_ids",
        lambda *args: {"exec-old": None, "exec-new": None},
    )
    monkeypatch.setattr(
        "openprogram.execution.default_store",
        lambda: SimpleNamespace(
            get_execution_input=lambda eid: SimpleNamespace(
                user_message_id="u1" if eid == "exec-old" else "u2",
                assistant_message_id="a1" if eid == "exec-old" else "a2retry",
            ),
            get_execution=lambda eid: None,
        ),
    )
    op = sanitize_operation(
        action="click", arguments={"x": 10, "y": 10}, result={"ok": True},
        phase="dispatched", viewport={"width": 800, "height": 600},
        operation_id="op_shared", frame_id="frame-1",
    )
    report_browser_operation(
        "page:shared", op, follow=True, execution_id="exec-new",
    )
    assert len(captured) == 1
    assert captured[0]["execution_id"] == "exec-new"
    assert str(captured[0].get("branch_id") or "").endswith(":a2retry")


def test_completed_owner_becomes_idle_when_page_is_released_and_effects_are_clear(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import (
        BrowserResourceStore, fence_page_writes, project_conversation_resources,
        writes_fenced,
    )

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    store = BrowserResourceStore()
    store.retain(
        page_key="page:term", window_id="win", tab_id="tab-a", title="Done",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id="exec-term",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    fence_page_writes("page:term", input_seq=1)
    store.set_control_state("page:term", "yielding", command_id="pause-term")
    completed = SimpleNamespace(
        execution_id="exec-term", session_id="parent", parent_execution_id=None,
        status=SimpleNamespace(value="completed"),
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_executions",
        lambda *args: [completed],
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_parent_ids",
        lambda *args: {"exec-term": None},
    )
    monkeypatch.setattr("openprogram.execution.default_store", lambda: SimpleNamespace(
        get_execution_input=lambda eid: SimpleNamespace(
            user_message_id="u1", assistant_message_id="a1",
        ),
        get_execution=lambda eid: completed if eid == "exec-term" else None,
    ))
    monkeypatch.setattr(
        "openprogram.execution.default_control_service",
        lambda: SimpleNamespace(effects=SimpleNamespace(list_unresolved=lambda eid: [])),
    )
    rows, _, _ = project_conversation_resources("parent")
    row = next(item for item in rows if item["resource_id"] == "page:term")
    stored = store.get_resource("page:term")
    assert row["control_state"] == "idle"
    assert stored["control_state"] == "idle"
    assert not stored.get("pause_command_id")
    assert writes_fenced("page:term") is False


def test_terminal_unknown_effects_stay_stop_unconfirmed_and_keep_fence(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import (
        BrowserResourceStore, fence_page_writes, project_conversation_resources,
        writes_fenced,
    )

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    db = _conversation(tmp_path)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    store = BrowserResourceStore()
    store.retain(
        page_key="page:fail", window_id="win", tab_id="tab-a", title="Failed",
        target="https://example.test/", connection_generation=1,
        session_id="parent", execution_id="exec-fail",
        user_message_id="u1", assistant_message_id="a1",
        conversation_session_id="parent", live=True,
    )
    fence_page_writes("page:fail", input_seq=1)
    store.set_control_state("page:fail", "yielding", command_id="pause-fail")
    failed = SimpleNamespace(
        execution_id="exec-fail", session_id="parent", parent_execution_id=None,
        status=SimpleNamespace(value="failed"),
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_executions",
        lambda *args: [failed],
    )
    monkeypatch.setattr(
        "openprogram.execution.conversation_scope.conversation_parent_ids",
        lambda *args: {"exec-fail": None},
    )
    monkeypatch.setattr("openprogram.execution.default_store", lambda: SimpleNamespace(
        get_execution_input=lambda eid: SimpleNamespace(
            user_message_id="u1", assistant_message_id="a1",
        ),
        get_execution=lambda eid: failed if eid == "exec-fail" else None,
    ))

    class _Boom:
        def list_unresolved(self, execution_id):
            raise RuntimeError("effects unavailable")

    monkeypatch.setattr(
        "openprogram.execution.default_control_service",
        lambda: SimpleNamespace(effects=_Boom()),
    )
    rows, _, _ = project_conversation_resources("parent")
    row = next(item for item in rows if item["resource_id"] == "page:fail")
    stored = store.get_resource("page:fail")
    assert row["control_state"] == "stop_unconfirmed"
    assert stored["control_state"] == "stop_unconfirmed"
    assert writes_fenced("page:fail") is True

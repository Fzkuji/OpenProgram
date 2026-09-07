import json
import threading
import time
from types import SimpleNamespace

from openprogram.programs.workflow.browser.web_use_runtime import (
    SUPPORTED_BACKENDS,
    WebUseSessionRegistry,
)


class _GuardedAdapter:
    supports_operation_guard = True

    def __init__(self):
        self.acts = []
        self.observes = []

    def observe(self, session, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        self.observes.append(dict(arguments))
        return {"ok": True, "frame_id": "frame-1"}

    def act(self, session, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        self.acts.append(dict(arguments))
        return {"ok": True, "frame_id": "frame-2"}

    def verify(self, session, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        return {"ok": True, "passed": True}

    def close(self, session):
        return None


def _registry(adapter, page_key="page:owned"):
    return WebUseSessionRegistry(
        adapters={name: adapter for name in SUPPORTED_BACKENDS},
        binding_validator=lambda binding, **kwargs: {"ok": True},
        binding_revision_resolver=lambda binding: {
            "page_revision": 1, "access_revision": 1, "geometry_revision": 0,
        },
        page_key_resolver=lambda binding: page_key,
        release_context=lambda context: None,
    )


def test_act_is_blocked_after_human_fence_without_waiting_on_operation_lock(tmp_path, monkeypatch):
    from openprogram.browser_resources import fence_page_writes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    adapter = _GuardedAdapter()
    registry = _registry(adapter)
    observed = registry.execute(
        command="observe", owner_id="owner", binding_id="owned",
        backend="open_claude_chrome",
    )
    session_id = observed["web_session_id"]
    page_key = "page:owned"
    session = registry._sessions[session_id]
    started = threading.Event()
    release = threading.Event()

    def occupy():
        with session.operation_lock:
            started.set()
            assert release.wait(2)

    holder = threading.Thread(target=occupy)
    holder.start()
    assert started.wait(1)
    fenced_at = time.monotonic()
    assert fence_page_writes(page_key, input_seq=2) is True
    assert time.monotonic() - fenced_at < 0.2
    blocked = registry.execute(
        command="act", owner_id="owner", web_session_id=session_id,
        backend="open_claude_chrome",
        arguments={"action": "click", "expected_frame_id": "frame-1", "ref": "e1"},
    )
    assert blocked.get("ok") is False
    assert blocked.get("reason_code") == "write_fenced"
    assert adapter.acts == []
    release.set()
    holder.join(2)
    still = registry.execute(
        command="act", owner_id="owner", web_session_id=session_id,
        backend="open_claude_chrome",
        arguments={"action": "click", "expected_frame_id": "frame-1", "ref": "e1"},
    )
    assert still.get("reason_code") == "write_fenced"
    seen = registry.execute(
        command="observe", owner_id="owner", web_session_id=session_id,
        backend="open_claude_chrome",
    )
    assert seen.get("ok") is not False or seen.get("web_session_id")
    registry.close_all()


def test_unguarded_backend_stays_explicit_and_does_not_acquire_when_fenced(tmp_path, monkeypatch):
    from openprogram.browser_resources import fence_page_writes

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    fence_page_writes("page:owned", input_seq=1)
    calls = []
    backend = SimpleNamespace(observe=lambda *args, **kwargs: calls.append(1))
    registry = WebUseSessionRegistry(
        adapters={name: backend for name in SUPPORTED_BACKENDS},
        binding_validator=lambda binding, **kwargs: {"ok": True},
        binding_revision_resolver=lambda binding: {},
        page_key_resolver=lambda binding: "page:owned",
        release_context=lambda context: None,
    )
    result = registry.execute(
        command="observe", owner_id="owner", binding_id="owned",
        before_dispatch=lambda: None,
    )
    assert result == {"ok": False, "reason_code": "guarded_dispatch_unsupported"}
    assert calls == []


def test_observe_open_list_verify_do_not_mark_page_active(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_execution_id", lambda: "exec-obs",
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_session_id", lambda: "parent",
    )
    monkeypatch.setattr(
        "openprogram.processes.current_owner",
        lambda: ("parent", "exec-obs", None),
    )
    adapter = _GuardedAdapter()
    registry = _registry(adapter, page_key="page:obs")
    observed = registry.execute(
        command="observe", owner_id="owner", binding_id="owned",
        backend="open_claude_chrome",
    )
    assert observed.get("ok") is not False
    row = BrowserResourceStore().get_resource("page:obs")
    assert row is not None
    assert row.get("control_state") != "active"
    verified = registry.execute(
        command="verify", owner_id="owner",
        web_session_id=observed["web_session_id"],
        backend="open_claude_chrome",
        arguments={"expected_frame_id": "frame-1"},
    )
    assert verified.get("ok") is not False
    row = BrowserResourceStore().get_resource("page:obs")
    assert row.get("control_state") != "active"
    listed = registry.list_pages(
        context={
            "context_id": "ctx",
            "window_id": "win",
            "surfaces": [{"binding_id": "owned", "surface_key": "page"}],
        },
        owner_id="owner",
    )
    assert listed.get("ok") is True
    row = BrowserResourceStore().get_resource("page:obs")
    assert row.get("control_state") != "active"
    registry.close_all()


def test_act_emits_dispatched_before_blocking_adapter_and_acks_same_operation(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_execution_id", lambda: "exec-act",
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_session_id", lambda: "parent",
    )
    started = threading.Event()
    release = threading.Event()
    captured = []

    class _BlockingAdapter(_GuardedAdapter):
        def act(self, session, arguments, *, before_dispatch=None):
            if before_dispatch is not None:
                before_dispatch()
            started.set()
            assert release.wait(2)
            self.acts.append(dict(arguments))
            return {"ok": True, "frame_id": "frame-2"}

    def _emit(row, *, page_key):
        captured.append(dict((row or {}).get("last_operation") or {}))

    monkeypatch.setattr("openprogram.browser_resources.emit_browser_resource", _emit)
    registry = _registry(_BlockingAdapter(), page_key="page:act")
    observed = registry.execute(
        command="observe", owner_id="owner", binding_id="owned",
        backend="open_claude_chrome",
    )
    session_id = observed["web_session_id"]
    worker = threading.Thread(
        target=lambda: registry.execute(
            command="act", owner_id="owner", web_session_id=session_id,
            backend="open_claude_chrome",
            arguments={"action": "click", "expected_frame_id": "frame-1", "ref": "e1"},
        )
    )
    worker.start()
    assert started.wait(2)
    assert captured, "dispatched receipt must exist before adapter.act returns"
    assert captured[0].get("phase") == "dispatched"
    op_id = captured[0].get("id")
    assert op_id
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    phases = [item.get("phase") for item in captured]
    assert "acknowledged" in phases
    ack = next(item for item in captured if item.get("phase") == "acknowledged")
    assert ack.get("id") == op_id
    row = BrowserResourceStore().get_resource("page:act")
    assert row is not None
    stored_op = row.get("last_operation")
    if isinstance(stored_op, str):
        stored_op = json.loads(stored_op)
    assert (stored_op or {}).get("id") == op_id
    registry.close_all()

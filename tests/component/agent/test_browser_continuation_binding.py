"""Resumed public dispatch keeps browser ownership and restores caller context."""
from types import SimpleNamespace

import pytest


def test_public_continuation_binds_exact_turn_and_releases_on_error(monkeypatch):
    from openprogram.agent import dispatcher, surface_context
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.store import _current_turn_id
    from openprogram.agent.run_control import get_current_session_id

    request = TurnRequest(session_id="resume-session", agent_id="main", user_text="resume", source="component")
    db = SimpleNamespace(get_session=lambda _: {}, message_exists=lambda *_: True)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.context.persistence.rendered_history", lambda *_a, **_k: [])
    # Keep the real binding implementation; unrelated persistence/provider setup is inert.
    monkeypatch.setattr("openprogram.store.SessionNodeWriter", lambda *_: object())
    monkeypatch.setattr("openprogram.providers.registry.create_runtime", lambda: None)
    before = (_current_turn_id.get(), get_current_session_id())

    def run(**kwargs):
        from openprogram.programs.workflow.browser.web_use_runtime import WebUseSessionRegistry, SUPPORTED_BACKENDS
        adapter = SimpleNamespace(
            observe=lambda *_: {"ok": True, "frame_id": "frame-1"}, close=lambda *_: None,
        )
        registry = WebUseSessionRegistry(adapters={name: adapter for name in SUPPORTED_BACKENDS},
            binding_validator=lambda _: {"ok": True}, release_context=lambda _: None,
            page_key_resolver=lambda _: "page-1", binding_revision_resolver=lambda _: {})
        owner = surface_context.web_use_owner_id({"context_id": "first"})
        listed = registry.list_pages(owner_id=owner, context={"context_id": "first",
            "surfaces": [{"binding_id": "binding-1", "surface_key": "p1"}]})
        token = listed["pages"][0]["page_context_token"]
        try:
            observed = registry.execute(command="observe", backend="open_claude_chrome",
                owner_id=surface_context.web_use_owner_id(None), page_context_token=token)
            assert observed["ok"] is True
            assert observed["web_session_id"]
        finally:
            registry.release_owner(owner)
        raise RuntimeError("stop after ownership check")

    monkeypatch.setattr(dispatcher, "_run_loop_blocking", run)
    continuation = SimpleNamespace(request=request, assistant_message_id="reply",
        state=SimpleNamespace(payload={"turn": {"user_message_id": "user"}}))
    with pytest.raises(RuntimeError, match="stop after ownership check"):
        dispatcher.process_agent_continuation(continuation)
    assert (_current_turn_id.get(), get_current_session_id()) == before


@pytest.mark.parametrize("reason", ["page_context_owner_mismatch", "write_fenced"])
def test_registered_browser_tool_reports_rejection_as_error(monkeypatch, reason):
    import asyncio
    import json
    from openprogram.programs.workflow import browser

    rejection = {"ok": False, "reason_code": reason}
    monkeypatch.setattr(browser, "_execute_web_use", lambda *_: rejection)
    tool = browser.web_use._agent_tool
    result = asyncio.run(tool.execute("browser-rejected", {"command": "observe"}, None, None))
    assert result.is_error is True
    assert json.loads(result.content[0].text) == rejection

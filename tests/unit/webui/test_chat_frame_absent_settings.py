"""A chat frame that leaves a setting out keeps the conversation's own.

The CLI's ``--print``/``--resume`` frame and the TUI send no ``web_search``;
the Web composer always sends it. An absent field must not switch the
setting off, or a resumed turn loses the tool and its cached prefix.
"""
import asyncio

from tests.unit.dag.test_rewind_modes import Session, store  # noqa: F401
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server


def _parse(cmd):
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _parse_chat_command
    return asyncio.run(_parse_chat_command(None, cmd))


def test_a_frame_without_settings_leaves_every_setting_to_the_conversation():
    req = _parse({"action": "chat", "text": "hi", "session_id": "s"})
    assert (req.web_search_flag, req.thinking_effort, req.permission_mode,
            req.sandbox_flag, req.service_tier, req.tools_flag) == (None,) * 6


def test_a_frame_with_a_null_web_search_leaves_it_to_the_conversation():
    assert _parse({"text": "hi", "session_id": "s", "web_search": None}).web_search_flag is None


def test_an_explicit_web_search_choice_is_kept():
    assert _parse({"text": "hi", "session_id": "s", "web_search": False}).web_search_flag is False
    assert _parse({"text": "hi", "session_id": "s", "web_search": True}).web_search_flag is True


def test_a_resumed_turn_keeps_the_conversations_web_search(store, monkeypatch):  # noqa: F811
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    from openprogram.runtime.conversation.session_config import tools_override_from_config
    Session(store, "s").turn_edits()
    store.update_session("s", web_search=True, thinking_effort="high")
    req = _parse({"action": "chat", "text": "again", "session_id": "s"})
    cfg, _ = _save_run_config(server, req, {"id": "s"}, "second-user")
    assert store.get_session("s")["web_search"] is True
    assert cfg.thinking_effort == "high"
    assert tools_override_from_config(cfg) == {"inherit": True, "web_search": True}


def test_a_conversation_without_a_web_search_choice_follows_its_agent(store, monkeypatch):  # noqa: F811
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    from openprogram.runtime.conversation.session_config import tools_override_from_config
    Session(store, "s").turn_edits()
    req = _parse({"action": "chat", "text": "again", "session_id": "s"})
    cfg, _ = _save_run_config(server, req, {"id": "s"}, "second-user")
    assert store.get_session("s").get("web_search") is None
    assert tools_override_from_config(cfg) is None


def _overrides():
    from openprogram.interfaces.server.ws_actions.chat.turn_request import TURN_OVERRIDES
    return TURN_OVERRIDES


# A value each override takes, sent on its own.
_SENT = {"thinking_effort": "high", "web_search": False, "permission_mode": "ask",
         "sandbox_enabled": True, "service_tier": "priority", "surface": {"kind": "browser"},
         "turn_model": "anthropic/claude-x", "turn_effort": "high",
         "turn_permission_mode": "ask", "turn_no_web": True}


def test_every_override_has_a_sample_and_says_whether_it_sticks():
    assert {o.key for o in _overrides()} == set(_SENT)
    assert all(isinstance(o.sticky, bool) for o in _overrides())


def test_each_override_left_out_keeps_the_conversation_s_value():
    """One frame per override, carrying every other one: the missing one
    alone reads as unchanged."""
    for override in _overrides():
        sent = {key: value for key, value in _SENT.items() if key != override.key}
        req = _parse({"text": "hi", "session_id": "s", **sent})
        assert getattr(req, override.attribute) is None, override.key
        for other in _overrides():
            if other.key != override.key:
                assert getattr(req, other.attribute) is not None, (override.key, other.key)


def test_each_override_sent_as_null_keeps_the_conversation_s_value():
    for override in _overrides():
        req = _parse({"text": "hi", "session_id": "s", override.key: None})
        assert getattr(req, override.attribute) is None, override.key

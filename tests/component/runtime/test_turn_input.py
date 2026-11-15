"""A typed chat message becomes its turn request in one place.

``turn_input.build_turn_request`` is what the Worker's chat command, a retry,
``--print``, ACP, a chat channel and the owner's automation build their
request with: the authority they admit under, the conversation's permission,
the run settings they take from the conversation, and the run note for a
run the surface says no one watches.
"""
from __future__ import annotations

import types

import pytest

from openprogram.runtime.conversation import turn_input
from openprogram.runtime.conversation.run_note import RUN_NOTE
from openprogram.runtime.permissions.authority import local_owner_authority


def _build(**kwargs):
    defaults = dict(agent_id="main", source="web", msg_id="m1",
                    authority=local_owner_authority(), permission=None)
    return turn_input.build_turn_request("local_s", "hello", **{**defaults, **kwargs})


def test_a_run_no_one_watches_carries_the_run_note_and_others_do_not():
    unattended = _build(authority={**local_owner_authority(), "interaction": "non-interactive"},
                        unattended=True)
    attended = _build()

    assert unattended.run_note == RUN_NOTE
    assert attended.run_note is None
    assert (unattended.user_text, unattended.user_msg_id, unattended.source) == (
        "hello", "m1", "web")


def test_a_paired_channel_turn_is_non_interactive_but_gets_no_run_note():
    """A chat channel admits a paired sender as non-interactive, so approvals
    are refused, but a person reads every reply: the note is the surface's
    call, not the authority's."""
    from openprogram.runtime.permissions.authority import paired_channel_authority

    authority = paired_channel_authority("telegram", "acct", "u1", "Alice")
    request = _build(source="telegram", authority=authority,
                     permission={"permission_mode": "ask"})
    assert (request.interaction, request.run_note) == ("non-interactive", None)


def test_a_surface_field_does_not_add_the_run_note():
    """A /task is marked in its own field; ``interaction`` keeps the authority's."""
    from openprogram.runtime.agent.turn_request import slash_action_of

    request = _build(slash_action="spawn")
    assert (request.slash_action, request.interaction, request.run_note) == (
        "spawn", local_owner_authority().get("interaction"), None)
    assert slash_action_of(request) == "spawn"


@pytest.mark.parametrize("stored, action", [
    ({"slash_action": "merge", "interaction": "interactive"}, "merge"),
    # Admitted before the field existed: the action was in ``interaction``.
    ({"interaction": "spawn"}, "spawn"),
    ({"interaction": "non-interactive"}, None),
    ({}, None),
])
def test_a_stored_request_names_its_slash_action_either_way(stored, action):
    from openprogram.runtime.agent.turn_request import slash_action_of

    assert slash_action_of(stored) == action


def test_the_conversation_s_permission_and_run_settings_are_read_from_it(monkeypatch):
    from openprogram.runtime.conversation import session_config

    monkeypatch.setattr(session_config, "conversation_permission",
                        lambda session_id: {"permission_mode": "ask", "permission_rules": ["r"]})
    config = types.SimpleNamespace(thinking_effort="high", additional_working_dirs=["/w"])
    monkeypatch.setattr(session_config, "tools_override_from_config", lambda cfg: ["read"])

    request = _build(permission=turn_input.CONVERSATION,
                     run_config=("thinking_effort", "tools_override", "additional_working_dirs"),
                     run_cfg=config)

    assert (request.permission_mode, request.permission_rules) == ("ask", ["r"])
    assert (request.thinking_effort, request.tools_override, request.additional_working_dirs) == (
        "high", ["read"], ["/w"])


def test_a_surface_s_own_fields_win_and_unknown_run_settings_are_refused():
    request = _build(run_config=("thinking_effort",),
                     run_cfg=types.SimpleNamespace(thinking_effort="low"),
                     thinking_effort="high")
    assert request.thinking_effort == "high"
    with pytest.raises(ValueError, match="model_override"):
        _build(run_config=("model_override",))

"""A chat message someone typed, as the turn request it becomes.

Every surface that takes a person's text and starts a turn with it (the
Worker's chat command, a retry or edit, ``openprogram --print``, ACP, a chat
channel, a turn the owner's own automation starts) builds the request here,
so what a request carries for the same input does not depend on which
surface took it. The surface keeps only what is its own: how it answers
(an ack, a broadcast, a channel reply), its message ids and its extra fields.

What is decided here:

- the authority the surface admits the turn under, last but for the
  surface's own fields, so it cannot be overridden by the conversation's
  settings;
- the conversation's permission (:data:`CONVERSATION`): its mode and its
  merged rules, as ``session_config.conversation_permission`` reads them;
- the run settings the surface takes from the conversation (``run_config``:
  ``thinking_effort``, ``tools_override``, ``additional_working_dirs``);
- the run note (``run_note.RUN_NOTE``) for a turn the surface says no one
  is watching (``unattended=True``). The surface decides, not the
  authority: a chat channel admits a paired sender as non-interactive, so
  approvals are refused, yet a person reads every reply there.

Jobs, scheduled runs, sub-agents and merges are not built here: their input
is a task definition, not something a person typed.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

#: ``permission=CONVERSATION``: the conversation's permission mode and rules.
CONVERSATION = "conversation"

#: The longest message a person may send, in characters, as Codex's
#: ``MAX_USER_INPUT_TEXT_CHARS``. A message is never cut: below this it is
#: sent whole in its turn and on every later request; above it the send is
#: refused (``input_too_large``) and the text stays where it was typed.
#: Admission measures the text against the same number
#: (``agent_input_budget.AGENT_USER_TEXT_MAX_CHARS``), outside the 256 KiB
#: envelope budget, so a message a surface accepts is admitted.
MAX_USER_INPUT_CHARS = 1 << 20

#: The error code a surface answers a too-long message with.
INPUT_TOO_LARGE = "input_too_large"


def input_too_large(text: Any) -> str | None:
    """The message to show when ``text`` is longer than a person may send,
    or None when it may be sent."""
    if not isinstance(text, str) or len(text) <= MAX_USER_INPUT_CHARS:
        return None
    return (f"This message is {len(text):,} characters; one message can be at most "
            f"{MAX_USER_INPUT_CHARS:,}. Shorten it, or save it to a file and refer to the file.")

#: The run settings a surface may take from the conversation.
RUN_CONFIG_FIELDS = frozenset({"thinking_effort", "tools_override", "additional_working_dirs"})


def build_turn_request(
    session_id: str,
    text: str,
    *,
    agent_id: str | None,
    source: str,
    msg_id: str,
    authority: Mapping[str, Any],
    permission: str | Mapping[str, Any] | None = CONVERSATION,
    run_config: Iterable[str] = (),
    run_cfg: Any = None,
    unattended: bool = False,
    **fields: Any,
):
    """The ``TurnRequest`` for ``text`` typed into ``session_id``.

    ``permission`` is :data:`CONVERSATION`, explicit ``permission_mode`` /
    ``permission_rules`` fields, or ``None`` to leave both to admission.
    ``run_config`` names the run settings taken from the conversation's run
    configuration (``run_cfg`` when the caller already read it).
    ``unattended`` adds the run note for a run no one is watching. ``fields``
    are the surface's own and win over everything else.
    """
    from openprogram.runtime.agent.turn_request import TurnRequest

    values: dict[str, Any] = {
        "session_id": session_id,
        "user_text": text,
        "agent_id": agent_id,
        "source": source,
        "user_msg_id": msg_id,
    }
    if permission == CONVERSATION:
        from openprogram.runtime.conversation.session_config import conversation_permission

        values.update(conversation_permission(session_id))
    elif permission is not None:
        values.update(permission)
    wanted = set(run_config)
    unknown = wanted - RUN_CONFIG_FIELDS
    if unknown:
        raise ValueError(f"not a run setting a surface takes: {sorted(unknown)}")
    if wanted:
        from openprogram.runtime.conversation.session_config import (
            load_session_run_config,
            tools_override_from_config,
        )

        config = run_cfg if run_cfg is not None else load_session_run_config(session_id)
        for name in sorted(wanted):
            values[name] = (tools_override_from_config(config) if name == "tools_override"
                            else getattr(config, name, [] if name == "additional_working_dirs"
                                         else None))
    values.update(authority)
    if unattended:
        from openprogram.runtime.conversation.run_note import RUN_NOTE

        values["run_note"] = RUN_NOTE
    values.update(fields)
    return TurnRequest(**values)


__all__ = ["CONVERSATION", "RUN_CONFIG_FIELDS", "build_turn_request"]

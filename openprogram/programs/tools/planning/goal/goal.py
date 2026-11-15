"""The ``goal`` Program: the Programs page's form for starting a chat Goal.

The web form is answered by the chat route, which starts the first turn
itself. Agents create Goals with ``create_goal``.
"""
from __future__ import annotations


def goal(prompt: str) -> str:
    """Keep an objective active across this conversation's chat turns."""
    from openprogram.runtime.agent.call_state import current_session_id
    from . import chat
    session_id = current_session_id()
    if not session_id:
        raise ValueError("A Goal needs a conversation")
    created = chat.create(session_id, prompt)
    return f"Goal saved: {created['text']}"


PROGRAM_ENTRIES = [goal]

ENTRY_OPTIONS = {
    "goal": {
        "input": {"prompt": {"multiline": True}},
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The objective to keep working on."},
            },
            "required": ["prompt"],
        },
    },
}

"""session_transcript — read a past session as plain text.

One tool. It renders a session's branch (conversation + the tool /
function calls each turn made) into LLM-readable prose so the model can
reason about work that happened outside its own context — what the
``distill`` skill reads when turning a past session into a reusable
skill or agentic function.

Discovery is already covered: ``list_sessions`` and ``list_branches``
(``agent_collab``) name the session ids and ``SID:HEAD`` branch tips
this tool takes as arguments.
"""
from .session_transcript import session_transcript

__all__ = ["session_transcript"]

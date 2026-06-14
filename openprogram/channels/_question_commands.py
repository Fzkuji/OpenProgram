"""Channel /answer · /decline text commands — answer a function's
runtime.ask / form question from a chat message.

A chat platform has no in-composer question card (that's the web UI). So
when a function pauses on runtime.ask inside a channel session, the
question is pushed to the chat (see _question_bridge) and the user
answers with a text command:

    /answer <qid> <choice…>     pick an option / type free text / a form value
    /decline <qid>              decline the question

Scope is the session, not an extra "owner" field: a question belongs to a
session_key, and `list_pending(session_key)` is what a /answer is allowed
to resolve. Discord already scopes each (channel, user) to its own
session, so users can't answer each other's questions; a DM is one user;
a Telegram/Slack group deliberately shares one session (group-wide bot),
so any member answering is the group semantics, not a leak.

Resolve funnels through `resolve_question_and_broadcast` — the same
claim-once collapse point the web UI uses — so the first answer across
surfaces wins and the others' cards/messages retract.
"""
from __future__ import annotations

from typing import Optional


def try_handle_question_command(
    user_text: str, session_key: str,
) -> Optional[str]:
    """If ``user_text`` is a /answer or /decline command for a question in
    ``session_key``, resolve it and return a receipt string. Otherwise
    return None so the caller routes the message through normal dispatch.

    Returning None on a malformed / unauthorized command (rather than
    swallowing it) means a real message that merely starts with "/answer"
    falls through to the agent instead of vanishing.
    """
    text = (user_text or "").strip()
    low = text.lower()
    if not (low.startswith("/answer") or low.startswith("/decline")):
        return None

    from openprogram.agent.questions import (
        get_question_registry, resolve_question_and_broadcast,
    )

    parts = text.split(maxsplit=2)
    verb = parts[0].lower()
    qid = parts[1] if len(parts) > 1 else ""
    if not qid:
        return None  # "/answer" with no id — let it fall through

    # Authorization = the question must belong to THIS session.
    pend = {q.id: q for q in get_question_registry().list_pending(session_key)}
    q = pend.get(qid)
    if q is None:
        # Not a pending question of this session: already answered, expired,
        # wrong id, or just a coincidental message. Don't swallow it.
        return None

    if verb == "/decline":
        ok = resolve_question_and_broadcast(qid, "declined", None)
        return "✓ 已拒绝该问题。" if ok else "该问题已失效。"

    # /answer — map the raw choice text to the question's answer shape.
    raw = parts[2].strip() if len(parts) > 2 else ""
    if not raw:
        return "请在 /answer 后给出你的回答，例如 /answer " + qid + " 1"

    value = _map_choice(q, raw)
    ok = resolve_question_and_broadcast(qid, "answered", value)
    return "✓ 已记录你的回答。" if ok else "该问题已失效（可能已被回答）。"


def _map_choice(q, raw: str):
    """Turn the user's raw text into the answer value the question expects.

    - options + a 1-based index in range → that option's text
    - multi → comma-separated → list[str] (each item index-mapped too)
    - otherwise → the raw text verbatim (free input)
    """
    options = list(getattr(q, "options", None) or [])

    def one(token: str):
        token = token.strip()
        if token.isdigit() and options:
            idx = int(token) - 1  # 1-based for humans
            if 0 <= idx < len(options):
                return options[idx]
        return token

    if getattr(q, "multi", False):
        return [one(t) for t in raw.split(",") if t.strip()]
    return one(raw)

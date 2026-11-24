"""Where a retry or an edit starts its new branch, decided without the server.

Retry and edit (the web's buttons, ``--print`` resuming "from message X")
both start from the nearest user message at or above a node: a retry
resends it, an edit sends new text, each as a sibling under the same
parent, so the old turn stays reachable as a branch of its own. A retry of
a turn that never got a reply reruns that same message instead, since
there is no branch to keep. A retry of a turn whose reply is only an error
(the model was refused, the request timed out, nothing was written or run)
takes that turn's place: the caller removes the failed turn once the new
one is written, so trying again after an error does not leave a branch per
attempt. The caller holds the branch's slot, admits the
turn and writes the message; this module only decides what to write.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional


class ForkError(ValueError):
    """The fork cannot start; ``status`` is the HTTP status that says why."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


@dataclass
class ForkPlan:
    #: The user message the new turn sends (the old one, rerun, when ``reuse``).
    message: dict[str, Any]
    #: The user message it is a sibling of (or reruns).
    source: dict[str, Any]
    #: A new branch is made (an edit, or a retry of a turn that has a reply).
    new_branch: bool
    #: The new turn takes the place of ``source``, whose reply is only an
    #: error: the caller removes that turn once the new one is written.
    replaces: bool = False

    @property
    def msg_id(self) -> str:
        return str(self.message.get("id"))


def _only_an_error(messages: list[dict[str, Any]], source: dict[str, Any]) -> bool:
    """``source``'s turn ended in an error and left nothing: one reply, its
    text the error itself, with no step, tool call or message under it."""
    under = [m for m in messages if m.get("predecessor") == source.get("id")]
    if len(under) != 1:
        return False
    reply = under[0]
    if reply.get("role") != "assistant" or reply.get("status") != "error":
        return False
    if not str(reply.get("content") or "").lstrip().startswith("[error]"):
        return False
    return not any(m.get("predecessor") == reply.get("id") or m.get("caller") == reply.get("id")
                   for m in messages)


def plan_fork(messages: list[dict[str, Any]], pivot_id: str, new_content: Optional[str],
              *, authority: Optional[dict[str, Any]] = None) -> ForkPlan:
    """The message a retry (``new_content`` ``None``) or an edit of
    ``pivot_id`` sends. Raises ``ForkError`` for an unknown node or one
    with no user message above it."""
    by_id = {m.get("id"): m for m in messages}
    pivot = by_id.get(pivot_id)
    if pivot is None:
        raise ForkError("unknown msg", 404)
    cur = pivot
    while cur is not None and cur.get("role") != "user":
        cur = by_id.get(cur.get("predecessor"))
    if cur is None:
        raise ForkError("no user message to fork from", 400)
    source = cur
    has_reply = any(m.get("predecessor") == source.get("id") and m.get("role") == "assistant"
                    for m in messages)
    if new_content is None and not has_reply:
        return ForkPlan(message=source, source=source, new_branch=False)
    message: dict[str, Any] = {
        "role": "user",
        "id": uuid.uuid4().hex,
        "content": new_content if new_content is not None else source.get("content", ""),
        "timestamp": time.time(),
        # Sibling of the source: same parent. A first-turn retry forks at
        # ROOT explicitly; the store refuses a ROOT-level node without one.
        "predecessor": source.get("predecessor") or "ROOT",
        "forked_from": source.get("id"),
    }
    origin = "person" if new_content is not None else source.get("origin")
    if origin:
        message["origin"] = origin
    message.update(authority or {})
    if source.get("display"):
        message["display"] = source["display"]
    if new_content is None and source.get("model_input"):
        message["model_input"] = source["model_input"]
    if new_content is None and source.get("media"):
        # A retry sends the same pictures; the copy names the same kept files.
        message["media"] = source["media"]
    if new_content is not None:
        message["edit_of"] = source.get("id")
    return ForkPlan(message=message, source=source, new_branch=True,
                    replaces=new_content is None and _only_an_error(messages, source))

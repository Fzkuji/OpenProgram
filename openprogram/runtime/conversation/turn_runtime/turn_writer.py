"""TurnWriter — the ONE mover of a chat turn's session head.

HEAD single-writer (context/compaction.md §5): every store write a
turn performs on the conversation chain — the user node, the assistant
placeholder, the failed-turn head, the finalize bookkeeping — goes
through this object, and it alone applies the turn's head policy
(``TurnRequest.advance_head``). A same-session spawned sub-agent turn
runs with ``advance_head=False`` and therefore CANNOT move the head,
whichever write path it takes.

The invariant is structural, not disciplinary: inside the dispatcher
package, ``set_head`` / ``update_session(head_id=...)`` appear only in
this file (plus ``forced_tool.py``'s manual function-run path, which is
a user-initiated move by definition). The four unguarded movers that
used to hide in the six-step turn pipeline — spawn-branch
registration, the placeholder insert, the finalize bookkeeping and the
error path — each stole the head from under a running spawn before
they were pulled in here.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

_log = logging.getLogger("openprogram.dispatcher")


def mark_session_finished(db, session_id: str, *, failed: bool = False,
                          quiet: bool = False, own_msg_id: str | None = None,
                          **fields: Any) -> None:
    """Record that a turn ended: ``done`` or ``failed``, unread unless on screen.

    The sidebar shows an unread dot (red for a failure) for a conversation
    whose turn ended while nobody was looking at it. Every client gets the
    one-row update so the dot appears live. A ``quiet`` turn that succeeded
    (``TurnRequest.quiet_finish``) leaves the dot as it is.

    ``own_msg_id`` names the turn that ended. While another turn of the
    conversation still runs (one on another branch, or one queued behind
    this one), a turn that succeeded leaves the status ``running``: the
    conversation is done when its last turn is.
    """
    from openprogram.runtime import interface_link

    status = "failed" if failed else "done"
    if not failed and own_msg_id and _another_turn_runs(session_id, own_msg_id):
        status = "running"
    data: dict[str, Any] = {"id": session_id, "status": status}
    if failed or not quiet:
        data["unread"] = not interface_link.session_on_screen(session_id)
        fields["unread"] = data["unread"]
    db.update_session(session_id, status=status, **fields)
    try:
        interface_link.broadcast({"type": "session_updated", "data": data})
    except Exception:
        _log.warning("failed to broadcast finished status for %s", session_id, exc_info=True)


def _another_turn_runs(session_id: str, own_msg_id: str) -> bool:
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.projections.foreground import another_turn_runs
        return another_turn_runs(default_store(), session_id, own_msg_id=own_msg_id)
    except Exception:
        _log.debug("running turns not read for %s", session_id, exc_info=True)
        return False


def mark_session_unread(db, session_id: str) -> bool:
    """Give the conversation the sidebar's unread dot now, mid-turn, unless
    it is on screen; its status is left alone. True when it was marked."""
    from openprogram.runtime import interface_link

    if interface_link.session_on_screen(session_id):
        return False
    db.update_session(session_id, unread=True)
    try:
        interface_link.broadcast({"type": "session_updated", "data": {"id": session_id, "unread": True}})
    except Exception:
        _log.warning("failed to broadcast unread for %s", session_id, exc_info=True)
    return True

_ROOT_ID = "ROOT"


def beside_conversation(req: Any) -> bool:
    """A turn that leaves the conversation's own state alone: one admitted
    without the conversation's effects (``owns_conversation_effects``), a
    background check-in or a person's turn on a branch that is not the
    selected one. Neither sets the conversation's status, unread dot, head,
    model, token count or title, or replies to its channel."""
    from openprogram.runtime.agent.turn_request import owns_conversation_effects
    return not owns_conversation_effects(req)


class TurnWriter:
    """Owns the head policy for one turn. Construct once per turn.

    A turn writes to the branch it was admitted on (``req.branch_id``),
    whichever branch the person selects while it runs. Each chain write
    names its predecessor, and the store moves the branch that ends there:
    the session head with it while that branch is selected, only the
    branch's record while it is not. The head is never moved onto the
    turn's branch from another one, so a turn that ends after the person
    switched away leaves the selection where the person put it. A turn
    that continues its branch never makes a branch: a new one comes only
    from a message written under an earlier point (a retry, an edit, a
    fork), which names that point (``branch_from``).
    """

    def __init__(self, db: Any, req: Any):
        self.db = db
        self.req = req
        self.advance = bool(getattr(req, "advance_head", True))
        # The message this turn wrote and the tip it went under, until the
        # turn's own unselected branch has been moved onto it.
        self._prompt_under: Optional[tuple[str, Optional[str]]] = None

    def _moves_head(self) -> bool:
        """Whether the next chain write moves the head: on an advancing
        turn unless it continues a branch that is not the selected one; on a
        turn beside the selection only once the person has selected its
        branch, so its writes then show where they look."""
        if self.advance:
            return not self._continues_unselected_branch()
        branch = getattr(self.req, "branch_id", None)
        if not branch or not getattr(self.req, "side_branch", False):
            return False
        row = self.db.get_session(self.req.session_id) or {}
        return row.get("active_branch_id") == branch

    def _continues_unselected_branch(self) -> bool:
        """Whether the turn adds to its own recorded branch while another
        one is selected (a Goal continuation or a background-command notice
        on the Goal's branch, a turn the person switched away from). Its
        messages go under that branch's tip (``prep._own_branch_tip``) and
        carry that branch's record; written as a head move they would each
        open a new branch beside the selected one."""
        from openprogram.runtime.agent.turn_request import _InheritParent
        branch = getattr(self.req, "branch_id", None)
        if not branch or not isinstance(getattr(self.req, "branch_from", None), _InheritParent):
            return False
        row = self.db.get_session(self.req.session_id) or {}
        if row.get("active_branch_id") in (None, branch):
            return False
        ref = (row.get("branch_refs") or {}).get(branch)
        return isinstance(ref, dict) and ref.get("status") != "aborted"

    # ── user node ────────────────────────────────────────────────

    def persist_user(
        self,
        user_msg_id: str,
        user_msg: dict,
        user_caller_id: Optional[str],
    ) -> None:
        """Write the turn's user node (dag/overview.md step 5).

        Three shapes, one head policy:
          * spawn branch root — ``SessionStore.spawn_branch`` with
            ``register_head`` following the policy;
          * ordinary chained / forked turn — a Call through the shim,
            whose ``advance_head`` follows the policy;
          * legacy fallback — ``append_message`` + an explicit
            ``set_head`` only when the policy allows.
        """
        from openprogram.state.store.session.nodes import Call, ROLE_USER
        from openprogram.state.store import SessionNodeWriter

        req = self.req
        try:
            user_meta = {
                k: v for k, v in user_msg.items()
                if k not in {"id", "role", "content", "timestamp", "extra",
                             "predecessor", "caller"}
                and v is not None
            }
            raw_extra = user_msg.get("extra")
            if raw_extra:
                try:
                    decoded = json.loads(raw_extra) if isinstance(
                        raw_extra, str) else raw_extra
                    user_meta.update(decoded)
                except (json.JSONDecodeError, TypeError):
                    user_meta["extra"] = raw_extra
            if req.branch_from is None and req.spawn_caller:
                # Spawn branch root — created by the store primitive
                # (dag/overview.md): predecessor=None, caller = the
                # spawning node. Never hand-assembled here.
                self.db.spawn_branch(
                    req.session_id,
                    req.spawn_caller,
                    source=req.source,
                    node_id=user_msg_id,
                    prompt=req.user_text,
                    created_at=user_msg.get("timestamp"),
                    metadata=user_meta,
                    register_head=self._moves_head(),
                )
            else:
                node = Call(
                    id=user_msg_id,
                    created_at=user_msg.get("timestamp") or time.time(),
                    role=ROLE_USER,
                    output=req.user_text,
                    # A cross-session exact-node fork keeps its target-side
                    # predecessor and records the source-side spawning node as
                    # caller.  Same-session inherit/retry turns leave
                    # spawn_caller unset and retain the historical ROOT edge.
                    caller=req.spawn_caller or _ROOT_ID,
                    # Explicit root-level fork (branch_from=None) and
                    # the session's first turn both anchor at ROOT
                    # explicitly — same convention as Agent method
                    # root-level runs.
                    predecessor=user_caller_id or _ROOT_ID,
                    metadata=user_meta,
                )
                SessionNodeWriter(
                    self.db, req.session_id, advance_head=self._moves_head(),
                ).append(node)
        except Exception:
            self.db.append_message(req.session_id, user_msg)
            if self._moves_head():
                self.db.set_head(req.session_id, user_msg_id)
        self._prompt_under = (user_msg_id, user_caller_id)
        self._carry_own_branch()

    def _carry_own_branch(self) -> None:
        """Move the turn's own unselected branch onto the message this
        turn wrote under its tip, once. A message does not move a branch
        by itself; with the record on it, the reply carries the record on
        (``carry_branch``) and the next turn continues from the reply.
        Asked again before the reply is written, for a turn whose branch
        stopped being the selected one in between."""
        if self._prompt_under is None or not self._continues_unselected_branch():
            return
        (user_msg_id, tip), self._prompt_under = self._prompt_under, None
        branch = self.req.branch_id
        try:
            moved = self.db.point_branch(self.req.session_id, branch,
                                         expected_head=tip, head=user_msg_id)
        except Exception:
            moved = False
            _log.warning("branch %s not moved onto %s", branch, user_msg_id, exc_info=True)
        if not moved:
            _log.warning("branch %s of session %s had moved before message %s was written",
                         branch, self.req.session_id, user_msg_id)

    # ── assistant placeholder ────────────────────────────────────

    def open_placeholder(
        self, assistant_msg_id: str, user_msg_id: str,
    ) -> bool:
        """Insert the running assistant placeholder; head follows the
        policy (the shim inside handles it — no second ``set_head``)."""
        from openprogram.runtime.conversation.turn_admission.turn_lifecycle import (
            insert_placeholder,
        )
        from openprogram.state.authority import runtime_authority
        self._carry_own_branch()
        result = insert_placeholder(
            self.db, self.req.session_id, assistant_msg_id, user_msg_id,
            self.req.source, advance_head=self._moves_head(),
            authority=runtime_authority(
                self.req, f"agent/{self.req.agent_id}"
            ),
        )

        from openprogram.state.memory.policy import stamp
        if result:
            self.db.merge_node_metadata(self.req.session_id, assistant_msg_id, stamp(self.req))
        return result

    # ── terminal bookkeeping ─────────────────────────────────────

    def head_for_finalize(self, assistant_msg_id: str) -> Optional[str]:
        """The head value finalize's ``update_session`` may record —
        the reply on an advancing turn, nothing on a spawned one. It is
        written only while the turn's branch is selected
        (``on_own_branch``)."""
        return assistant_msg_id if self.advance else None

    def on_own_branch(self) -> dict:
        """The guard a write of the conversation's head and its selected
        branch's fields carries: refused (``BranchConfigConflict``) once
        the person has selected another branch than the turn's. The head
        only goes forward: a turn that ends after a later turn was written
        under its reply leaves the head on the later one."""
        branch = getattr(self.req, "branch_id", None)
        return {"head_forward_only": True, **({"expected_branch_id": branch} if branch else {})}

    def record_failure(self, head_for_next: Optional[str]) -> None:
        """Mark the session failed; move head to the failed turn ONLY
        on an advancing turn whose branch is still selected — a spawned
        turn's error stays on its side branch, and a turn the person
        switched away from never pulls the selection back. A background
        turn's failure is recorded by whoever started it, not on the
        conversation."""
        if beside_conversation(self.req):
            return
        from openprogram.state.store.session.branch_config import BranchConfigConflict
        head = head_for_next if self.advance else None
        try:
            try:
                mark_session_finished(
                    self.db, self.req.session_id, failed=True,
                    head_id=head, **(self.on_own_branch() if head else {}),
                )
            except BranchConfigConflict:
                mark_session_finished(self.db, self.req.session_id, failed=True)
        except Exception:
            _log.warning(
                "failed to record error status for session %s",
                self.req.session_id, exc_info=True,
            )

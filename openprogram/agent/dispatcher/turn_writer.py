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

_ROOT_ID = "ROOT"


class TurnWriter:
    """Owns the head policy for one turn. Construct once per turn."""

    def __init__(self, db: Any, req: Any):
        self.db = db
        self.req = req
        self.advance = bool(getattr(req, "advance_head", True))

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
        from openprogram.context.nodes import Call, ROLE_USER
        from openprogram.store import SessionNodeWriter

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
                    register_head=self.advance,
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
                    # explicitly — same convention as @agentic_function
                    # root-level runs.
                    predecessor=user_caller_id or _ROOT_ID,
                    metadata=user_meta,
                )
                SessionNodeWriter(
                    self.db, req.session_id, advance_head=self.advance,
                ).append(node)
        except Exception:
            self.db.append_message(req.session_id, user_msg)
            if self.advance:
                self.db.set_head(req.session_id, user_msg_id)

    # ── assistant placeholder ────────────────────────────────────

    def open_placeholder(
        self, assistant_msg_id: str, user_msg_id: str,
    ) -> bool:
        """Insert the running assistant placeholder; head follows the
        policy (the shim inside handles it — no second ``set_head``)."""
        from openprogram.agent.internals._turn_lifecycle import (
            insert_placeholder,
        )
        from openprogram.agent.authority import runtime_authority
        return insert_placeholder(
            self.db, self.req.session_id, assistant_msg_id, user_msg_id,
            self.req.source, advance_head=self.advance,
            authority=runtime_authority(
                self.req, f"agent/{self.req.agent_id}"
            ),
        )

    # ── terminal bookkeeping ─────────────────────────────────────

    def head_for_finalize(self, assistant_msg_id: str) -> Optional[str]:
        """The head value finalize's ``update_session`` may record —
        the reply on an advancing turn, nothing on a spawned one."""
        return assistant_msg_id if self.advance else None

    def record_failure(self, head_for_next: Optional[str]) -> None:
        """Mark the session failed; move head to the failed turn ONLY
        on an advancing turn — a spawned turn's error stays on its
        side branch and never steals the user's window."""
        try:
            self.db.update_session(
                self.req.session_id,
                head_id=head_for_next if self.advance else None,
                status="failed",
            )
        except Exception:
            _log.warning(
                "failed to record error status for session %s",
                self.req.session_id, exc_info=True,
            )

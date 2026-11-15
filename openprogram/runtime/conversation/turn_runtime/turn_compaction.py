"""Durable compaction a running turn asks for between two of its requests.

The agent loop calls it (``AgentLoopConfig.compact_turn``) at a step
boundary when the next request would pass the automatic threshold
(``mid_turn``), and after the provider reported a context overflow
(``reactive``). Either way the turn's branch is compacted into a summary
node, the history before this turn is rendered again from the DAG, and this
turn's own messages, held by the loop, follow it unchanged. The loop then
sends the next request, or the overflowed one again; nothing the turn has
already done runs twice (context/compaction.html, "Reactive" and
"Mid-turn").

The summary covers the history before this turn's message: that is the
chain the turn's render reads from, so the summary applies to the next
request at once. The turn's own steps stay raw.
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)


class TurnCompaction:
    """The ``compact_turn`` callback of one conversation turn."""

    def __init__(self, *, engine, req, db, agent_profile, model, tools,
                 system_prompt: str, render_kwargs: dict, turn_tip: str,
                 prefix_len: int, on_event: Callable[[dict], None],
                 cancel_event: Optional[threading.Event]):
        self.engine = engine
        self.req = req
        self.db = db
        self.agent_profile = agent_profile
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.render_kwargs = {k: v for k, v in render_kwargs.items() if k != "head_id"}
        self.turn_tip = turn_tip
        # Where this turn's own messages start in the loop's list: what came
        # before is the rendered history the turn was sent with.
        self.prefix_len = prefix_len
        self.on_event = on_event
        self.cancel_event = cancel_event

    @property
    def anchor_id(self) -> Optional[str]:
        """This turn's message: the requests the turn sends are recorded
        under it, for a compaction to fork (``compaction/fork.py``)."""
        return getattr(self.req, "user_msg_id", None)

    def _pre_turn_head(self) -> Optional[str]:
        """The tip of the branch before this turn's message."""
        user_id = getattr(self.req, "user_msg_id", None)
        chain = self.db.get_branch(self.req.session_id, self.turn_tip) or []
        ids = [row.get("id") for row in chain]
        if user_id in ids:
            index = ids.index(user_id)
            return ids[index - 1] if index > 0 else None
        return None

    def over_threshold(self, messages: list) -> bool:
        """Whether a request with ``messages`` passes the automatic threshold."""
        from openprogram.runtime.context.tokens import real_context_window
        budgets = getattr(self.engine, "budgets", None)
        if budgets is None:
            return False
        budget = budgets.allocate(
            context_window=real_context_window(self.model),
            system_prompt=self.system_prompt or "",
            history=messages,
            tools=list(self.tools or []),
        )
        return bool(self.engine.should_auto_compact(SimpleNamespace(budget_pct=budget.input_used_pct)))

    async def __call__(self, messages: list, trigger: str) -> Optional[list]:
        if trigger == "mid_turn" and not self.over_threshold(messages):
            return None
        head = self._pre_turn_head()
        if not head:
            return None
        try:
            result = await self.engine.compact(
                agent=self.agent_profile,
                session_id=self.req.session_id,
                model=self.model,
                on_event=self.on_event,
                user_initiated=False,
                head_id=head,
                cancel_event=self.cancel_event,
                trigger=trigger,
            )
        except Exception:  # noqa: BLE001 - a failed automatic compaction is silent
            _log.warning("%s compaction failed for session %s", trigger,
                         self.req.session_id, exc_info=True)
            return None
        if not getattr(result, "summary_id", None):
            return None
        from openprogram.runtime.context.persistence import rendered_history
        prep = self.engine.prepare(
            agent=self.agent_profile,
            session=self.db.get_session(self.req.session_id) or {},
            history=rendered_history(self.db, self.req.session_id, self.turn_tip),
            model=self.model,
            tools=self.tools,
            system_prompt=self.system_prompt,
            head_id=self.turn_tip,
            **self.render_kwargs,
        )
        prefix = list(prep.agent_messages)
        own = list(messages[self.prefix_len:])
        self.prefix_len = len(prefix)
        return [*prefix, *own]


def turn_compaction_for(req, **kwargs: Any) -> Optional[TurnCompaction]:
    """The callback for a turn that reads its own branch's whole history.

    A background check-in reads a bounded tail and a clean run no history:
    neither has a history of its own to compact, so their overflow ends the
    turn and the failure path records why.
    """
    if getattr(req, "background", False) or getattr(req, "history_override", None) is not None:
        return None
    if not kwargs.get("turn_tip"):
        return None
    return TurnCompaction(req=req, **kwargs)


__all__ = ["TurnCompaction", "turn_compaction_for"]

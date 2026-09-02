"""archive_agent — remove an agent from the agent list.

Branches live forever in the session DAG (fork / replay /
read_conversation depend on that), so ``list_agents`` would otherwise
accumulate every agent ever spawned. Archiving marks a branch's meta
entry ``archived``: the branch leaves ``list_agents`` (a dedicated
``scope="archived"`` still lists it) and refuses further
``send_message`` / ``agent(to=)`` deliveries. Nothing else changes —
``read_conversation`` still reads it and ``agent(start_from="SID:MSG_ID")``
still forks it. Archiving stops new deliveries, it keeps the history.

There is no unarchive: to reuse an archived agent's memory, fork it.

Any session may archive any branch. Archiving interrupts nothing that
is running and deletes nothing, so it does not cancel an active execution.

Design: docs/reference/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

import time

from openprogram.programs._runtime import function
from openprogram.programs.tools.agents.send_message.shared import (
    _current_session,
    _db,
)


_DESCRIPTION = (
    "Archive an agent whose work is finished: it leaves list_agents "
    "and refuses further send_message / agent(to=) deliveries. Its "
    "history is untouched — read_conversation still reads it, "
    "agent(start_from=\"SID:MSG_ID\") still forks it, and "
    "list_agents(scope=\"archived\") still lists it. There is no "
    "unarchive; to reuse an archived agent's memory, fork it. `to` "
    "accepts \"SID:HEAD\" or a branch name (see list_agents). Any "
    "session may archive any agent. To archive at spawn time instead, "
    "pass agent(archive_when_done=true)."
)


def _archive_agent_impl(to: str, reason: str = "") -> str:
    from openprogram.programs.tools.agents.send_message.send_message.addressing import (
        resolve_existing_target,
    )

    if not (to or "").strip():
        return (
            "[archive_agent error] `to` is required — pass a SID:HEAD "
            "address or a branch name (see list_agents)."
        )
    cur = _current_session()
    status, payload = resolve_existing_target(
        to.strip(), cur or "", allow_archived=True,
    )
    if status != "ok":
        return f"[archive_agent error] {payload}"
    sid, tip = payload  # type: ignore[misc]

    db = _db()
    try:
        meta = db.get_branch_meta(sid, tip)
    except Exception as e:  # noqa: BLE001
        return f"[archive_agent error] {type(e).__name__}: {e}"
    if meta.get("archived"):
        return f"[archive_agent] agent {sid}:{tip} is already archived."

    fields: dict = {"archived": True, "archived_at": time.time()}
    if (reason or "").strip():
        fields["archived_reason"] = reason.strip()
    try:
        db.set_branch_meta(sid, tip, **fields)
    except Exception as e:  # noqa: BLE001
        return f"[archive_agent error] {type(e).__name__}: {e}"

    name = meta.get("name")
    label = f" «{name}»" if name else ""
    return (
        f"[archive_agent] archived {sid}:{tip}{label} — it no longer "
        "appears in list_agents and refuses send_message / agent(to=) "
        "deliveries. Its history stays readable (read_conversation) "
        f"and forkable (agent(start_from=\"{sid}:MSG_ID\"))."
    )


@function(name="archive_agent", description=_DESCRIPTION, toolset=["core"])
def archive_agent(to: str, reason: str = "") -> str:
    """Archive an agent branch: keep its history, revoke its right to
    be disturbed (no more send_message / agent(to=) deliveries)."""
    return _archive_agent_impl(to=to, reason=reason)

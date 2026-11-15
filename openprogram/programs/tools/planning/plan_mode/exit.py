"""exit_plan_mode — submit the drafted plan for user approval.

This tool is always wrapped by the dispatcher's approval gate
(``requires_approval=True``). The approval payload carries the
``plan`` argument so the UI shows the proposed plan to the user. If
the user approves, the tool body runs (clears the plan-mode flag and
returns a success message). If the user rejects, the gate returns a
refusal and the flag stays on — the LLM is expected to read
the rejection, revise, and either re-submit or change direction.

The plan is passed as the ``plan`` argument; OpenProgram has no
plan-file workflow.
"""
from __future__ import annotations

from openprogram.programs._host.runtime import function
from openprogram.runtime.conversation import plan_mode as _pm


_DESCRIPTION = """Send your finished plan to the user for a decision. This call is the approval request, so do not also ask for approval in your reply.

Put the whole plan in the `plan` argument, in markdown, written for the person reviewing it: which files change and how, new functions and where they live, the order of the work, edge cases, and anything they must weigh before agreeing.

If approved, plan mode ends and you carry out the plan. If rejected, plan mode stays on: change the plan to answer the reason given and send it again.

Settle open questions with the user before calling this. It is for proposed code changes; a task that only gathers information has nothing to submit.
"""


def _restore_mode(session_id: str) -> None:
    """Put the selector back on what it said before Plan; the live refresh applies it in this turn."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.permissions.state import permission_state, update_permission
    from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
    from openprogram.state.authority import normalize_authority

    state = permission_state(session_id)
    if state["mode"] != "plan":
        return
    updated = update_permission(session_id, state.get("before_plan") or DEFAULT_PERMISSION, state["version"],
                                normalize_authority(get_turn_request()))
    # Every open window's selector follows, as when the owner clicks it.
    emit_ws_frame({"type": "permission_changed", "data": {
        "session_id": session_id, "action": "set_permission", "mode": updated["mode"], "version": updated["version"],
    }})


@function(
    name="exit_plan_mode",
    description=_DESCRIPTION,
    toolset=["core"],
    requires_approval=True,
)
def exit_plan_mode(plan: str) -> str:
    """Submit a plan for user approval.

    Args:
        plan: The concrete implementation plan in markdown. Reaches
            the user verbatim — write it for a human reviewer, not
            for the LLM.
    """
    session_id = _pm.turn_session_id()
    if not session_id:
        return "[plan-mode] no active session — flag not cleared"
    _pm.exit(session_id)
    _restore_mode(session_id)
    return (
        "Plan approved and plan mode is off. Write tools are available "
        "again. Carry out the plan as approved, and check with the "
        "user before departing from it."
    )

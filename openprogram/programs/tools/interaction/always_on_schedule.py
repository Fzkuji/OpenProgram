"""always_on_schedule — an always-on conversation sets its own next check-in."""
from __future__ import annotations

from openprogram.programs._host.runtime import function


@function(
    name="always_on_schedule",
    description=(
        "Set when this always-on conversation checks in next, when you know when something "
        "you watch is due: in_minutes from now, or at a local date and time "
        "(YYYY-MM-DDTHH:MM). Give a short reason the user can read. Between 5 minutes and "
        "30 days ahead; quiet hours move it later. For something the user asked to happen "
        "on a fixed schedule, use the scheduler tool instead, and never use this to keep a "
        "regular rhythm: without it, check-ins slow down by themselves."
    ),
    toolset=["always_on"],
)
def always_on_schedule(reason: str, in_minutes: int | None = None, at: str | None = None) -> str:
    """Schedule the conversation's next check-in."""
    from datetime import datetime
    from openprogram.runtime.conversation.always_on import schedule_wake
    from openprogram.runtime.execution.run_control import get_current_session_id
    sid = get_current_session_id()
    if not sid:
        return "[always_on_schedule error] no active conversation"
    try:
        row = schedule_wake(sid, in_minutes=in_minutes, at=at, reason=reason)
    except ValueError as exc:
        return f"[always_on_schedule error] {exc}"
    when = datetime.fromtimestamp(row["next_wake_at"]).strftime("%Y-%m-%d %H:%M")
    return f"Next check-in at {when} (local time)."

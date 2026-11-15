"""always_on_report — an always-on conversation tells the user what it found."""
from __future__ import annotations

from openprogram.programs._host.runtime import function


@function(
    name="always_on_report",
    description=(
        "Report to the user from this always-on conversation. The report becomes your message "
        "in this conversation, the user gets a notification, and it may also go to the channel "
        "this conversation is bound to and the channel conversations chosen for this Agent. Use once per check-in, only for something worth "
        "their attention and not already reported. body is the message they will read: one to "
        "three complete sentences saying what you found, why it matters and where it came from, "
        "making sense without the title, with no headings or tables. title is a few words for "
        "lists and notifications only. Put each link in links. kind: 'approval' when the user "
        "must decide something (end body with the one question and your recommendation), 'done' "
        "when a piece of work finished, 'finding' otherwise."
    ),
    toolset=["always_on"],
)
def always_on_report(title: str, body: str = "", links: list[str] | None = None,
                     kind: str = "finding") -> str:
    """Save the report and notify the user."""
    from openprogram.runtime.conversation.always_on import report
    from openprogram.runtime.execution.run_control import get_current_session_id
    sid = get_current_session_id()
    if not sid:
        return "[always_on_report error] no active conversation"
    try:
        row = report(sid, title, body, links, kind)
    except ValueError as exc:
        return f"[always_on_report error] {exc}"
    sending = len(row.get("deliveries") or [])
    notice = ""
    if len(row["body"]) < len((body or "").strip()):
        notice = f" The body was too long and only its first {len(row['body'])} characters were kept."
    return f"Reported: {row['title']}" + (f" (sending to {sending} channel conversation(s))" if sending else "") + notice

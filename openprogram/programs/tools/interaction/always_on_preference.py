"""always_on_preference — remember what the user wants reported, or not."""
from __future__ import annotations

from openprogram.programs._host.runtime import function


@function(
    name="always_on_preference",
    description=(
        "Record a short rule about what to report, when the user says in this conversation "
        "that some kind of report is unwanted or wanted (\"don't tell me about passing "
        "builds\", \"always tell me when a PR needs review\"). Every later check-in of this "
        "Agent follows it. One rule per call, in a sentence."
    ),
    toolset=["always_on"],
)
def always_on_preference(text: str) -> str:
    """Save the rule for this conversation's Agent."""
    from openprogram.runtime.conversation.always_on import WakeRefused, add_preference
    from openprogram.runtime.execution.run_control import get_current_session_id
    sid = get_current_session_id()
    if not sid:
        return "[always_on_preference error] no active conversation"
    try:
        row = add_preference(sid, text)
    except (ValueError, WakeRefused) as exc:
        return f"[always_on_preference error] {exc}"
    return f"Noted: {row['text']}"

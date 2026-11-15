"""Always-on: an Agent configuration that makes its conversations wake.

Every conversation that uses an always-on Agent wakes on the Agent's
schedule or when it scheduled itself, works on the standing goal as an
ordinary chat Goal there, does silent read-only check-ins on a side branch
while it has no unfinished Goal, and reports with ``always_on_report``: a
message from the Agent in the conversation, a notification, and the
channel conversations chosen for the Agent or bound to the chat. Quiet hours and wake days hold scheduled wakes back,
and ``send_message`` gives work to another conversation of the owner.

Design: docs/reference/design/runtime/always-on-agents.html
"""
from openprogram.runtime.conversation.always_on.delivery import channel_targets
from openprogram.runtime.conversation.always_on.runtime import (
    WakeRefused,
    add_preference,
    agent_for_session,
    join,
    remove_preference,
    report,
    schedule_wake,
    status,
    wake,
)

__all__ = ["WakeRefused", "add_preference", "agent_for_session",
           "channel_targets", "join", "remove_preference", "report", "schedule_wake",
           "status", "wake"]

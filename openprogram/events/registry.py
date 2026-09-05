"""Central event registry — the admission boundary of the event layer.

``EVENTS`` lists every event type the bus recognizes. An event type enters
this registry only when a real consumer subscribes to it (the same
principle ``bridges.py`` applies to type-B sources — a moment becomes an
event because someone wants to respond to it, never because the code
happens to pass through it). Emitting an unregistered type is tolerated
during migration — the bus logs one warning per type instead of raising.

Two kinds of dispatch:

* ``notify`` — asynchronous observation via ``EventBus.emit``; subscribers
  can never block or influence the emitter.
* ``gate`` — synchronous veto via ``EventBus.emit_gate``; subscribers run
  in the emitter's thread and any deny reason stops the action.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventSpec:
    kind: str         # "notify" | "gate"
    payload_doc: str  # what the payload dict carries


EVENTS: dict[str, EventSpec] = {
    "tool.before": EventSpec(
        kind="gate",
        payload_doc="{tool: str, tool_call_id: str, args: dict} — the tool "
                    "call about to run; a deny reason becomes the model's "
                    "error tool result",
    ),
    "tool.after": EventSpec(
        kind="notify",
        payload_doc="{tool: str, tool_call_id: str, is_error: bool, "
                    "result_text: str} — a tool call finished; result_text "
                    "carries only the text channel of the result",
    ),
    "turn.stop": EventSpec(
        kind="gate",
        payload_doc="{session_id, user_msg_id, assistant_msg_id, "
                    "last_text: str (≤4000 chars), stop_hook_active: bool} — "
                    "asked after a completed top-level chat turn; Goal "
                    "Workflow rounds use their own completion judge instead; "
                    "a deny reason launches one more continuation turn",
    ),
    "turn.start": EventSpec(
        kind="notify",
        payload_doc="{session_id, user_msg_id, assistant_msg_id} — the user "
                    "message is persisted and the agent loop is about to run",
    ),
    "turn.end": EventSpec(
        kind="notify",
        payload_doc="{session_id, user_msg_id, assistant_msg_id, usage: "
                    "dict} — finalize (persistence + bookkeeping) completed",
    ),
    "session.start": EventSpec(
        kind="notify",
        payload_doc="{session_id, agent_id, channel} — a session object was "
                    "created (emitted by webui session creation)",
    ),
    "chat.before_send": EventSpec(
        kind="notify",
        payload_doc="{session_id, msg_id, text, agent_id, attachments: bool} "
                    "— a user chat message was persisted and is about to "
                    "enter the runtime",
    ),
    "plugin.enable": EventSpec(
        kind="notify",
        payload_doc="{plugin: str} — the plugin loaded, registered its "
                    "contributions and its hook subscriptions",
    ),
    "plugin.disable": EventSpec(
        kind="notify",
        payload_doc="{plugin: str} — the plugin is about to be unloaded; "
                    "its subscriptions are still live when this fires",
    ),
    "goal.update": EventSpec(
        kind="notify",
        payload_doc="{session_id, goal: {text, status, turns_used, "
                    "max_turns, last_reason, last_question}} — the session "
                    "goal state changed",
    ),
    "user.prompt_submitted": EventSpec(
        kind="notify",
        payload_doc="{msg_id, chars} — the user's message row is persisted; "
                    "resets the proactive engine's per-turn signals",
    ),
    "model.response_started": EventSpec(
        kind="notify",
        payload_doc="{} — the provider stream opened for one model response "
                    "within the agent loop",
    ),
    "model.response_completed": EventSpec(
        kind="notify",
        payload_doc="{is_error: bool} — one model response finished; fires "
                    "per loop iteration, not per dispatcher turn",
    ),
    "file.changed": EventSpec(
        kind="notify",
        payload_doc="{path, op: 'write'|'edit'|'patch'} — a file tool "
                    "modified the working tree",
    ),
    "question.asked": EventSpec(
        kind="notify",
        payload_doc="{session_id, question, ...} — the agent asked the user "
                    "a question through the question channel",
    ),
    "mcp.request.cancelled": EventSpec(
        kind="notify",
        payload_doc="{request_id, session_id, client_id, reason} — an MCP-owned "
                    "prompt request was cancelled and cleaned up",
    ),
    "context.compacted": EventSpec(
        kind="notify",
        payload_doc="{ok, tokens_before, tokens_after, ...} — a compaction "
                    "run finished",
    ),
    "context.compaction_recommended": EventSpec(
        kind="notify",
        payload_doc="{budget_pct} — the context budget crossed the "
                    "auto-compact threshold before a turn",
    ),
    "memory.ingest_started": EventSpec(
        kind="notify",
        payload_doc="{messages: int} — the session memory watcher began "
                    "ingesting a batch",
    ),
    "memory.ingest_ended": EventSpec(
        kind="notify",
        payload_doc="{ok: bool, retryable: bool, reason: str} — the session "
                    "memory ingest finished; ok=false carries why it did not, "
                    "and retryable=false is the failure a later poll will not "
                    "fix, so the watcher stops offering that session",
    ),
    "channel.message_inbound": EventSpec(
        kind="notify",
        payload_doc="{channel, peer_kind, chars} — a message arrived from an "
                    "external channel (Discord etc.)",
    ),
    "branches.listed": EventSpec(
        kind="notify",
        payload_doc="{session, count} — an agent enumerated a session's "
                    "branches via the collab tools",
    ),
    "branch.message_sent": EventSpec(
        kind="notify",
        payload_doc="{from, to} — one agent dispatched a message to another "
                    "session branch",
    ),
    "agents.listed": EventSpec(
        kind="notify",
        payload_doc="{sessions, branches} — an agent enumerated available "
                    "collaboration targets",
    ),
    "subagent.started": EventSpec(
        kind="notify",
        payload_doc="{job_id, label} — a background agent job entered its "
                    "running state",
    ),
    "subagent.ended": EventSpec(
        kind="notify",
        payload_doc="{job_id, status, error} — a background agent job "
                    "reached a terminal state",
    ),
    "sessions.listed": EventSpec(
        kind="notify",
        payload_doc="{count} — an agent enumerated sessions via the collab "
                    "tools",
    ),
    "skills.changed": EventSpec(
        kind="notify",
        payload_doc="{} — the skills watcher detected an install/update on "
                    "disk",
    ),
    "plugins.update_available": EventSpec(
        kind="notify",
        payload_doc="{plugin, current, latest} — the plugin update checker "
                    "found a newer version",
    ),
}

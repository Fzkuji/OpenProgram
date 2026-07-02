"""Turn-dispatch type definitions — aliases, the parent sentinel, the
TurnRequest / TurnResult dataclasses, and the no-op ``EventCallback``
default (``_noop``).

Extracted from dispatcher/__init__.py (dispatcher-split step 1). These
depend only on the stdlib, so they live in a leaf module
that everything else (and external callers) can import without pulling in
the heavy agent-loop / provider chain. ``__init__`` re-exports every name
here, so ``dispatcher.TurnRequest`` and
``from openprogram.agent.dispatcher import TurnRequest`` resolve unchanged.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

PermissionMode = Literal["ask", "acceptEdits", "plan", "auto", "bypass"]
EventCallback = Callable[[dict], None]


def _noop(_: dict) -> None:
    """The canonical no-op ``EventCallback``. Used as the default event
    sink across the dispatcher package (``on_event = on_event or _noop``)
    so every entry point can be called without a callback. Lives here
    next to ``EventCallback`` so titles/forced_tool/__init__ share one
    definition instead of three copies."""
    pass


# Sentinel: "caller did not specify predecessor, dispatcher should pick"
# vs explicit ``None`` which means "fork from root". The two cases need
# different behavior — see TurnRequest.caller.
class _InheritParent:
    __slots__ = ()
    def __repr__(self) -> str: return "<INHERIT>"


INHERIT_PARENT: Any = _InheritParent()


@dataclass
class TurnRequest:
    session_id: str
    user_text: str
    agent_id: str
    source: str                                  # "tui" / "web" / "wechat" / ...
    peer_display: Optional[str] = None
    peer_id: Optional[str] = None
    model_override: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: PermissionMode = "ask"
    # Optional explicit tool whitelist that overrides the agent's
    # configured tools. Channels can opt out of risky tools per turn
    # (e.g. wechat shouldn't ever hit destructive bash).
    tools_override: Optional[list[str]] = None
    # Branching: predecessor of the user message we're about to write.
    #   - INHERIT_PARENT (default) → dispatcher uses the active
    #     branch's tail (head_id walk). Normal append.
    #   - explicit string → fork sibling branch off that message.
    #     Retry / edit flows pass the parent of the message being
    #     replaced.
    #   - explicit None → root-level fork (the very first turn of a
    #     new conversation tree, or "retry the very first user
    #     message" case from contextgit/dag.py).
    # Mirrors Claude Code's parentUuid chain: append-only, no mutation
    # of historical messages.
    branch_from: Any = INHERIT_PARENT
    # When the caller has already linearized "the branch the user
    # currently sees" (e.g. webui has its in-memory active-branch
    # walk), pass it here so the dispatcher uses it as the LLM
    # context instead of re-querying SessionDB. Each entry is a row-
    # shaped dict with role/content/timestamp/id at minimum. Passing
    # None means "load history from SessionDB via get_branch".
    history_override: Optional[list[dict]] = None
    # Caller-supplied id for the user message. When omitted dispatcher
    # mints one. Useful for webui where the WS handler pre-emits a
    # ``chat_ack`` envelope tied to a frontend-known msg_id.
    user_msg_id: Optional[str] = None
    # When True, the caller has already persisted the user message
    # under ``user_msg_id`` and advanced head — dispatcher should
    # NOT re-write it. Used by webui where the WS handler appends
    # the user msg before kicking off the agent thread.
    user_already_persisted: bool = False
    # Per-turn speed / priority tier ("priority" = Fast, "flex" =
    # cheaper-slower, None = provider default). Set by the composer's
    # speed pill; flows to ``SimpleStreamOptions.service_tier`` and on
    # to the provider request body. Falls back to the session's stored
    # value when the caller doesn't pin one for this turn.
    service_tier: Optional[str] = None
    # Multimodal attachments to include in the user message. Each
    # entry is ``{"type": "image", "data": <base64>, "media_type":
    # "image/png"}`` (or jpeg/webp/gif). The dispatcher attaches
    # these as ImageContent blocks alongside the text TextContent.
    # Providers that don't support vision will reject; the dispatcher
    # surfaces that as an error envelope, not a crash.
    attachments: Optional[list[dict]] = None
    # Spawn caller: when this turn STARTS a new branch that was spawned by
    # another node (message_branch target="new"), this is the id of the
    # spawning node. The dispatcher sets the new branch-root's ``caller`` to
    # it (instead of ROOT), so the branch is an explicit spawn — otherwise a
    # ROOT-parented branch root with no predecessor gets seq-stitched into a
    # sibling branch and the chat view flattens all branches into one.
    # See docs/design/runtime/dag/session-dag.md §2.3.
    spawn_caller: Optional[str] = None
    # 用户配的权限规则（allow/deny/ask，各来源合并后），供 _gated_execute 的
    # _match_rule 判定。见 docs/design/runtime/permission-model.md §3.4。
    # 类型是 session_config.PermissionRules，这里用 Any 避免循环 import。
    permission_rules: Any = None
    # 路径安全的额外工作目录集（acceptEdits / safetyCheck 用，§3.5）。
    additional_working_dirs: list = field(default_factory=list)


@dataclass
class TurnResult:
    final_text: str
    user_msg_id: str
    assistant_msg_id: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0
    failed: bool = False
    error: Optional[str] = None
    # Structured error taxonomy for a failed turn, so the webui can show an
    # actionable error (retryable rate-limit vs fatal auth/context) instead of a
    # string. See docs/design/providers/error-taxonomy-propagation.md.
    error_reason: Optional[str] = None
    error_retryable: Optional[bool] = None
    error_retry_after_s: Optional[float] = None
    # Per-turn ordered LLM blocks (thinking/text/tool, in emission
    # order). Mirrors what's persisted to ``extra.blocks`` so the
    # webui result-envelope path and the after-refresh DB-rebuilt
    # path render identically.
    blocks: list[dict] = field(default_factory=list)

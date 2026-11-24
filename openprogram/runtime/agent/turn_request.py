"""The request one persistent turn runs under, and the request in force now.

``TurnRequest`` carries what a turn needs and what may not be downgraded
below it: the session and Agent, the source, the permission mode and rules,
and the speaker's authority. The conversation engine builds one per turn and
binds it with :func:`set_turn_request`; the call core reads it back, binds a
standalone one around a plain library call (``runtime_scope``) and derives
the request an inner ``AgentSession``'s tools are gated by
(:func:`inner_turn_request`). Nothing bound means no turn is running.

The types depend only on the standard library, so every layer can import
them; ``openprogram.runtime.conversation.turn_runtime`` re-exports them.
"""
from __future__ import annotations

import contextvars
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

PermissionMode = Literal["ask", "acceptEdits", "plan", "auto", "bypass"]


def _default_permission() -> PermissionMode:
    # Read when a request is built: importing the permission package here
    # would break this module's standard-library-only promise.
    from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
    return DEFAULT_PERMISSION


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
    permission_mode: PermissionMode = field(default_factory=_default_permission)
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
    #     message" case from context/git/dag.py).
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
    # another node (send_message to="new"), this is the id of the
    # spawning node. The dispatcher sets the new branch-root's ``caller`` to
    # it (instead of ROOT), so the branch is an explicit spawn — otherwise a
    # ROOT-parented branch root with no predecessor gets seq-stitched into a
    # sibling branch and the chat view flattens all branches into one.
    # See docs/design/runtime/dag/overview.md §2.3.
    spawn_caller: Optional[str] = None
    # 用户配的权限规则（allow/deny/ask，各来源合并后），供 _gated_execute 的
    # match_rule 判定。见 docs/reference/design/runtime/security/permission-decision.html。
    # 类型是 session_config.PermissionRules，这里用 Any 避免循环 import。
    permission_rules: Any = None
    # HEAD single-writer (context/compaction.md §5): a same-session
    # SPAWNED turn (task / send_message sub-agent) is machinery, not
    # the conversation the user is on — it must never move the session
    # head. With head stolen mid-run, every head-following surface (the
    # transcript mirror, branches_list broadcasts) switched to the
    # agent's window until the outer turn's reply moved it back.
    advance_head: bool = True
    # 路径安全的额外工作目录集（acceptEdits / safetyCheck 用，§3.5）。
    additional_working_dirs: list = field(default_factory=list)
    # Runtime-owned provenance and authorization. Appended after all existing
    # fields so external positional constructors retain their historical
    # meaning. Production entry points construct these explicitly; internal
    # reconstruction that loses them may render/reply but cannot perform a
    # host side effect.
    speaker_kind: Optional[str] = None
    speaker_id: Optional[str] = None
    speaker_display: Optional[str] = None
    principal_id: Optional[str] = None
    authority_tier: Optional[Literal["owner", "paired"]] = None
    interaction: Optional[str] = None
    response_format: Any = None
    # ``agent_delivery`` for a message another agent delivered to this branch.
    message_origin: Optional[str] = None
    # The text of the turn's last model round, without what earlier rounds said.
    last_round_text: str = ""
    structured_output: Any = None
    structured_output_mode: Optional[str] = None
    structured_output_attempt: Optional[int] = None
    # Default DAG slice for agentic functions invoked during this turn.
    # Kept at the end for positional-constructor compatibility. An explicit
    # decorator value takes precedence.
    render_range: Optional[dict[str, int]] = None
    # Validated, turn-scoped descriptor and DOM/ARIA preview for a visible
    # OpenProgram desktop surface. Raw renderer refs never reach providers.
    surface_context: Optional[dict[str, Any]] = None
    # Session that owns ``spawn_caller`` when it is outside this turn's
    # session.  Appended for positional-constructor compatibility.  The node
    # id remains the real caller edge; this field supplies its namespace for
    # persisted provenance and cross-session rendering.
    spawned_from_session: Optional[str] = None
    profile_snapshot: Optional[dict[str, Any]] = None
    goal_context: Optional[dict] = None
    goal_trigger: bool = False
    goal_previous_execution: Optional[str] = None
    goal_verification: Optional[str] = None

    memory_policy_override: Optional[dict[str, Any]] = None
    memory_policy_snapshot: Optional[dict[str, Any]] = None
    memory_degraded_reason: Optional[str] = None
    # A job_followup's per-job summary (agent/job/notices.py
    # ``notice_items``), stamped on its user message so the chat draws one
    # row per finished agent instead of the notice text.
    notice: Optional[list[dict[str, Any]]] = None
    # The turn that continues a run a worker restart interrupted. Its
    # prompt is an instruction to the model, not something the owner said,
    # so the chat shows the continued reply without a user message.
    restart_resume: bool = False
    # On such a turn of a handoff chain: the first message of the line of turns
    # it continues, which is the one the chain saved.
    resumes_message_id: Optional[str] = None
    # Why a turn nobody typed started; ``"handoff"`` marks a turn of a handoff
    # chain, ``"reminder"`` a reminder's wake, ``"automatic_goal"`` what an
    # automatic Goal's turn started for itself. Written on its user message.
    # Kept in the admitted input, so its continuations stay marked.
    automation: Optional[str] = None
    # The turn's end leaves the unread dot alone (a failure still sets
    # it): whatever the owner should see, the turn marks unread itself.
    quiet_finish: bool = False
    # A turn that runs beside the conversation instead of in it: an
    # always-on check-in on a side branch (``advance_head=False``). It never
    # changes the conversation's status, unread dot, model or title, and its
    # reply is never delivered to a channel the chat is bound to. It never
    # compacts the conversation, on the normal or the overflow path, and it
    # reads only the summary and a bounded tail (context/session_dag/tail.py).
    background: bool = False
    # Told to the model with this turn's message and not shown as part of it:
    # a scheduled run's last result and how to say there is nothing new.
    run_note: Optional[str] = None
    # Told to the model with this turn's message and not shown as part of it:
    # what the application the person typed in says the message is about
    # (the research task open in Auto Research, its deliverable and its
    # failing checks). Sent by the composer an application window shows.
    application_note: Optional[str] = None
    # The scheduled task whose run this turn is (``id`` and ``title``), kept in
    # the admitted input: a turn resumed after a restart still ends as that run.
    scheduled_task: Optional[dict[str, Any]] = None
    # "direct", or the kind of group room a channel turn came from.
    peer_kind: Optional[str] = None
    # What memory recall looks for when the turn's prompt is not what the turn
    # is about, as with a check-in's fixed instructions. None: the prompt
    # itself. Empty: nothing to look for, so no recall.
    recall_query: Optional[str] = None
    # Started by a turn no window is attached to, so a sub-agent or a Program
    # keeps off the page in front as that turn does (detached_from_window).
    detached: bool = False

    # Stable settings owner; independent of the page-selected branch and HEAD.
    branch_id: Optional[str] = None
    # None is a frozen "inherit project/global" choice when the flag is set.
    # Old requests have no frozen choice and resolve their own branch on bind.
    sandbox_enabled: Optional[bool] = None
    sandbox_config_frozen: bool = False
    # A person's turn on a recorded branch that is not the selected one
    # (``branch_id``, ``advance_head=False``), running beside the selected
    # branch's turn: it reads its branch's whole history, moves only its
    # own branch (compacting it when over budget, and counting its tokens on
    # it), and leaves the conversation's status, unread dot, model, title
    # and channel replies to the selected branch.
    side_branch: bool = False
    # Frozen at admission: whether this turn carries the conversation's own
    # effects (its head, status, unread dot, title, the conversation token
    # count and channel replies). False for a turn on a branch that is not
    # the selected one and for a background check-in. Read through
    # ``owns_conversation_effects``; ``None`` only on a request admitted
    # before the flag existed, which derives it from the two flags above.
    owns_conversation_effects: Optional[bool] = None
    # Frozen at admission: the settings this turn runs with, where each came
    # from, and the Agent they belong to (``freeze_branch_request``). Written
    # on the turn's user message as ``turn_settings``.
    turn_settings: Optional[dict[str, Any]] = None
    # A typed ``/task`` (``"spawn"``) or ``/merge`` (``"merge"``): the turn
    # carries out that slash action instead of a model round. ``interaction``
    # keeps the authority's value. A request admitted before this field
    # marked the action in ``interaction``; read both with ``slash_action_of``.
    slash_action: Optional[str] = None
    # A foreground sub-agent's approval questions are shown in its caller's
    # conversation, which waits on it: ``{"session_id", "execution_id",
    # "label"}``, frozen at admission (permissions/approvals/lifecycle.py
    # ``spawn_question_route``). None: nobody waits on this turn's questions.
    asks_through: Optional[dict[str, Any]] = None
    # The message sets a Goal (``/goal <objective>``): written on the turn's
    # user message as ``starts_goal``, which the chat labels Goal.
    starts_goal: bool = False

    def __post_init__(self) -> None:
        if self.profile_snapshot is not None:
            if not isinstance(self.profile_snapshot, dict):
                raise ValueError("profile_snapshot must be a dict")
            self.profile_snapshot = deepcopy(self.profile_snapshot)


def owns_conversation_effects(request: Any) -> bool:
    """Whether a turn carries the conversation's own effects.

    Read from the flag frozen at admission; the rule lives in
    :mod:`openprogram.runtime.execution.admission_input.turn_effects`, below the call core,
    because the execution control plane reads it too.
    """
    from openprogram.runtime.execution.admission_input.turn_effects import (
        owns_conversation_effects as read_frozen_flag,
    )
    return read_frozen_flag(request)


current_turn_request: contextvars.ContextVar[Optional[Any]] = (
    contextvars.ContextVar("openprogram_current_turn_request", default=None)
)


def set_turn_request(req: Any):
    return current_turn_request.set(req)


def get_turn_request() -> Optional[Any]:
    return current_turn_request.get(None)


def reset_turn_request(token) -> None:
    try:
        current_turn_request.reset(token)
    except ValueError:
        pass


def inner_turn_request(source: str) -> Optional[Any]:
    """Derive the request an inner AgentSession's tools are gated by.

    Inherits the outer request's authority through ``runtime_authority``
    (which pins ``interaction="non-interactive"`` and the runtime speaker),
    and keeps source/permission_mode/permission_rules so neither the hard
    constraints nor a deny rule can be dropped by descending a level.
    """
    outer = get_turn_request()
    if outer is None:
        return None
    from openprogram.runtime.permissions.authority import runtime_authority
    from openprogram.runtime.permissions.identity import inherited_automation

    authority = runtime_authority(outer, source)
    if not authority:
        return None
    return TurnRequest(
        session_id=getattr(outer, "session_id", "") or "",
        user_text="",
        agent_id=getattr(outer, "agent_id", "main") or "main",
        # A nested program cannot widen the outer source. agent_spawn and
        # cron stay themselves so their hard-constraint sets keep applying.
        source=getattr(outer, "source", "web") or "web",
        permission_mode=getattr(outer, "permission_mode", None),
        permission_rules=getattr(outer, "permission_rules", None),
        surface_context=getattr(outer, "surface_context", None),
        additional_working_dirs=list(
            getattr(outer, "additional_working_dirs", None) or ()
        ),
        automation=inherited_automation(outer),
        **authority,
    )


def slash_action_of(request: Any) -> Optional[str]:
    """The slash action a turn request carries out, or ``None`` for a model
    turn (:func:`openprogram.runtime.execution.admission_input.slash_actions.slash_action_of`,
    which recovery reads stored requests with)."""
    from openprogram.runtime.execution.admission_input.slash_actions import slash_action_of as _of
    return _of(request)


__all__ = [
    "PermissionMode", "INHERIT_PARENT", "TurnRequest", "slash_action_of",
    "current_turn_request", "set_turn_request", "get_turn_request",
    "reset_turn_request", "inner_turn_request", "owns_conversation_effects",
]

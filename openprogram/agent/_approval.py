"""Tool-approval gate — runs over the unified QuestionRegistry.

Lifted out of ``dispatcher.py`` to keep that file from drowning. 审批已
合流到 user-input 的 QuestionRegistry（kind="approval"），所以批准和
runtime.ask 走同一条链路、同一个前端承接点（composer approval mode）。
两个 moving parts：

* ``await_user_approval`` — registers a ``kind="approval"`` question on the
  shared QuestionRegistry, emits ``question.asked`` through the event layer,
  and awaits the answer off the asyncio loop (``asyncio.to_thread`` on the
  registry's Event). answered「允许」→ True；declined / timeout → False.
* ``wrap_with_approval`` — returns a copy of the agent tool whose
  ``execute`` first awaits approval (unless permission_mode bypasses it).
  The wrapping happens inside the tool's coroutine because agent_loop
  schedules tool.execute eagerly — gating from outside is racey.

``approval_registry()`` returns the shared QuestionRegistry (no separate
ApprovalRegistry class anymore); tests resolve via
``resolve(qid, "answered"|"declined", value)``.
See docs/design/runtime/user-input-requests.md (point 6) +
docs/design/ui/composer-interaction-modes.md.
"""
from __future__ import annotations

import asyncio
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest

EventCallback = Callable[[dict], None]


# 审批合流到 QuestionRegistry（kind="approval"）——不再有独立的 ApprovalRegistry。
# ``approval_registry()`` 现在返回统一的 QuestionRegistry，调用方（测试 / WS）用
# 它的 resolve(qid, "answered"|"declined", value) 应答；批准的等待/唤醒走
# await_user_approval。保留这个访问器名是为了不破坏现有 import 点。

def approval_registry():
    """已合流：返回统一的 QuestionRegistry（审批是 kind="approval" 的问题）。"""
    from openprogram.agent.questions import get_question_registry
    return get_question_registry()


def wrap_with_approval(
    agent_tool,
    req: "TurnRequest",
    on_event: EventCallback,
):
    """Return a copy of ``agent_tool`` whose ``execute`` first checks
    approval, awaiting (not blocking) the user's response. Falls back
    to the original tool when permission_mode is "bypass" or the
    tool's per-tool gate decides no approval is needed.

    Why a wrapper layer (vs. inspecting tool_execution_start in the
    drain): agent_loop schedules ``await tool.execute(...)`` directly
    after pushing tool_execution_start. The dispatcher's async-for
    consumer can't reliably block the tool from running because the
    tool already runs as a thread-pool task in parallel. Gating
    inside the tool's own coroutine is the only safe seam.
    """
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent
    from openprogram.functions._runtime import tool_requires_approval

    orig_execute = agent_tool.execute

    # Tools that MUST always ask for user input, even when the session's
    # permission_mode is "bypass". exit_plan_mode is the canonical case:
    # the whole point of submitting a plan is to get explicit user sign-
    # off; silently approving it under bypass would defeat the feature.
    _force_approval_tools = {"exit_plan_mode"}

    async def _gated_execute(call_id, args, cancel, on_update):
        force_ask = agent_tool.name in _force_approval_tools
        if req.permission_mode == "bypass" and not force_ask:
            return await orig_execute(call_id, args, cancel, on_update)

        per_tool_required, _per_tool_reason = tool_requires_approval(agent_tool, args)
        if req.permission_mode == "auto" and not force_ask:
            risky_default = agent_tool.name in {"bash", "exec", "shell",
                                                  "execute_code", "process"}
            if not per_tool_required and not risky_default:
                return await orig_execute(call_id, args, cancel, on_update)

        approved, reason = await await_user_approval(
            req=req,
            tool_name=agent_tool.name,
            args=args,
            on_event=on_event,
        )
        if not approved:
            # 拒绝理由（用户在 approval mode 填的）作为错误文本回给模型，
            # 否则只说"未批准"（opencode 做法）。
            msg = (f"[denied] {reason.strip()}" if isinstance(reason, str)
                   and reason.strip()
                   else f"[denied] user did not approve {agent_tool.name}")
            return AgentToolResult(
                content=[TextContent(text=msg)],
                details={"is_error": True, "denied": True},
            )
        return await orig_execute(call_id, args, cancel, on_update)

    wrapped = AgentTool(
        name=agent_tool.name,
        description=agent_tool.description,
        parameters=agent_tool.parameters,
        label=getattr(agent_tool, "label", agent_tool.name) or agent_tool.name,
        execute=_gated_execute,
    )
    # Carry over sidecar flags the dispatcher reads downstream.
    # _is_agentic in particular is how runtime-block rendering is
    # triggered for LLM-invoked @agentic_function calls.
    for _attr in ("_is_agentic", "_defer"):
        try:
            setattr(wrapped, _attr, getattr(agent_tool, _attr, None))
        except Exception:
            pass
    return wrapped


def _approval_detail(tool_name: str, args: dict) -> str:
    """批准卡片的危险摘要：工具名 + 参数全文（超长截断，首尾保留）。
    第一版不做危险 token 高亮（docs/design/ui/composer-interaction-modes.md 决策）。"""
    try:
        import json
        body = json.dumps(args, ensure_ascii=False, indent=2) if args else ""
    except Exception:
        body = str(args)
    if len(body) > 2000:
        body = body[:1200] + "\n…（已截断）…\n" + body[-600:]
    return f"{tool_name}\n{body}".rstrip()


async def await_user_approval(
    *,
    req: "TurnRequest",
    tool_name: str,
    args: dict,
    on_event: EventCallback,
    timeout: float = 300.0,
) -> tuple[bool, "str | None"]:
    """注册一个 kind="approval" 的问题、经事件层发 question.asked、await 用户答。
    返回 (approved, reason)：approved=是否放行；reason=拒绝理由（用户在 approval
    mode 填的，可为 None），由调用方变成回给模型的错误文本。

    审批合流到 QuestionRegistry（docs/design/runtime/user-input-requests.md 点6
    + docs/design/ui/composer-interaction-modes.md）：不再用独立的 ApprovalRegistry
    / approval_request 信封，而是走 runtime.ask 同一条链路——前端 composer 把它
    呈现成 approval mode（允许 / 拒绝）。answered「允许」=放行；declined / timeout
    = 不放行。

    用 ``asyncio.to_thread`` 等 threading.Event，asyncio loop 不被阻塞（工具
    execute 是协程，并发工具的进度事件照常处理）。
    """
    from openprogram.agent.questions import (
        open_question, consume_or_timeout, emit_question_asked,
        retract_question,
    )

    # 跟 runtime.ask 一致：如果当前执行上下文有 runtime（@agentic_function 跑在
    # 子进程，runtime 上装了 QueueTransport），用它的 transport 把问题送回父进程；
    # 否则（主 agent loop 里 gate LLM 工具调用）走默认事件层。
    transport = None
    try:
        from openprogram.agentic_programming.function import _current_runtime
        rt = _current_runtime.get(None)
        if rt is not None:
            transport = getattr(rt, "_question_transport", None)
    except Exception:
        pass

    def _on_asked(q) -> None:
        emit_question_asked({
            "id": q.id, "session_id": q.session_id, "kind": q.kind,
            "prompt": q.prompt, "options": q.options, "multi": q.multi,
            "allow_custom": q.allow_custom, "detail": q.detail,
            "expires_at": q.expires_at,
            # approval 专属：工具名 + 参数，给 approval mode 画危险摘要。
            "tool": tool_name, "args": args,
        }, transport)

    q, ev = open_question(
        session_id=req.session_id, kind="approval",
        prompt=f"允许执行 {tool_name}？",
        options=["允许", "拒绝"], multi=False, allow_custom=False,
        detail=_approval_detail(tool_name, args), timeout=timeout,
        on_asked=_on_asked,
    )
    await asyncio.to_thread(ev.wait, timeout)
    outcome, value = consume_or_timeout(q.id)
    if outcome == "timeout":
        retract_question(q.id, transport)  # 超时收回前端批准卡片
    if outcome == "answered":
        ok = (value.strip() in ("允许", "approve", "yes", "y", "true", "ok", "是")
              if isinstance(value, str) else bool(value))
        return ok, None
    # declined：value 可能是用户填的拒绝理由（reason）；timeout：value=None。
    reason = value if (outcome == "declined" and isinstance(value, str)) else None
    return False, reason

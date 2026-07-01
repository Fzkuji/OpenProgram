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

# 即使 bypass 也强制审批的工具（提交计划要用户签字）。
_FORCE_APPROVAL_TOOLS = {"exit_plan_mode"}
# auto 档下即便未声明 requires_approval 也仍要审批的高风险工具。
_RISKY_TOOLS = {"bash", "exec", "shell", "execute_code", "process"}


# 审批合流到 QuestionRegistry（kind="approval"）——不再有独立的 ApprovalRegistry。
# ``approval_registry()`` 现在返回统一的 QuestionRegistry，调用方（测试 / WS）用
# 它的 resolve(qid, "answered"|"declined", value) 应答；批准的等待/唤醒走
# await_user_approval。保留这个访问器名是为了不破坏现有 import 点。

def approval_registry():
    """已合流：返回统一的 QuestionRegistry（审批是 kind="approval" 的问题）。"""
    from openprogram.agent.questions import get_question_registry
    return get_question_registry()


def _match_rule(rules, tool_name: str, args: dict) -> "str | None":
    """匹配用户配的权限规则，返回 "deny" | "ask" | "allow" | None（未命中）。
    优先级固定 deny > ask > allow。每档内：先 per-tool，再 per-pattern。
    见 docs/design/runtime/permission-model.md §3.4。"""
    if rules is None:
        return None
    from openprogram.functions.permission_rule import parse_rule, parse_command, pattern_matches
    cmd = None  # 惰性求值：只在遇到 per-pattern 规则时解析命令
    for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        for raw in ruleset:
            rv = parse_rule(raw)
            if rv.tool_name != tool_name:
                continue
            if rv.pattern is None:
                return behavior
            if cmd is None:
                cmd = parse_command(tool_name, args)
            if cmd is not None and pattern_matches(rv.pattern, cmd):
                return behavior
    return None


def _would_need_approval(tool_name: str, per_tool_required: bool) -> bool:
    """dontAsk 档判定：这次调用在非 dontAsk 下会不会需要审批。"""
    return per_tool_required or tool_name in _RISKY_TOOLS


def _path_is_safe(tool_name: str, args: dict, req: "TurnRequest") -> bool:
    """acceptEdits 档下判断写目标是否在安全工作目录内。
    完整危险文件/Windows 绕过检测在 file_safety.py（S13）；这里先只判"路径在
    工作目录集内"这一必要条件。"""
    import os
    from openprogram.functions.permission_rule import parse_command
    path = parse_command(tool_name, args)
    if not path:
        return True  # 无路径参数（如 glob/grep）视为安全
    work_dirs = [os.getcwd(), *getattr(req, "additional_working_dirs", [])]
    ap = os.path.realpath(path)
    return any(ap == os.path.realpath(d) or ap.startswith(os.path.realpath(d) + os.sep)
               for d in work_dirs)


def _persist_always_allow_rule(session_id: str, tool_name: str,
                               destination: str = "session") -> None:
    """把 "总是允许" 写成一条 per-tool allow 规则并落盘。
    destination="session"（默认）→ 落 session meta（schemaless）。"""
    if not session_id or destination != "session":
        return
    from openprogram.agent.session_config import (
        load_session_run_config, save_session_run_config, PermissionRules)
    cfg = load_session_run_config(session_id)
    rules = cfg.permission_rules or PermissionRules()
    if tool_name not in rules.allow:
        rules.allow.append(tool_name)
    save_session_run_config(session_id, agent_id=cfg.__dict__.get("agent_id", "main"),
                            permission_rules=rules)


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
    name = agent_tool.name

    def _denied(text: str) -> "AgentToolResult":
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={"is_error": True, "denied": True},
        )

    async def _approve_then_run(call_id, args, cancel, on_update):
        approved, reason, scope = await await_user_approval(
            req=req, tool_name=name, args=args, on_event=on_event)
        if not approved:
            msg = (f"[denied] {reason.strip()}" if isinstance(reason, str)
                   and reason.strip() else f"[denied] user did not approve {name}")
            return _denied(msg)
        if scope == "always":
            _persist_always_allow_rule(req.session_id, name)
        return await orig_execute(call_id, args, cancel, on_update)

    async def _gated_execute(call_id, args, cancel, on_update):
        mode = req.permission_mode
        force_ask = name in _FORCE_APPROVAL_TOOLS

        # ① 规则层 deny/ask —— bypass 之前，最高安全优先级
        verdict = _match_rule(getattr(req, "permission_rules", None), name, args)
        if verdict == "deny":
            return _denied(f"[denied] blocked by deny rule: {name}")
        if verdict == "ask":
            return await _approve_then_run(call_id, args, cancel, on_update)

        # ② force_ask（exit_plan_mode），bypass 也不能跳
        if force_ask:
            return await _approve_then_run(call_id, args, cancel, on_update)

        # ③ bypass 短路（deny/ask/force 之后）
        if mode == "bypass":
            return await orig_execute(call_id, args, cancel, on_update)

        per_tool_required, _reason = tool_requires_approval(agent_tool, args)

        # ④ dontAsk：本该问的直接拒
        if mode == "dontAsk":
            if _would_need_approval(name, per_tool_required):
                return _denied(f"[denied] dontAsk mode: approval required for {name}")
            return await orig_execute(call_id, args, cancel, on_update)

        # ⑤ 规则层 allow —— bypass 之后
        if verdict == "allow":
            return await orig_execute(call_id, args, cancel, on_update)

        # ⑥ acceptEdits：写安全工具自动放行；命令类落审批
        if mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) \
                and _path_is_safe(name, args, req):
            return await orig_execute(call_id, args, cancel, on_update)

        # ⑦ auto：低风险直接放
        if mode == "auto" and not per_tool_required and name not in _RISKY_TOOLS:
            return await orig_execute(call_id, args, cancel, on_update)

        # ⑧ 弹卡片阻塞等答
        return await _approve_then_run(call_id, args, cancel, on_update)

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


def _risk_level(tool_name: str, args: dict) -> str:
    """审批卡片的危险分级 "low"|"medium"|"high"，驱动前端高亮。
    完整规则集见 file_safety.py（S13）；这里是基础判定。"""
    name = tool_name.lower()
    if name in _RISKY_TOOLS:
        cmd = str((args or {}).get("command", "")).lower()
        if any(p in cmd for p in ("rm -rf", "sudo", "mkfs", ":(){", "| sh", "| bash", "curl", "wget")):
            return "high"
        return "medium"
    if any(k in name for k in ("write", "edit", "apply_patch", "delete", "remove")):
        return "medium"
    return "low"


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
) -> tuple[bool, "str | None", str]:
    """注册一个 kind="approval" 的问题、经事件层发 question.asked、await 用户答。
    返回 (approved, reason, scope)：approved=是否放行；reason=拒绝理由（可为 None）；
    scope ∈ {"once","always"}——"总是允许"经 question_reply 的 scope 字段带回，
    调用方据此把这次批准写成持久 allow 规则。

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
            # approval 专属：工具名 + 参数 + 危险分级，给 approval mode 画危险摘要。
            "tool": tool_name, "args": args, "risk_level": _risk_level(tool_name, args),
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
        return False, None, "once"
    if outcome == "answered":
        # value 可能是纯 answer 串，或前端带 scope 的 dict {"answer","scope"}。
        answer, scope = (value.get("answer"), value.get("scope", "once")) \
            if isinstance(value, dict) else (value, "once")
        ok = (answer.strip() in ("允许", "approve", "yes", "y", "true", "ok", "是")
              if isinstance(answer, str) else bool(answer))
        return ok, None, (scope if scope in ("once", "always") else "once")
    # declined：value 可能是用户填的拒绝理由（reason）。
    reason = value if (outcome == "declined" and isinstance(value, str)) else None
    return False, reason, "once"

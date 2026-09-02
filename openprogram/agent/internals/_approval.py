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
import time
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest

EventCallback = Callable[[dict], None]

# 即使 bypass 也强制审批的工具（提交计划要用户签字）。
_FORCE_APPROVAL_TOOLS = {"exit_plan_mode"}
# auto 档下即便未声明 requires_approval 也仍要审批的高风险工具。
_RISKY_TOOLS = {"bash", "exec", "shell", "execute_code", "process"}
# Non-interactive worktree side effects mutate repository state outside the
# spawned agent's working directories, and those turns cannot ask approval.
_WORKTREE_TOOLS = {
    "worktree_create", "worktree_merge", "worktree_discard", "worktree_keep",
}
_WRITE_TOOLS = {"write", "write_file", "edit", "edit_file"}
_PATCH_PATH_PREFIXES = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)
_NON_INTERACTIVE_SOURCES = {"agent_spawn", "cron", "scheduler", "mcp"}
_SCHEDULED_MEMORY_TOOLS = {
    "memory_status", "memory_get", "memory_update",
}
_SCHEDULED_ALLOWED_TOOLS = {
    "read", "read_file", "grep", "glob", "list", "list_files", "tool_search",
} | _SCHEDULED_MEMORY_TOOLS


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
    from openprogram.programs.permission_rule import parse_rule, parse_command, pattern_matches
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
            if cmd is not None and pattern_matches(
                rv.pattern, cmd, allow=(behavior == "allow"),
            ):
                return behavior
    return None


def _path_is_safe(tool_name: str, args: dict, req: "TurnRequest") -> bool:
    """acceptEdits 档下判断写目标是否安全（工作目录内 + 非危险文件/目录 +
    无 Windows 绕过）。完整规则集在 file_safety.check_path_safety。"""
    import os
    from openprogram.programs.permission_rule import parse_command
    from openprogram.programs.tools.files.file_safety import check_path_safety
    from openprogram.worktree.context import current_worktree_path
    path = parse_command(tool_name, args)
    if not path:
        return True  # 无路径参数（如 glob/grep）视为安全
    # 围栏基准与 system prompt 的 cwd 同源（_model_tools 同一 ContextVar）：
    # dispatcher 每 turn 把真实 cwd（worktree / 项目路径）绑进
    # current_worktree_path，进程 getcwd 只是无绑定时的回落。
    worktree = current_worktree_path() or os.getcwd()
    work_dirs = [worktree, *getattr(req, "additional_working_dirs", [])]
    target = path if os.path.isabs(path) else os.path.join(worktree, path)
    return check_path_safety(target, work_dirs)["safe"]


def _hard_constraint_violation(
    tool_name: str,
    args: dict,
    req: "TurnRequest",
) -> str | None:
    """Return the non-configurable constraint violated by an external turn."""
    import os
    from openprogram.programs.permission_rule import parse_command
    from openprogram.protected_paths import applications_root
    from openprogram.worktree.context import current_worktree_path

    protected = applications_root()

    def _targets_agentics(path: str | None) -> bool:
        if not path or not protected:
            return False
        if not os.path.isabs(path):
            path = os.path.join(current_worktree_path() or os.getcwd(), path)
        target = os.path.realpath(path)
        root = os.path.realpath(protected)
        return target == root or target.startswith(root + os.sep)

    if tool_name in _WRITE_TOOLS:
        path = parse_command(tool_name, args)
        if _targets_agentics(path):
            return "model tools cannot write auto-imported agentic Python"
    if tool_name == "apply_patch":
        patch = args.get("patch") if isinstance(args, dict) else None
        if isinstance(patch, str):
            for line in patch.splitlines():
                prefix = next(
                    (p for p in _PATCH_PATH_PREFIXES if line.startswith(p)), None
                )
                if prefix and _targets_agentics(line[len(prefix):].strip()):
                    return "model tools cannot write auto-imported agentic Python"

    if req.source in {"cron", "scheduler"}:
        if tool_name not in _SCHEDULED_ALLOWED_TOOLS:
            return f"{req.source} cannot execute side-effect tool {tool_name}"
        if (
            tool_name in _SCHEDULED_MEMORY_TOOLS
            and req.authority_tier != "owner"
        ):
            return f"{req.source} Memory lifecycle requires owner authority"
        return None
    if req.source not in {"agent_spawn", "mcp"}:
        return None
    if tool_name in _RISKY_TOOLS or tool_name in _WORKTREE_TOOLS:
        return f"{req.source} cannot execute {tool_name}"
    if tool_name in _WRITE_TOOLS:
        if not _path_is_safe(tool_name, args, req):
            return (
                f"{req.source} cannot write outside its working directories: "
                f"{tool_name}"
            )
        return None
    if tool_name != "apply_patch":
        return None

    patch = args.get("patch") if isinstance(args, dict) else None
    if not isinstance(patch, str):
        return None
    for line in patch.splitlines():
        prefix = next((p for p in _PATCH_PATH_PREFIXES if line.startswith(p)), None)
        if prefix is None:
            continue
        path = line[len(prefix):].strip()
        if not _path_is_safe("write", {"file_path": path}, req):
            return (
                f"{req.source} cannot apply a patch outside its working "
                "directories"
            )
    return None


def _persist_always_allow_rule(session_id: str, tool_name: str, args: dict) -> bool:
    """把 "总是允许" 写成一条精确操作规则，落到**项目**层
    （<project>/.openprogram/settings.json 的 permission_rules.allow）。
    规则跟项目走——切会话仍生效、长期记住。见 permission-model.md §2.3。"""
    if not session_id:
        return False
    try:
        from openprogram.programs.permission_rule import (
            exact_rule_for_call, rule_to_string,
        )
        from openprogram.store.project import project_store as _projects
        value = exact_rule_for_call(tool_name, args)
        if value is None:
            return False
        serialized = rule_to_string(value)
        proj = _projects.project_for_session(session_id) or _projects.get_default_project()
        settings = _projects.load_project_settings(proj.id)
        rules = settings.get("permission_rules") or {"allow": [], "deny": [], "ask": []}
        allow = rules.setdefault("allow", [])
        if serialized not in allow:
            allow.append(serialized)
        settings["permission_rules"] = rules
        _projects.save_project_settings(proj.id, settings)
        return True
    except Exception:
        return False


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

    orig_execute = agent_tool.execute
    name = agent_tool.name

    def _denied(
        text: str,
        reason_code: str,
        authority_decision=None,
    ) -> "AgentToolResult":
        details = {
            "denied": True,
            "reason_code": reason_code,
        }
        if authority_decision is not None:
            details["authority_decision"] = authority_decision.to_dict()
        return AgentToolResult(
            content=[TextContent(text=text)],
            details=details,
            is_error=True,
        )

    def _approval_authorized() -> bool:
        from openprogram.agent.authority import (
            has_capability, normalize_authority, owner_principal_id,
        )

        authority = normalize_authority(req)
        try:
            is_owner = authority.get("principal_id") == owner_principal_id()
        except Exception:
            is_owner = False
        return bool(
            authority
            and is_owner
            and authority.get("authority_tier") == "owner"
            and authority.get("speaker_kind") == "owner"
            and authority.get("interaction") == "interactive"
            and has_capability(authority, "approval.request")
        )

    def _sandbox_metadata(result) -> dict | None:
        details = getattr(result, "details", None)
        if not isinstance(details, dict):
            return None
        direct = details.get("sandbox")
        nested = details.get("json")
        value = direct if isinstance(direct, dict) else (
            nested.get("sandbox") if isinstance(nested, dict) else None
        )
        return value if isinstance(value, dict) else None

    async def _run_original(
        call_id, args, cancel, on_update, *, already_escalated=False,
    ):
        result = await orig_execute(call_id, args, cancel, on_update)
        sandbox = _sandbox_metadata(result)
        if not sandbox or sandbox.get("kind") != "denied":
            return result

        event = {
            "type": "sandbox.violation",
            "data": {
                "session_id": req.session_id,
                "tool": name,
                "args": args,
                "sandbox": sandbox,
            },
        }
        try:
            on_event(event)
        except Exception:
            pass

        # Bypass never offers sandbox escalation. An approved escalation
        # retry already ran with configurable restrictions lifted;
        # asking again would rerun the same policy. Return the denial.
        if already_escalated or req.permission_mode == "bypass":
            return result

        if not _approval_authorized():
            return _denied(
                "[denied] sandbox escalation requires an interactive local owner",
                "SANDBOX_ESCALATION_OWNER_REQUIRED",
            )
        hard_violation = _hard_constraint_violation(name, args, req)
        if hard_violation:
            return _denied(
                f"[denied] hard constraint: {hard_violation}",
                "HARD_CONSTRAINT_DENIED",
            )
        escalation = {
            "from": sandbox.get("backend", "sandbox"),
            "to": "hard-constraints-only",
        }
        if sandbox.get("path"):
            escalation["path"] = sandbox["path"]
        if sandbox.get("rule"):
            escalation["rule"] = sandbox["rule"]
        approval_args = {**args, "_sandbox_escalation": escalation}
        approved, reason, scope = await await_user_approval(
            req=req,
            tool_name=f"{name}:sandbox-escalation",
            args=approval_args,
            on_event=on_event,
        )
        if not approved:
            msg = (f"[denied] {reason.strip()}" if isinstance(reason, str)
                   and reason.strip() else "[denied] sandbox escalation not approved")
            return _denied(msg, "SANDBOX_ESCALATION_NOT_APPROVED")
        if scope == "always_path":
            from openprogram.sandbox import persist_allow_read
            err = persist_allow_read(sandbox.get("path"))
            if err:
                return _denied(f"[denied] {err}", "SANDBOX_ALLOW_READ_REFUSED")
        from openprogram.sandbox import escalated_policy
        with escalated_policy():
            return await orig_execute(call_id, args, cancel, on_update)

    async def _approve_then_run(call_id, args, cancel, on_update):
        if not _approval_authorized():
            return _denied(
                "[denied] approval requires an interactive local owner",
                "APPROVAL_LOCAL_OWNER_REQUIRED",
            )
        approved, reason, scope = await await_user_approval(
            req=req, tool_name=name, args=args, on_event=on_event)
        if not approved:
            msg = (f"[denied] {reason.strip()}" if isinstance(reason, str)
                   and reason.strip() else f"[denied] user did not approve {name}")
            return _denied(msg, "APPROVAL_DENIED")
        if scope == "always":
            _persist_always_allow_rule(req.session_id, name, args)
        return await _run_original(call_id, args, cancel, on_update)

    async def _gated_execute(call_id, args, cancel, on_update):
        mode = req.permission_mode
        force_ask = name in _FORCE_APPROVAL_TOOLS

        # Non-interactive external turns have no approval surface. These
        # constraints are evaluated before rules and bypass, so neither a
        # stored allow rule nor permission_mode can remove them.
        hard_violation = _hard_constraint_violation(name, args, req)
        if hard_violation:
            return _denied(
                f"[denied] hard constraint: {hard_violation}",
                "HARD_CONSTRAINT_DENIED",
            )

        # Tier is runtime-owned, not a model-visible label. The request carries
        # one enum; capabilities are read only from the fixed process table.
        # Missing/unknown tiers deny before rules, approval, or bypass.
        from openprogram.agent.authority import decide_tool_authority
        authority_decision = decide_tool_authority(req, name, args)
        if not authority_decision.allowed:
            return _denied(
                "[denied] authority tier does not allow "
                f"{authority_decision.capability}",
                authority_decision.reason_code,
                authority_decision,
            )

        # ① 规则层 deny/ask —— bypass 之前，最高安全优先级
        verdict = _match_rule(getattr(req, "permission_rules", None), name, args)
        if verdict == "deny":
            return _denied(
                f"[denied] blocked by deny rule: {name}",
                "PERMISSION_RULE_DENY",
            )
        if verdict == "ask":
            if req.source in _NON_INTERACTIVE_SOURCES:
                return _denied(
                    f"[denied] non-interactive {req.source} cannot approve {name}",
                    "APPROVAL_UNAVAILABLE_NON_INTERACTIVE",
                )
            return await _approve_then_run(call_id, args, cancel, on_update)

        # ② force_ask（exit_plan_mode），bypass 也不能跳
        if force_ask:
            if req.source in _NON_INTERACTIVE_SOURCES:
                return _denied(
                    f"[denied] non-interactive {req.source} cannot approve {name}",
                    "APPROVAL_UNAVAILABLE_NON_INTERACTIVE",
                )
            return await _approve_then_run(call_id, args, cancel, on_update)

        # The owner granted this turn access to one exact in-app web surface
        # when sending the message.  Requiring a second generic process
        # approval here would make that explicit, turn-scoped grant unusable.
        # Deny/ask rules, authority checks, and hard constraints above remain
        # authoritative; this exception applies only to the bound public tool.
        if name == "web_use":
            from openprogram.agent.surface_context import web_use_available

            if web_use_available(getattr(req, "surface_context", None)):
                return await _run_original(call_id, args, cancel, on_update)

        # ③ bypass：跳过普通审批与 Auto，不改变 Sandbox。
        if mode == "bypass":
            return await _run_original(call_id, args, cancel, on_update)

        # ④ 规则层 allow —— bypass 之后
        if verdict == "allow":
            return await _run_original(call_id, args, cancel, on_update)

        # A signed owner-created prompt task may keep its linked Memory record
        # current without an approval UI. Explicit deny/ask rules above still
        # win, and the hard constraint limits scheduled turns to these tools.
        if req.source in {"cron", "scheduler"} and name in _SCHEDULED_MEMORY_TOOLS:
            return await _run_original(call_id, args, cancel, on_update)

        # ⑤ 只读安全工具全模式放行（对齐 CC：Ask / Accept edits / Plan 下
        #    read/grep/glob 这类只读调用不弹卡，审批只留给会改状态的）。
        #    复用 auto 分类器的白名单；deny/ask 规则在 ① 已优先兜住。
        from openprogram.agent.internals._auto_classifier import (
            auto_classify_tool, SAFE_AUTO_ALLOWLIST, RISKY_AUTO_DENYLIST,
        )
        if name in SAFE_AUTO_ALLOWLIST:
            return await _run_original(call_id, args, cancel, on_update)

        # ⑥ acceptEdits：写安全工具自动放行；命令类落审批
        if mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) \
                and _path_is_safe(name, args, req):
            return await _run_original(call_id, args, cancel, on_update)

        # ⑦ auto：LLM 分类器判定（对齐 CC "Auto mode"）。三级过滤省调用：
        #    明显安全在 ⑤ 已放行；明显危险→拒；拿不准→问一次 haiku。
        if mode == "auto":
            if name in RISKY_AUTO_DENYLIST:
                return _denied(
                    f"[denied] auto mode: risky tool blocked: {name}",
                    "AUTO_RISK_DENY",
                )
            should_block, reason = await auto_classify_tool(name, args)
            if should_block:
                return _denied(
                    f"[denied] auto classifier: {reason}",
                    "AUTO_CLASSIFIER_DENY",
                )
            return await _run_original(call_id, args, cancel, on_update)

        # ⑧ 弹卡片阻塞等答（ask / plan / acceptEdits 的命令类都落这里）
        if req.source in _NON_INTERACTIVE_SOURCES:
            return _denied(
                f"[denied] non-interactive {req.source} cannot approve {name}",
                "APPROVAL_UNAVAILABLE_NON_INTERACTIVE",
            )
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
    for _attr in (
        "_is_agentic", "_defer", "_run_in_worker", "_mcp_server",
        "_runtime_implementation", "_requires_approval", "_accept_edits_safe",
    ):
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
    scope ∈ {"once","always","always_path"}——"总是允许"经 canonical wait
    answer command 的 scope 字段带回；always_path 把被拦路径写入 sandbox.allow_read。

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
            "execution_id": q.execution_id,
            "wait_generation": q.wait_generation,
            "expected_version": q.execution_version,
        }, transport)

    q, ev = open_question(
        session_id=req.session_id, kind="approval",
        prompt=f"允许执行 {tool_name}？",
        options=["允许", "拒绝"], multi=False, allow_custom=False,
        detail=_approval_detail(tool_name, args), timeout=timeout,
        request_metadata={
            "tool": tool_name, "args": args,
            "risk_level": _risk_level(tool_name, args),
        },
        policy_snapshot={
            "version": 1, "kind": "approval", "on_decline": "deny",
            "on_timeout": "deny", "allowed_scopes": ["once", "always", "always_path"],
        },
        on_asked=_on_asked,
    )
    deadline = time.monotonic() + timeout
    outcome, value = "pending", None
    while outcome == "pending":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            from openprogram.execution import default_store
            from openprogram.execution.waits import DurableWaitStore

            DurableWaitStore(default_store()).expire_due()
            outcome, value = consume_or_timeout(q.id)
            break
        await asyncio.to_thread(ev.wait, min(0.25, remaining))
        ev.clear()
        outcome, value = consume_or_timeout(q.id)
    if outcome in {"pending", "timeout"}:
        retract_question(q.id, transport)  # 超时收回前端批准卡片
        return False, None, "once"
    if outcome == "answered":
        # value 可能是纯 answer 串，或前端带 scope 的 dict {"answer","scope"}。
        answer, scope = (value.get("answer"), value.get("scope", "once")) \
            if isinstance(value, dict) else (value, "once")
        ok = (answer.strip() in ("允许", "approve", "yes", "y", "true", "ok", "是")
              if isinstance(answer, str) else bool(answer))
        return ok, None, (scope if scope in ("once", "always", "always_path") else "once")
    # declined：value 可能是用户填的拒绝理由（reason）。
    reason = value if (outcome == "declined" and isinstance(value, str)) else None
    return False, reason, "once"

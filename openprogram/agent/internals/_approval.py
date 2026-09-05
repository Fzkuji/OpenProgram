"""Tool-approval gate — runs over durable execution waits.

Lifted out of ``dispatcher.py`` to keep that file from drowning. 审批已
合流到 user-input 的 QuestionRegistry（kind="approval"），所以批准和
runtime.ask 走同一条链路、同一个前端承接点（composer approval mode）。
两个 moving parts：

* ``await_user_approval`` — consumes the resolved ``kind="approval"`` wait
  selected by the Agent safe-point handoff. answered「允许」→ True；declined
  / timeout → False.
* ``wrap_with_approval`` — returns a copy of the agent tool whose
  ``execute`` first awaits approval (unless permission_mode bypasses it).
  The wrapping happens inside the tool's coroutine because agent_loop
  schedules tool.execute eagerly — gating from outside is racey.

See docs/design/runtime/user-input-requests.md (point 6) +
docs/design/ui/composer-interaction-modes.md.
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest

EventCallback = Callable[[dict], None]

# 即使 bypass 也强制审批的工具（提交计划要用户签字）。
_FORCE_APPROVAL_TOOLS = {"exit_plan_mode", "self_update_prepare", "self_update_retry"}
# These approvals bind one exact request and must never become a project-wide
# allow rule. A later candidate always needs its own owner decision.
_ONE_SHOT_FORCE_APPROVAL_TOOLS = {"self_update_prepare", "self_update_retry"}
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



def permission_decision(agent_tool, req, args: dict) -> tuple[str, str, str, object]:
    """One synchronous decision for both wait publication and execution.

    Auto classification runs only during execution. The final item carries
    structured authority evidence when a capability check denies access.
    """
    name = agent_tool.name
    violation = _hard_constraint_violation(name, args, req)
    if violation:
        return "deny", "HARD_CONSTRAINT_DENIED", f"hard constraint: {violation}", None
    from openprogram.agent.authority import decide_tool_authority
    authority = decide_tool_authority(req, name, args)
    if not authority.allowed:
        return "deny", authority.reason_code, f"authority tier does not allow {authority.capability}", authority
    from openprogram.agent import plan_mode
    from openprogram.programs import _unsafe_in_for
    if (req.permission_mode == "plan" or plan_mode.is_plan_mode(req.session_id)) and "plan" in _unsafe_in_for(name):
        return "deny", "PLAN_MODE_DENY", f"plan mode cannot execute {name}", None
    verdict = _match_rule(getattr(req, "permission_rules", None), name, args)
    if verdict == "deny":
        return "deny", "PERMISSION_RULE_DENY", f"blocked by deny rule: {name}", None
    if verdict == "ask" or name in _FORCE_APPROVAL_TOOLS:
        reason = "PERMISSION_RULE_ASK" if verdict == "ask" else "MANDATORY_APPROVAL"
    else:
        if name == "web_use":
            from openprogram.agent.surface_context import web_use_available
            if web_use_available(getattr(req, "surface_context", None)):
                return "allow", "SURFACE_GRANT", "", None
        if req.permission_mode == "bypass" or verdict == "allow":
            return "allow", "BYPASS" if req.permission_mode == "bypass" else "PERMISSION_RULE_ALLOW", "", None
        if req.source in {"cron", "scheduler"} and name in _SCHEDULED_MEMORY_TOOLS:
            return "allow", "SCHEDULED_MEMORY", "", None
        from openprogram.agent.internals._auto_classifier import SAFE_AUTO_ALLOWLIST
        if name in SAFE_AUTO_ALLOWLIST:
            return "allow", "SAFE_TOOL", "", None
        if req.permission_mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) and _path_is_safe(name, args, req):
            return "allow", "SAFE_EDIT", "", None
        if req.permission_mode == "auto":
            return "auto", "AUTO_CLASSIFY", "", None
        reason = "MODE_APPROVAL"
    if req.source in _NON_INTERACTIVE_SOURCES:
        return "deny", "APPROVAL_UNAVAILABLE_NON_INTERACTIVE", f"non-interactive {req.source} cannot approve {name}", None
    return "ask", reason, "", None

def wrap_with_approval(
    agent_tool,
    req: "TurnRequest",
    on_event: EventCallback,
    *,
    _live: bool = True,
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

    if _live:
        from openprogram.agent.permissions import wrap_live_permission
        return wrap_live_permission(agent_tool, req, on_event)

    orig_execute = agent_tool.execute
    name = agent_tool.name

    def _interaction_manifest(call_id: str, args: dict) -> dict | None:
        """Describe an approval before the Agent loop dispatches its effect."""
        decision, reason, _, _ = permission_decision(agent_tool, req, args)
        if decision != "ask":
            return None
        from openprogram.worktree.context import current_worktree_path
        import os
        return {
            "kind": "approval",
            "prompt": f"允许执行 {name}？",
            "options": ["允许", "拒绝"],
            "allow_custom": False,
            "detail": _approval_detail(name, args),
            "request_metadata": {
                "tool": name, "args": args, "tool_call_id": str(call_id),
                "risk_level": _risk_level(name, args),
                "approval_reason": reason,
                "permission_version": getattr(req, "_permission_version", 0),
                "accept_edits_safe": bool(getattr(agent_tool, "_accept_edits_safe", False)),
                "working_dir": current_worktree_path() or os.getcwd(),
            },
            "policy_snapshot": {
                "version": 1, "kind": "approval", "on_answer": "continue",
                "on_decline": "fail", "on_timeout": "fail",
                "allowed_scopes": ["once", "always", "always_path"],
            },
            "timeout": 300.0,
        }

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
            tool_call_id=str(call_id),
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
        approval_args = args
        if name == "self_update_retry":
            from openprogram.self_update.next_candidate import approval_preview
            try:
                preview = approval_preview(args.get("update_id"), args.get("candidate_sha"), req)
            except Exception as exc:
                return _denied(f"[denied] {exc}", "SELF_UPDATE_RETRY_INVALID")
            approval_args = {**args, "candidate": preview}
        approved, reason, scope = await await_user_approval(
            req=req, tool_name=name, args=approval_args, on_event=on_event,
            tool_call_id=str(call_id))
        if not approved:
            msg = (f"[denied] {reason.strip()}" if isinstance(reason, str)
                   and reason.strip() else f"[denied] user did not approve {name}")
            return _denied(msg, "APPROVAL_DENIED")
        if scope == "always" and name not in _ONE_SHOT_FORCE_APPROVAL_TOOLS:
            _persist_always_allow_rule(req.session_id, name, args)
        return await _run_original(call_id, args, cancel, on_update)

    async def _gated_execute(call_id, args, cancel, on_update):
        decision, code, message, authority = permission_decision(agent_tool, req, args)
        if decision == "deny":
            return _denied(f"[denied] {message}", code, authority)
        if decision == "ask":
            return await _approve_then_run(call_id, args, cancel, on_update)
        if decision == "auto":
            from openprogram.agent.internals._auto_classifier import auto_classify_tool, RISKY_AUTO_DENYLIST
            if name in RISKY_AUTO_DENYLIST:
                return _denied(f"[denied] auto mode: risky tool blocked: {name}", "AUTO_RISK_DENY")
            blocked, reason = await auto_classify_tool(name, args)
            if blocked:
                return _denied(f"[denied] auto classifier: {reason}", "AUTO_CLASSIFIER_DENY")
        return await _run_original(call_id, args, cancel, on_update)

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
    object.__setattr__(wrapped, "_interaction_manifest", _interaction_manifest)
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
    if len(body) > 2000 and tool_name not in _ONE_SHOT_FORCE_APPROVAL_TOOLS:
        body = body[:1200] + "\n…（已截断）…\n" + body[-600:]
    return f"{tool_name}\n{body}".rstrip()


async def await_user_approval(
    *,
    req: "TurnRequest",
    tool_name: str,
    args: dict,
    on_event: EventCallback,
    timeout: float = 300.0,
    tool_call_id: str | None = None,
) -> tuple[bool, "str | None", str]:
    """Consume the resolved approval wait selected by the Agent safe point.
    返回 (approved, reason, scope)：approved=是否放行；reason=拒绝理由（可为 None）；
    scope ∈ {"once","always","always_path"}——"总是允许"经 canonical wait
    answer command 的 scope 字段带回；always_path 把被拦路径写入 sandbox.allow_read。

    审批等待由 Agent safe-point handoff 预先发布，答案通过 canonical
    ``execution.wait.answer`` / ``execution.wait.decline`` command 写入 durable
    execution state；此函数只读取该结果，不创建第二个本地审批状态。
    """
    from openprogram.agent.run_control import get_preapproved_wait_id
    preapproved_wait_id = get_preapproved_wait_id()
    if preapproved_wait_id:
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore, WaitStatus

        wait = DurableWaitStore(default_store()).get_wait(preapproved_wait_id)
        if wait is None or wait.kind != "approval":
            raise RuntimeError("preapproved wait is unavailable")
        execution = default_store().get_execution(wait.execution_id)
        from openprogram.agent.run_control import get_current_execution_id
        current_execution = get_current_execution_id()
        if (execution is None or execution.session_id != req.session_id
                or wait.request.get("tool") != tool_name
                or (current_execution and current_execution != wait.execution_id)
                or (tool_call_id and wait.request.get("tool_call_id") != tool_call_id)):
            return False, "approval does not authorize this operation", "once"
        if wait.status is WaitStatus.RESOLVED:
            value = wait.answer
            answer, scope = (
                (value.get("answer"), value.get("scope", "once"))
                if isinstance(value, dict) else (value, "once")
            )
            allowed = (
                answer.strip() in ("允许", "approve", "yes", "y", "true", "ok", "是")
                if isinstance(answer, str) else bool(answer)
            )
            return allowed, None, (
                scope if scope in ("once", "always", "always_path") else "once"
            )
        if wait.status in {WaitStatus.DECLINED, WaitStatus.EXPIRED, WaitStatus.CANCELLED}:
            return False, None, "once"
        raise RuntimeError("approval continuation has no durable outcome")

    raise RuntimeError(
        "tool approval requires a pre-dispatch durable wait safe point"
    )

"""Shared operation decisions, permission rules and hard constraints."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest


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
        from openprogram.agent.permissions.classifier import SAFE_AUTO_ALLOWLIST
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

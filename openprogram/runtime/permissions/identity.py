"""The identity gate: what someone other than the owner may do.

It is the first step of ``permission_decision``. The capability table decides
what a tier may do at all; the scope limits below hold what a turn someone else
started may reach within that; a page script runs only in work the owner
started; work that came from text the owner did not write runs no page script
and no destructive delete. None of it depends on the permission mode, so
Bypass does not widen it.
"""
from __future__ import annotations

import re
from typing import Any

from openprogram.runtime.permissions import refusal
from openprogram.runtime.permissions.refusal import Decision

# High-impact tools an external turn may not run at all.
RISKY_TOOLS = {"bash", "exec", "shell", "execute_code", "write_stdin"}
# Non-interactive worktree side effects mutate repository state outside the
# spawned agent's working directories, and those turns cannot ask approval.
WORKTREE_TOOLS = {
    "worktree_create", "worktree_merge", "worktree_discard", "worktree_keep",
}
WRITE_TOOLS = {"write", "write_file", "edit", "edit_file"}
PATCH_PATH_PREFIXES = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)
SCHEDULED_MEMORY_TOOLS = {"memory_search", "memory_get", "memory_update"}
# The speaker of an always-on check-in's turn, and the tools it reads paths with.
CHECK_IN_SPEAKER = "runtime/always_on"
SCHEDULER_SPEAKER = "runtime/scheduler"
# Goals no person asked for: what started them read text the owner did not write.
AUTOMATIC_GOAL_ORIGINS = ("check_in", "handoff", "scheduled")
# ``TurnRequest.automation`` of a turn that receives a handoff message (📨). Its
# answer goes back to the sender, so it is not sent to the chat's bound channel.
HANDOFF_MESSAGE = "handoff_message"
HANDOFF_TURNS = ("handoff", HANDOFF_MESSAGE)
# ``TurnRequest.automation`` of work an automatic Goal's turn started for itself.
FROM_AUTOMATIC_GOAL = "automatic_goal"
# A guard against mistakes on the command text, not a security boundary.
_DESTRUCTIVE_COMMAND = re.compile(
    r"(?:^|[\s;&|(`/\\])(?:rm|rmdir|unlink|shred)(?:\s|$)"
    r"|\bfind\b[^;|&]*\s-delete\b"
    r"|\bgit\s+clean\b|\bgit\s+branch\s+(?:-d|--delete)\b"
    r"|\bkubectl\s+delete\b|\bterraform\s+destroy\b"
    r"|\bdrop\s+(?:database|schema|table)\b|\btruncate\s+table\b",
    re.IGNORECASE,
)
READ_PATH_TOOLS = {"read", "grep", "glob", "list"}


def unattended(req) -> bool:
    """A turn someone other than the owner started: an outside MCP client, or
    an agent or schedule someone else set up. The owner's own are not."""
    tier = getattr(req, "authority_tier", None)
    if req.source == "mcp":
        return True
    if req.source == "agent_spawn":
        return tier != "owner"
    return req.source in {"cron", "scheduler"} and tier == "paired"


def check_in(req) -> bool:
    """An always-on check-in. It carries the owner's authority, but nobody is
    watching what it reads, so it keeps off the owner's secrets."""
    return getattr(req, "speaker_id", None) == CHECK_IN_SPEAKER


def ends_with_its_run(req) -> bool:
    """A run that starts clean and ends with its answer: a scheduled run or a
    check-in. What it opened for itself (a page) is of no use to the next one."""
    return check_in(req) or getattr(req, "source", None) in {"cron", "scheduler"}


def in_handoff_chain(req) -> bool:
    """A turn that delivers a handoff or its reply."""
    return getattr(req, "automation", None) in HANDOFF_TURNS


def detached_from_window(req) -> bool:
    """A turn no window started, so the page in front is not its to use: a
    Goal's own turn, a handoff, a check-in, a scheduled run, a chat-app message,
    and what any of those starts for itself. It opens its own tab or names one.

    The runtime's follow-ups of a person's chat (a stop hook, a merge, a job's
    notice), a sub-agent of a person's turn and an MCP client are not detached,
    though they are non-interactive too.
    """
    if req is None:
        return False
    return bool(
        getattr(req, "detached", False)
        or getattr(req, "goal_trigger", False)
        or getattr(req, "automation", None)
        or getattr(req, "background", False)
        or getattr(req, "peer_kind", None)
        or getattr(req, "source", None) in {"cron", "scheduler"}
    )


def automatic_goal_origin(req) -> str:
    """Where a Goal this turn creates comes from; empty when a person asked for it."""
    speaker = getattr(req, "speaker_id", None)
    if speaker == CHECK_IN_SPEAKER:
        return "check_in"
    if speaker == SCHEDULER_SPEAKER:
        return "scheduled"
    return "handoff" if in_handoff_chain(req) else ""


def outside_text(req) -> bool:
    """The turn carries the owner's full power but works from text the owner
    did not write: a handoff chain, or the turns of an automatic Goal. A person
    typing in that conversation is not this."""
    if in_handoff_chain(req) or getattr(req, "automation", None) == FROM_AUTOMATIC_GOAL:
        return True
    if not getattr(req, "goal_trigger", False):
        return False
    import openprogram.programs.tools.planning.goal as goals
    goal = goals.load_goal(req.session_id)
    return bool(goal) and goal.get("origin") in AUTOMATIC_GOAL_ORIGINS


def inherited_automation(req) -> str | None:
    """The mark of work ``req`` starts for itself: a sub-agent, a nested
    program or a notice turn comes from the same text as the turn behind it.
    Only the turn receiving a handoff message owes its answer to the sender,
    so what it starts is an ordinary turn of the chain."""
    if req is None or not outside_text(req):
        return None
    automation = getattr(req, "automation", None)
    return "handoff" if automation == HANDOFF_MESSAGE else automation or FROM_AUTOMATIC_GOAL


def destructive_delete(name: str, args: dict | None) -> bool:
    """A call that deletes: a patch removing a file, or a delete command."""
    if not isinstance(args, dict):
        return False
    if name == "apply_patch":
        return any(line.startswith("*** Delete File: ")
                   for line in str(args.get("patch") or "").splitlines())
    if name == "worktree_discard":
        return True
    if name not in RISKY_TOOLS:
        return False
    return any(isinstance(args.get(key), str) and _DESTRUCTIVE_COMMAND.search(args[key])
               for key in ("command", "cmd", "code", "chars"))


def page_script(name: str, args: dict | None) -> bool:
    """A page script reads everything a signed-in page holds, including what
    observations redact. It is part of operating that browser: the grant that
    lets a conversation click in a browser lets it run scripts there, and a
    card that asks about one shows the script untruncated. Where nobody
    attends, it does not run (see ``unattended``), as an action or from code."""
    if name == "computer_use_agent":
        from openprogram.programs.tools.computer_use.computer_use_agent import evaluates
        return evaluates(args)
    return False


def path_is_safe(tool_name: str, args: dict, req: Any) -> bool:
    """Whether a write target stays in the working directories and off the
    dangerous files and directories; the full rules are in file_safety."""
    import os
    from openprogram.runtime.permissions.rule import parse_command
    from openprogram.programs.tools.files.file_safety import check_path_safety
    from openprogram.state.worktree.context import current_worktree_path
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


def audit_path(req: Any, tool_name: str, reason: str) -> None:
    from openprogram.runtime.permissions.audit import log_path_safety_violation
    log_path_safety_violation(req.session_id or "", tool_name, reason)


def check_in_read_refusal(tool_name: str, args: dict, req: Any) -> refusal.Refusal | None:
    """The refusal for a check-in naming a dangerous path, or ``None``."""
    if tool_name not in READ_PATH_TOOLS or not check_in(req) or not isinstance(args, dict):
        return None
    from openprogram.programs.tools.files.file_safety import check_in_read_refusal as reason_for
    for raw in (args.get("path"), args.get("file_path")):
        reason = reason_for(raw, req) if isinstance(raw, str) and raw.strip() else None
        if reason:
            audit_path(req, tool_name, "protected read target")
            return refusal.check_in_read(tool_name, args, reason)
    return None


def scope_violation(tool_name: str, args: dict, req: Any) -> str | None:
    """What a turn someone else started may not do with this call, or ``None``."""
    # The owner's own work, scheduled or delegated, runs under its
    # conversation's permission mode like any other turn. What stays limited
    # below is what someone else started.
    if not unattended(req):
        return None
    if tool_name in SCHEDULED_MEMORY_TOOLS and req.source in {"cron", "scheduler"}:
        return "run the Memory lifecycle"
    if tool_name in WORKTREE_TOOLS or tool_name in RISKY_TOOLS:
        return f"execute {tool_name}"
    if tool_name in WRITE_TOOLS:
        raw = (args.get("path") or args.get("file_path")) if isinstance(args, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            audit_path(req, tool_name, "missing write target")
            return f"call {tool_name} without an explicit target path"
        if not path_is_safe(tool_name, args, req):
            audit_path(req, tool_name, "unsafe write target")
            return "write outside its working directories"
        return None
    if tool_name != "apply_patch":
        return None

    patch = args.get("patch") if isinstance(args, dict) else None
    if not isinstance(patch, str):
        return None
    for line in patch.splitlines():
        prefix = next((p for p in PATCH_PATH_PREFIXES if line.startswith(p)), None)
        if prefix is None:
            continue
        path = line[len(prefix):].strip()
        if not path:
            audit_path(req, "apply_patch", "empty patch target")
            return "apply a patch with an empty target path"
        if not path_is_safe("write", {"file_path": path}, req):
            audit_path(req, "apply_patch", "unsafe patch target")
            return "apply a patch outside its working directories"
    return None


def decide(tool, req, args) -> Decision | None:
    """Refuse a call the turn's identity does not cover; ``None`` lets it continue."""
    from openprogram.runtime.permissions.tool_authority import decide_tool_authority
    name = tool.name
    authority = decide_tool_authority(req, name, args)
    if not authority.allowed:
        room = (getattr(req, "peer_kind", None) or "direct") != "direct"
        return _denied(refusal.authority(name, args, authority, getattr(req, "speaker_display", None), room=room),
                       authority)
    read_refused = check_in_read_refusal(name, args, req)
    if read_refused:
        return _denied(read_refused)
    action = scope_violation(name, args, req)
    if action:
        return _denied(refusal.scope(name, args, req.source, action))
    scripts = page_script(name, args)
    if scripts and unattended(req):
        return _denied(refusal.page_script_denial(name, args))
    if (scripts or destructive_delete(name, args)) and outside_text(req):
        return _denied(refusal.outside_text(name, args))
    return None


def _denied(item: refusal.Refusal, authority: object | None = None) -> Decision:
    return Decision("deny", item.code, "identity", refusal=item, authority=authority)


__all__ = [
    "AUTOMATIC_GOAL_ORIGINS", "CHECK_IN_SPEAKER", "FROM_AUTOMATIC_GOAL", "SCHEDULER_SPEAKER", "automatic_goal_origin", "destructive_delete", "detached_from_window", "ends_with_its_run", "in_handoff_chain", "inherited_automation", "outside_text", "PATCH_PATH_PREFIXES", "READ_PATH_TOOLS", "RISKY_TOOLS", "SCHEDULED_MEMORY_TOOLS",
    "WORKTREE_TOOLS", "WRITE_TOOLS", "audit_path", "check_in", "check_in_read_refusal", "decide", "page_script", "path_is_safe", "scope_violation", "unattended",
]

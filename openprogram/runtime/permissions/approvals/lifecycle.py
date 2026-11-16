"""Live operation snapshots and canonical approval-wait reconciliation."""
from __future__ import annotations

import copy
import dataclasses
import os

from openprogram.runtime.permissions.state import permission_state
from openprogram.state.worktree.context import current_worktree_path


def current_permission_request(request):
    """Snapshot the live override only for its authenticated local owner."""
    req = copy.copy(request)
    if req.source not in {"web", "tui", "acp"} or req.authority_tier != "owner" or req.interaction != "interactive":
        return req
    branch = getattr(req, "branch_id", None)
    branch_args = {"branch_id": branch} if branch else {}
    state = permission_state(req.session_id, **branch_args)
    if state.get("principal_id") == req.principal_id:
        req.permission_mode = state["mode"]
        req._permission_version = state["version"]
    # Project rule edits apply at the next operation boundary, including
    # revocations made in History while this owner turn is running.
    from openprogram.runtime.permissions.rule import load_merged_rules
    req.permission_rules = load_merged_rules(req.session_id, **branch_args)
    req.permission_rules = copy.deepcopy(req.permission_rules)
    return req


def policy_changed(before, after, tool_name: str) -> bool:
    """Whether the policy that decides a call of ``tool_name`` differs between two requests.

    Mode and version decide every call; of the rules, only those naming this
    tool do. A rule added or removed for another tool meanwhile (an agent
    started with a computer_use_agent grant takes it back when it ends) leaves
    the call's decision as reviewed.
    """
    from openprogram.runtime.permissions.policy import rules_for_tool
    if any(getattr(before, key, None) != getattr(after, key, None) for key in ("permission_mode", "_permission_version")):
        return True
    return (rules_for_tool(getattr(before, "permission_rules", None), tool_name)
            != rules_for_tool(getattr(after, "permission_rules", None), tool_name))


def spawn_permission_snapshot(store, parent, child):
    """Freeze policy only from the exact live, same-principal owner parent.

    Caller-supplied parent IDs, source labels and session settings alone cannot
    confer approval policy. Recovery retains the already admitted input.
    """
    from types import SimpleNamespace
    from openprogram.state.authority import normalize_authority
    from openprogram.runtime.permissions.modes import VALID_PERMISSION, PermissionRules
    from openprogram.runtime.permissions.session_facts import facts

    if (parent is None or child.source != "agent_spawn"
            or facts().current_execution_id() != parent.execution_id
            or facts().current_session_id() != parent.session_id
            or parent.status.value != "running" or parent.current_attempt_id is None
            or (child.caller_session_id or child.parent_session_id) != parent.session_id):
        return None
    payload = store.get_job_agent_input(parent.execution_id)
    if payload is not None:
        values = payload["turn_request"]
    else:
        payload = store.get_agent_turn_input(parent.execution_id)
        if payload is None or payload.get("kind") != "chat":
            return None
        values = payload["request"]
    parent_authority = normalize_authority(values)
    child_authority = normalize_authority(child)
    if (not parent_authority or not child_authority
            or parent_authority.get("authority_tier") != "owner"
            or child_authority.get("authority_tier") != "owner"
            or not parent_authority.get("principal_id")
            or parent_authority["principal_id"] != child_authority.get("principal_id")):
        return None
    request_values = copy.deepcopy(dict(values))
    request_values.setdefault("session_id", parent.session_id)
    request_values.setdefault("interaction", parent_authority.get("interaction"))
    request = current_permission_request(SimpleNamespace(**request_values))
    if request.permission_mode not in VALID_PERMISSION:
        raise ValueError("parent permission mode is invalid")
    rules = getattr(request, "permission_rules", None)
    if isinstance(rules, PermissionRules):
        # The snapshot is persisted in the child's Job input, which is JSON.
        rules = dataclasses.asdict(rules)
    snapshot = {"mode": request.permission_mode, "rules": copy.deepcopy(rules)}
    # The folders and Sandbox the caller's turn runs with travel too, so the
    # sub-agent's own Agent can only narrow them (permissions/delegation.py).
    dirs = values.get("additional_working_dirs")
    if isinstance(dirs, list) and dirs:
        snapshot["additional_working_dirs"] = [str(path) for path in dirs]
    enabled = _caller_sandbox(values, parent.session_id)
    if isinstance(enabled, bool):
        snapshot["sandbox_enabled"] = enabled
    return snapshot


def _caller_sandbox(values, session_id):
    """The Sandbox the caller's stored turn ``values`` run with, or ``None`` (the global default).

    A turn that left it to its project (``None``) is resolved here, against
    the caller's conversation, or a sub-agent in another conversation would
    read that conversation's project instead.
    """
    if not values.get("sandbox_config_frozen"):
        return None
    enabled = values.get("sandbox_enabled")
    if enabled is None:
        # The project's own switch, as session_config.sandbox_override_from_config
        # reads it for a turn that set none; read from the project store here
        # because permissions sit below the conversation package.
        try:
            from openprogram.host.sandbox import MODE_WORKSPACE_WRITE
            from openprogram.state.store.project import project_store
            project = project_store.project_for_session(session_id) or project_store.get_default_project()
            mode = project_store.load_project_settings(project.id).get("sandbox_mode")
            enabled = str(mode).strip().lower() == MODE_WORKSPACE_WRITE if mode else None
        except Exception:  # noqa: BLE001 - no project setting: the global default applies to both
            enabled = None
    return enabled if isinstance(enabled, bool) else None


def _answers_questions(values) -> bool:
    """Whether a person is behind the stored turn ``values`` to answer an approval."""
    route = values.get("asks_through")
    if isinstance(route, dict) and route.get("session_id"):
        return True
    return (values.get("interaction") == "interactive" and values.get("speaker_kind") == "owner"
            and not values.get("detached"))


def spawn_question_route(store, parent, child, snapshot):
    """Where a foreground sub-agent's approval questions are shown, or ``None``.

    A sub-agent its caller waits on (``agent`` in the foreground, forked or
    fresh) in the caller's own conversation asks there, through the
    caller's existing approval card, named after the sub-agent: Claude
    Code's foreground subagents and its fork (``permissionMode: 'bubble'``)
    bubble their prompts up to the leader the same way. A foreground
    sub-agent forked into another conversation (``start_from="SID:MSG"``)
    asks in its caller's conversation too: its card is shown there
    (``asked_in_session``, ``waits.list_open``). Nobody waits on a
    background sub-agent or a delivered message, so those keep ``None`` and
    what would ask is refused at once (``refusal.helper_cannot_ask``).

    Only a caller whose policy froze into ``snapshot`` (the live
    same-principal owner parent) and that can answer itself qualifies.
    """
    if (snapshot is None or parent is None or child.source != "agent_spawn" or not child.wait
            or child.message_origin
            or (child.caller_session_id or child.parent_session_id) != parent.session_id):
        return None
    payload = store.get_job_agent_input(parent.execution_id)
    if payload is not None:
        values = payload["turn_request"]
    else:
        payload = store.get_agent_turn_input(parent.execution_id)
        if payload is None or payload.get("kind") != "chat":
            return None
        values = payload["request"]
    if not _answers_questions(values):
        return None
    label = str(child.label or child.subject or child.agent_id or "helper").strip()[:40]
    return {"session_id": parent.session_id, "execution_id": parent.execution_id, "label": label}


def wrap_live_permission(tool, request, on_event):
    """Pin a decision per operation, without modifying the admission request.

    A safe-point suspension leaves at most one pending wrapper per tool.
    Continuation rebuilds it using the latest confirmed policy.
    """
    from openprogram.runtime.permissions.approvals.approval import wrap_with_approval
    pending = None
    # The desktop operations whose macOS grants this turn has found: its first
    # application step probes for them, the later ones do not (see
    # system_access.required_access_state).
    access_found: set[str] = set()
    wrapped = copy.copy(tool)

    async def preflight(call_id, args, *, operation_id=None):
        nonlocal pending
        import copy
        snapshot = copy.deepcopy(current_permission_request(request))
        reviewed_args = copy.deepcopy(args)
        working_dir = current_worktree_path() or os.getcwd()
        inner = wrap_with_approval(tool, snapshot, on_event, _live=False, _access_found=access_found)
        await inner._permission_preflight(call_id, reviewed_args, operation_id=operation_id)
        pending = (call_id, inner, reviewed_args, snapshot, working_dir)

    def manifest(call_id, args):
        nonlocal pending
        if pending is not None and pending[0] == call_id:
            if pending[2] != args:
                return None
            inner = pending[1]
        else:
            snapshot = current_permission_request(request)
            inner = wrap_with_approval(tool, snapshot, on_event, _live=False, _access_found=access_found)
            import copy
            pending = (call_id, inner, copy.deepcopy(args), snapshot, current_worktree_path() or os.getcwd())
        return inner._interaction_manifest(call_id, args)

    async def execute(call_id, args, cancel, on_update):
        nonlocal pending
        if pending is None or pending[0] != call_id:
            await preflight(call_id, args)
        operation = pending
        pending = None
        if operation is not None and operation[0] == call_id:
            current = current_permission_request(request)
            stale = operation[2] != args or operation[4] != (current_worktree_path() or os.getcwd())
            if not stale and policy_changed(operation[3], current, tool.name):
                # The policy moved while this operation waited (the owner switched
                # mode during an approval, say). The review stands only where the
                # policy now allows the call outright; anything else reviews anew.
                from openprogram.runtime.permissions.policy import permission_decision
                from openprogram.runtime.permissions.approvals.approval import wrap_with_approval
                stale = permission_decision(tool, current, args).outcome != "allow"
                if not stale:
                    operation = (*operation[:1],
                                 wrap_with_approval(tool, current, on_event, _live=False,
                                                    _access_found=access_found),
                                 *operation[2:])
            if stale:
                from openprogram.runtime.permissions import refusal
                return refusal.tool_result(refusal.stale(
                    "PERMISSION_REVIEW_STALE", "Permission policy or arguments changed during review; submit a new operation."))
            inner = operation[1]
        return await inner.execute(call_id, args, cancel, on_update)

    def visible():
        from openprogram.programs import _unsafe_in_for
        current = current_permission_request(request)
        if not epoch_visible():
            return False
        if tool.name == "exit_plan_mode":
            return current.permission_mode == "plan"
        return not (current.permission_mode == "plan" and "plan" in _unsafe_in_for(tool.name))

    def epoch_visible():
        """Whether the tool goes in the tools array of a new epoch: every
        call of it refused (a whole-tool deny rule, an identity without its
        capability) leaves it out. The mode does not: plan mode keeps the
        array as it is and refuses the calls (``permission_decision``), as
        Codex does, so entering or leaving it changes no request bytes."""
        from openprogram.runtime.permissions.rule import parse_rule
        from openprogram.runtime.permissions.tool_authority import lacks_static_capability
        current = current_permission_request(request)
        if lacks_static_capability(current, tool.name):
            return False
        rules = getattr(current, "permission_rules", None)
        return not any(rule.tool_name == tool.name and rule.pattern in (None, "")
                       for rule in (parse_rule(raw) for raw in (getattr(rules, "deny", None) or ())))

    wrapped._permission_visible = visible
    wrapped._permission_epoch_visible = epoch_visible
    wrapped._permission_managed = True
    wrapped.execute = execute
    wrapped._permission_preflight = preflight
    wrapped._interaction_manifest = manifest
    return wrapped


async def reconcile_permission_waits(session_id: str, *, service=None, force: bool = False) -> None:
    """Resume every approval the current policy no longer asks about, through canonical wait commands.

    ``force`` re-decides an owner's wait even though the conversation's
    permission version has not moved: the policy changed underneath it, as in
    the one-time open-permissions migration. Only a decision of ``allow``
    resumes such a wait.
    """
    from types import SimpleNamespace
    from openprogram.runtime.permissions.authority import LOCAL_OWNER_SPEAKER
    from openprogram.runtime.permissions.policy import permission_decision
    from openprogram.runtime.permissions.session_facts import facts
    from openprogram.runtime.execution import default_control_service
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.execution.model import CommandStatus
    from openprogram.runtime.execution.store import ExecutionConflict
    from openprogram.runtime.execution.waits import DurableWaitStore
    from openprogram.state.worktree.context import set_worktree, reset_worktree

    service = service or default_control_service()
    store = service.executions
    for wait in DurableWaitStore(store).list_open(session_id=session_id):
        if wait.kind != "approval":
            continue
        execution = store.get_execution(wait.execution_id)
        if execution is None or store.get_agent_turn_input(wait.execution_id) is None:
            continue
        if facts().turn_running(session_id, wait.execution_id):
            # The driver repeats reconciliation after its old frame exits.
            continue
        request = facts().turn_request_for(store, execution)
        current = current_permission_request(request)
        version = getattr(current, "_permission_version", 0)
        if wait.request.get("approval_reason") == "PLAN_EXIT":
            # Leaving Plan by the selector approves no plan: only the owner's answer does.
            continue
        if force:
            # Only the owner at this machine: a chat turn's stored tier dates
            # from when any approved sender counted as the owner.
            if current.authority_tier != "owner" or getattr(current, "speaker_id", None) != LOCAL_OWNER_SPEAKER:
                continue
        elif version <= wait.request.get("permission_version", 0):
            continue
        tool = SimpleNamespace(name=wait.request.get("tool"), _accept_edits_safe=wait.request.get("accept_edits_safe", False))
        with facts().bind_execution(execution.execution_id):
            token = set_worktree(wait.request.get("working_dir"))
            try:
                decision = permission_decision(tool, current, dict(wait.request.get("args") or {})).outcome
            finally:
                reset_worktree(token)
        if decision != "allow" and (force or not (decision == "auto" and wait.request.get("approval_reason") == "MODE_APPROVAL")):
            continue
        try:
            dispatch = await service.request_wait_answer(
                command_id=f"permission:{wait.wait_id}:{version}", execution_id=wait.execution_id,
                expected_version=execution.status_version, actor={
                    "principal_id": current.principal_id, "authority_tier": "owner",
                    "surface": "permission-setting", "permission_version": version,
                }, wait_id=wait.wait_id, generation=wait.claim_generation,
                answer={"answer": "approve", "scope": "once"},
            )
            if dispatch.command.status is CommandStatus.APPLIED:
                emit_ws_frame({"type": "question.replied", "data": {
                    "id": wait.wait_id, "session_id": session_id,
                    "execution_id": wait.execution_id,
                }})
        except ExecutionConflict:
            # A concurrent answer, cancellation, or timeout owns the outcome.
            continue

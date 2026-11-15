"""Read-only foreground controls derived from durable Agent executions."""
from __future__ import annotations

from .model import ExecutionRecord, ExecutionStatus, TERMINAL_EXECUTION_STATUSES
from .turn_effects import owns_conversation_effects

# A turn paused at one of these waits is still the conversation's turn: the
# user answers its question or approval and the same execution continues.
# Any other pause (an explicit Pause) hands the conversation back.
WAITING_PAUSE_REASONS = frozenset({"wait_open", "system_access_required"})

#: Sources that are chat channels.
CHANNEL_SOURCES = frozenset({"telegram", "discord", "slack", "wechat"})
# A chat app's turn is a foreground turn like a typed one. When it pauses at a
# question or approval its receiving thread ends and frees the run slot, so
# only this record keeps another turn from starting on top of it.
FOREGROUND_SOURCES = frozenset({"web", "tui", "acp"}) | CHANNEL_SOURCES


def foreground_task_snapshot(store, execution: ExecutionRecord) -> dict | None:
    """Return controls for an active interactive turn, never a background Job."""
    if execution.status in TERMINAL_EXECUTION_STATUSES:
        return None
    if (execution.status is ExecutionStatus.PAUSED
            and execution.reason_code not in WAITING_PAUSE_REASONS):
        return None
    if (execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
            and execution.current_attempt_id is None):
        return None
    payload = store.get_agent_turn_input(execution.execution_id)
    if payload is None:
        return None
    request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
    if request.get("source") not in FOREGROUND_SOURCES:
        return None
    if request.get("interaction") not in {None, "interactive"}:
        return None
    source = store.get_execution_input(execution.execution_id)
    if source is None or not source.user_message_id:
        return None
    return {
        "session_id": execution.session_id,
        "msg_id": source.user_message_id,
        "execution_id": execution.execution_id,
        "status_version": execution.status_version,
        "status": execution.status.value,
        "reason_code": execution.reason_code,
        "func_name": "agent" if payload.get("kind") == "chat" else payload.get("tool_name", "function"),
        "started_at": execution.created_at,
        "branch_id": request.get("branch_id"),
        # Frozen at admission: whether the turn carries the conversation's
        # effects. Which branch it runs on is ``branch_id``; whether that
        # branch is the selected one is decided by the reader, at read time.
        "side_branch": not owns_conversation_effects(request),
    }


def on_selected_branch(task: dict, selected_branch: str | None) -> bool:
    """Whether a turn runs on the branch selected now.

    A turn keeps the branch it was admitted on, so after the person
    switches branches the selected branch's turn is the one whose
    ``branch_id`` is the new selection. With no selection to compare (a
    caller that does not know it) or a turn admitted without a branch, the
    flag frozen at admission answers.
    """
    if selected_branch and task.get("branch_id"):
        return task["branch_id"] == selected_branch
    return not task["side_branch"]


def active_foreground_task(store, session_id: str, *,
                           selected_branch: str | None = None) -> dict | None:
    """The selected branch's running turn: the newest admitted foreground
    turn on ``selected_branch``. Without it, the newest one that carries the
    conversation's effects (``owns_conversation_effects``), whatever its branch."""
    for execution in reversed(store.list_nonterminal(session_id=session_id)):
        task = foreground_task_snapshot(store, execution)
        if task is not None and on_selected_branch(task, selected_branch):
            return task
    return None


def side_branch_tasks(store, session_id: str, *,
                      selected_branch: str | None = None) -> list[dict]:
    """The running turns on branches that are not selected, one per branch."""
    tasks: dict[str, dict] = {}
    for execution in store.list_nonterminal(session_id=session_id):
        task = foreground_task_snapshot(store, execution)
        if task is not None and task.get("branch_id") and not on_selected_branch(task, selected_branch):
            tasks[task["branch_id"]] = task
    return list(tasks.values())


def open_root_turns(store, session_id: str) -> list[dict]:
    """Every unfinished top-level execution of a conversation, foreground or
    not (a paused turn, a scheduled run, a queued Job), with its branch and
    the ids a Stop names. A rewind that changes files is refused while any
    of these runs on another branch."""
    turns: list[dict] = []
    for execution in store.list_nonterminal(session_id=session_id):
        if execution.parent_execution_id:
            continue
        payload = store.get_agent_turn_input(execution.execution_id) or {}
        request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
        request = request if isinstance(request, dict) else {}
        source = store.get_execution_input(execution.execution_id)
        turns.append({
            "session_id": execution.session_id,
            "execution_id": execution.execution_id,
            "status_version": execution.status_version,
            "status": execution.status.value,
            "reason_code": execution.reason_code,
            "branch_id": request.get("branch_id"),
            "msg_id": (source.user_message_id if source is not None else None) or "",
            "source": request.get("source"),
        })
    return turns


def branch_has_open_execution(store, session_id: str, branch_id: str) -> bool:
    """Whether any unfinished turn of this conversation runs on ``branch_id``,
    a scheduled run, a Job or a paused turn included."""
    for execution in store.list_nonterminal(session_id=session_id):
        payload = store.get_agent_turn_input(execution.execution_id) or {}
        request = payload.get("request", {}) if payload.get("kind") == "chat" else payload
        if isinstance(request, dict) and request.get("branch_id") == branch_id:
            return True
    return False

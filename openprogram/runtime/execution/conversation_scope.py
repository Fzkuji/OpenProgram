"""Exact execution membership derived from immutable conversation provenance."""
from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from .authorization import ExecutionAuthorizationError, authorize_session_action
from .model import ExecutionRecord


def _caller_session_hint(payload_json: str) -> Any:
    """The session id a stored Agent payload names as its Job's caller, if readable."""
    try:
        value = json.loads(payload_json)
    except (ValueError, RecursionError):
        return None
    for key in ("job_context", "caller", "session_id"):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def conversation_execution_scope(store: Any, session_id: str, *, consulted: set[str] | None = None):
    """Include direct runs, exact called Jobs and descendants, never whole target sessions.

    Admission rows never change, so the answer for the executions that exist
    stays the same until one is admitted or removed, unless a session graph
    was read to place a called Job: a graph still grows. ``consulted``
    collects the sessions whose graph was read, for callers that reuse the
    answer.
    """
    from .job_input import JobAgentInputV1
    from .store import ExecutionConflict

    store.tables.ensure_session(session_id)
    with closing(store._connect()) as connection, store.tables.lock:
        executions = {row["execution_id"]: store._record(row) for row in connection.rows(
            "executions", order=("-created_at", "execution_id"),
        )}
        inputs = {row["execution_id"]: row for row in connection.rows("execution_inputs")}
        # These untrusted hints only select candidates. Full immutable input
        # integrity/schema checks happen below, for relevant sessions only.
        caller_hints = {
            row["execution_id"]: _caller_session_hint(row["payload_json"])
            for row in connection.rows("execution_agent_turn_inputs")
        }
        caller_sessions = {key: hint for key, hint in caller_hints.items() if isinstance(hint, str)}

    anchor_owners = {}
    for key, record in inputs.items():
        for anchor in (record["user_message_id"], record["assistant_message_id"]):
            if anchor:
                anchor_owners.setdefault((record["session_id"], anchor), set()).add(key)
    assistant_owners = {}
    for key, record in inputs.items():
        if record["assistant_message_id"]:
            assistant_owners.setdefault((record["session_id"], record["assistant_message_id"]), set()).add(key)
    anchor_owners.update(assistant_owners)
    graphs = {}

    def caller_owners(caller):
        """Walk only the caller's own DAG ancestry, stopping at the first turn anchor."""
        from openprogram.state.store.session.nodes import ROLE_USER, ROLE_LLM

        sid = caller["session_id"]
        found = set()
        pending = [caller["msg_id"], caller["node_id"]]
        seen = set()
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            owners = anchor_owners.get((sid, node_id))
            if owners:
                found.update(owners)
                continue
            if sid not in graphs:
                if consulted is not None:
                    consulted.add(sid)
                from openprogram.runtime.context.session_db import default_db
                from openprogram.state.store import SessionNodeWriter

                try:
                    graphs[sid] = SessionNodeWriter(default_db(), sid).load().nodes
                except (OSError, ValueError, KeyError):
                    graphs[sid] = {}
            node = graphs[sid].get(node_id)
            if node is None or node.role in {ROLE_USER, ROLE_LLM}:
                continue
            # A function's caller is its owning turn; predecessor is used
            # only when no explicit caller was saved. Never follow both.
            ancestor = node.caller or node.predecessor
            if ancestor:
                pending.append(ancestor)
        return found

    caller_parents = {}
    checked_callers = set()
    included = {key for key, execution in executions.items() if execution.session_id == session_id}
    while True:
        # Canonical ancestry needs neither payload parsing nor a session DAG.
        additions = {
            key for key, execution in executions.items()
            if key not in included and execution.parent_execution_id in included
        }
        relevant_sessions = {session_id} | {executions[key].session_id for key in included}
        for key, caller_session in caller_sessions.items():
            if key in checked_callers or caller_session not in relevant_sessions or key not in executions:
                continue
            if executions[key].parent_execution_id in included:
                checked_callers.add(key)
                continue
            checked_callers.add(key)
            try:
                payload = store.get_job_agent_input(key)
                if payload is None:
                    continue
                job = JobAgentInputV1.parse(payload)
                if job.turn_request["session_id"] != executions[key].session_id:
                    continue
                caller = job.job_context["caller"]
                if caller is None or caller["session_id"] != caller_session:
                    continue
            except (ExecutionConflict, ValueError, TypeError, KeyError):
                continue
            caller_parents[key] = caller_owners(caller) - {key}
            if caller_session == session_id:
                additions.add(key)
        additions.update(
            key for key, owners in caller_parents.items()
            if key not in included and len(owners) == 1 and owners & included
        )
        additions.difference_update(included)
        if not additions:
            parents = {}
            for key in included:
                parent = executions[key].parent_execution_id
                if parent in included and parent != key:
                    parents[key] = parent
                    continue
                matches = (caller_parents.get(key, set()) & included) - {key}
                parents[key] = next(iter(matches)) if len(matches) == 1 else None
            return tuple(execution for key, execution in executions.items() if key in included), parents
        included.update(additions)


def conversation_executions(store: Any, session_id: str) -> tuple[ExecutionRecord, ...]:
    return conversation_execution_scope(store, session_id)[0]


def conversation_parent_ids(store: Any, session_id: str) -> dict[str, str | None]:
    """Conversation display links only; ambiguous callers remain roots."""
    return conversation_execution_scope(store, session_id)[1]


def authorize_conversation_execution(
    actor: Any, action: str, execution: ExecutionRecord, *, store: Any,
    session_id: str, bound_session: str | None = None,
):
    """Authorize through the caller conversation without changing the target identity."""
    from .public import project_id_for_session

    if (bound_session is not None and bound_session != session_id) or not any(
        item.execution_id == execution.execution_id and item.session_id == execution.session_id
        for item in conversation_executions(store, session_id)
    ):
        raise ExecutionAuthorizationError("execution is not visible")
    return authorize_session_action(actor, action, {
        "project_id": project_id_for_session(session_id), "session_id": session_id,
    })

"""Display projections for canonical verifier turns; never rewrite evidence."""
from __future__ import annotations


def history_projection(goal: dict | None, session_id: str) -> dict | None:
    """Project legacy terminal state without changing the captured session."""
    if goal is None:
        return None
    from .state import _end_records
    import openprogram.programs.tools.planning.goal as goals
    if (goal.get("status") not in {"achieved", "impossible", "cancelled", "cleared"}
            or any(row["goal"].get("goal_id") == goal.get("goal_id")
                   for row in goal.get("end_records", []))):
        return goal
    # Legacy Goals lack an ending anchor. Derive it from the saved terminal
    # time, never from the current head, which moves with subsequent chat.
    ended_at = goal.get("updated_at") or goal.get("created_at") or 0
    messages = goals._db().get_messages(session_id)
    eligible = [row for row in messages
                if row.get("role") in {"user", "assistant"} and not row.get("caller")
                and (row.get("timestamp") or 0) <= ended_at]
    anchor = max(eligible, key=lambda row: row.get("timestamp") or 0).get("id") if eligible else None
    return dict(goal, end_records=_end_records(goal, goal, anchor))


def candidate_presentation(candidate: dict) -> dict:
    status = candidate.get("status")
    report = candidate.get("report") or {}
    if not isinstance(report, dict):
        report = {}
    labels = {row["id"]: row.get("text", row["id"])
              for row in candidate.get("requirements", [])}
    assessments = report.get("requirements")
    requirements = []
    for row in assessments if isinstance(assessments, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        requirements.append({
            "id": row["id"], "text": labels.get(row["id"], row["id"]),
            "verdict": row.get("verdict") if row.get("verdict") in ("met", "unmet", "unknown") else "unknown",
            "reason": row.get("reason") if isinstance(row.get("reason"), str) else "",
            "evidence": [ref for ref in row.get("evidence", []) if isinstance(ref, str)]
            if isinstance(row.get("evidence"), list) else [],
        })
    return {
        "id": candidate["id"],
        "status": status if status in {"pending", "met", "unmet"} else "unavailable",
        "reason": candidate.get("reason") if isinstance(candidate.get("reason"), str) else "",
        "requirements": requirements,
    }


def goal_message(goal: dict) -> dict | None:
    candidate = goal.get("verification") or {}
    message_id = candidate.get("result_message_id")
    if not candidate.get("id") or not message_id:
        return None
    return {"message_id": message_id, "presentation": candidate_presentation(candidate)}


def verification_executions(session_id: str, row_ids: set) -> dict[str, str]:
    """``{reply id: execution id}`` for the session's goal-verifier turns."""
    from openprogram.runtime.execution import default_store

    store = default_store()
    found: dict[str, str] = {}
    for source in store.list_inputs_for_session(session_id):
        if source.assistant_message_id not in row_ids:
            continue
        payload = store.get_agent_turn_input(source.execution_id) or {}
        identity = (payload.get("request") or {}).get("goal_verification")
        if payload.get("kind") == "chat" and isinstance(identity, str) and identity:
            found[source.assistant_message_id] = source.execution_id
    return found


def annotate_messages(session_id: str, messages: list[dict], *, executions: dict[str, str] | None = None) -> list[dict]:
    """Recognize old and new messages by immutable execution input, not text.

    ``executions`` names the verifier turns among ``messages`` when the caller
    already knows them, so a page need not scan the session's executions.
    """
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.context.session_db import default_db
    from .verification import digest, message_snapshot

    store = default_store()
    # Rendering uses the captured session store; lifecycle reads deliberately
    # invalidate that cache and would invalidate the transcript snapshot too.
    goal = ((default_db().get_session(session_id) or {}).get("extra_meta") or {}).get("goal") or {}
    candidates = {item["id"]: item for item in
                  (goal.get("verification"), goal.get("last_verification"))
                  if isinstance(item, dict) and item.get("id")}
    rows = {row.get("id"): row for row in messages}
    marks = {}
    scanned = store.list_for_session(session_id) if executions is None else filter(
        None, (store.get_execution(execution_id) for execution_id in executions.values()))
    for execution in scanned:
        source = store.get_execution_input(execution.execution_id)
        if source is None or source.assistant_message_id not in rows:
            continue
        payload = store.get_agent_turn_input(execution.execution_id) or {}
        request = payload.get("request") or {}
        identity = request.get("goal_verification")
        if payload.get("kind") != "chat" or not isinstance(identity, str) or not identity:
            continue
        mark = {"id": identity, "status": "pending" if execution.status.value in
                {"queued", "running"} else "unavailable"}
        candidate = candidates.get(identity)
        if candidate and candidate.get("execution_id") == execution.execution_id:
            if candidate.get("status") == "pending" and mark["status"] == "pending":
                mark = candidate_presentation(candidate)
            elif (candidate.get("result_message_id") == source.assistant_message_id
                  and candidate.get("result_sha256") == digest(message_snapshot(rows[source.assistant_message_id]))):
                mark = candidate_presentation(candidate)
        marks[source.assistant_message_id] = mark
    return [dict(row, goal_verification=marks[row["id"]]) if row.get("id") in marks
            else dict(row) for row in messages]


def event_sink(callback, request):
    """Attach identity before the first delta, including restarted verifier turns."""
    identity = request.goal_verification
    if not identity:
        return callback

    def emit(event):
        data = event.get("data") or {}
        if (event.get("type") in {"chat_ack", "chat_response"}
                and data.get("session_id") == request.session_id
                and data.get("msg_id") in {request.user_msg_id, request.user_msg_id + "_reply"}
                and data.get("type") != "user_message"):
            event = dict(event, data=dict(data, goal_verification={"id": identity, "status": "pending"}))
        callback(event)
    return emit

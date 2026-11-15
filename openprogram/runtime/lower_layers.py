"""What the runtime hands down to models, state and the host when it loads.

``openprogram.models``, ``openprogram.state`` and ``openprogram.host`` sit
below the runtime and never import it. The facts they need from it arrive
through ports they own, filled in here:

* ``state.store.current`` — the store the active Context carries;
* ``state.execution_facts`` — the bound session and execution, running
  turns, live work on a session, the saved Agents, the claimed Job;
* ``state.event_sink`` — the event bus;
* ``host.runtime_facts`` — execution records, the worker stopping, the
  foreground turn, the running call and tool call, the turn's source and
  tool policy, a conversation's working directories and App surface, the
  client broadcast, and loading the built-in Programs;
* ``models.providers.recording.capture`` — the named sections of a
  system prompt the runtime composed, for the prompt-cache check.

Each provider imports its runtime module when it is called, never here,
so loading the runtime package stays cheap and a test that replaces a
runtime function (``session_db.default_db``, ``run_control.…``) is what
the lower layers see.
"""
from __future__ import annotations

from openprogram.host import runtime_facts as host_runtime_facts
from openprogram.state import event_sink, execution_facts
from openprogram.state.store.current import register_current_store_resolver


def _current_store():
    from openprogram.runtime.context import session_db

    return session_db.default_db()


def _current_session_id():
    from openprogram.runtime.execution.run_control import get_current_session_id

    return get_current_session_id()


def _current_execution_id():
    from openprogram.runtime.execution.run_control import get_current_execution_id

    return get_current_execution_id()


def _turn_running(session_id: str) -> bool:
    from openprogram.runtime.conversation.run_control import is_turn_running

    return is_turn_running(session_id)


def _session_has_live_work(session_id: str) -> bool:
    """Executions, in-flight chat turns and Jobs still live on a session.

    Ordinary WS chat turns are held in the server runtime registry rather
    than jobs.json, so all three representations are asked.
    """
    try:
        from openprogram.runtime.execution.store import default_store as execution_default_store
        from openprogram.runtime.execution.model import ExecutionStatus
        execution_store = execution_default_store()
        active_statuses = {
            ExecutionStatus.RUNNING,
            ExecutionStatus.PAUSING,
            ExecutionStatus.CANCELLING,
        }
        if any(
            record.status in active_statuses
            for record in execution_store.list_nonterminal(session_id=session_id)
        ):
            return True
        from openprogram.runtime import interface_link
        if interface_link.session_busy(session_id):
            return True
        from openprogram.runtime.jobs.store import list_jobs
        from openprogram.runtime.jobs.types import JobStatus
        active = {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
        return bool(list_jobs(session_id, status_filter=active, limit=1))
    except Exception:
        # Unknown execution state is unsafe to migrate through; fail closed.
        return True


def _turn_branch(session_id: str):
    from openprogram.runtime.agent.turn_request import get_turn_request

    req = get_turn_request()
    if req is not None and getattr(req, "session_id", None) == session_id:
        return getattr(req, "branch_id", None) or None
    return None


def _unfinished_jobs(session_id: str):
    from openprogram.runtime.jobs.store import list_jobs
    from openprogram.runtime.jobs.types import is_terminal

    return [
        (job.id, getattr(job, "label", None) or getattr(job, "subject", None) or "", job.status.value)
        for job in list_jobs(session_id, lock_timeout=1.0)
        if not is_terminal(job.status)
    ]


def _agent_registry():
    from openprogram.runtime.agent_registry import manager

    return manager


def _session_chat_model(session_id):
    from openprogram.runtime.conversation.session_model import read_session_chat_model

    return read_session_chat_model(session_id)


def _current_job_resource_context():
    from openprogram.runtime.jobs.runner import current_job_resource_context

    return current_job_resource_context()


def _current_job_operation_timeout(default, *, preemptibility):
    from openprogram.runtime.jobs.runner import current_job_operation_timeout

    return current_job_operation_timeout(default, preemptibility=preemptibility)


def _record_current_job_activity(activity_kind: str) -> bool:
    from openprogram.runtime.jobs.runner import record_current_job_activity

    return record_current_job_activity(activity_kind)


def _emit(event_type, source, payload=None, metadata=None):
    from openprogram.runtime import events

    events.emit_safe(event_type, source, payload, metadata)


def _emit_ws_frame(frame: dict):
    from openprogram.runtime import events

    events.emit_ws_frame(frame)


def _execution(execution_id: str):
    from openprogram.runtime.execution import default_store

    return default_store().get_execution(execution_id)


def _conversation_execution_ids(session_id: str) -> list[str]:
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.conversation_scope import conversation_executions

    return [item.execution_id for item in conversation_executions(default_store(), session_id)]


def _worker_stopping() -> bool:
    from openprogram.runtime.conversation.run_control import is_worker_stopping

    return is_worker_stopping()


def _foreground_turn(session_id: str):
    from openprogram.runtime.execution.run_control import current_token

    return current_token(session_id)


def _current_call_id() -> str:
    from openprogram.runtime.agent.call_state import current_call_id

    return current_call_id()


def _current_tool_call_id() -> str:
    from openprogram.runtime.agent.call_state import current_tool_call_id

    return current_tool_call_id()


def _turn_request_source():
    from openprogram.runtime.agent.turn_request import get_turn_request

    return getattr(get_turn_request(), "source", None)


def _allowed_tool_names():
    from openprogram.programs._host.runtime import _allowed_tool_names

    return _allowed_tool_names.get()


def _session_workdir(session_id: str):
    from openprogram.runtime.conversation.workdir import runtime_location_for

    workdir = runtime_location_for(session_id)["workdir"]
    return workdir if isinstance(workdir, str) and workdir else None


def _session_additional_working_dirs(session_id: str):
    from openprogram.runtime.conversation.session_config import load_session_run_config

    return load_session_run_config(session_id).additional_working_dirs


def _surface_context():
    from openprogram.runtime.conversation import surface_context

    return surface_context.current()


def _broadcast(frame: dict) -> None:
    from openprogram.runtime import interface_link

    interface_link.broadcast(frame)


def _forget_session_runs(session_id: str) -> None:
    from openprogram.runtime.execution.store import default_store

    default_store().tables.forget_session(session_id)


def _load_programs() -> None:
    from openprogram.programs._host import services

    services.load_programs()


def _system_prompt_parts(text: str):
    from openprogram.runtime.context.components import prompt_parts

    return prompt_parts(text)


def register() -> None:
    from openprogram.models.providers.recording.capture import register_system_sections

    register_system_sections(_system_prompt_parts)
    register_current_store_resolver(_current_store)
    execution_facts.register(
        current_session_id=_current_session_id,
        current_execution_id=_current_execution_id,
        turn_running=_turn_running,
        session_has_live_work=_session_has_live_work,
        agent_registry=_agent_registry,
        session_chat_model=_session_chat_model,
        current_job_resource_context=_current_job_resource_context,
        current_job_operation_timeout=_current_job_operation_timeout,
        record_current_job_activity=_record_current_job_activity,
        forget_session_runs=_forget_session_runs,
        turn_branch=_turn_branch,
        unfinished_jobs=_unfinished_jobs,
    )
    event_sink.register_event_sink(emit=_emit, emit_ws_frame=_emit_ws_frame)
    host_runtime_facts.register(
        execution=_execution,
        conversation_execution_ids=_conversation_execution_ids,
        worker_stopping=_worker_stopping,
        foreground_turn=_foreground_turn,
        current_call_id=_current_call_id,
        current_tool_call_id=_current_tool_call_id,
        turn_request_source=_turn_request_source,
        allowed_tool_names=_allowed_tool_names,
        session_workdir=_session_workdir,
        session_additional_working_dirs=_session_additional_working_dirs,
        surface_context=_surface_context,
        broadcast=_broadcast,
        load_programs=_load_programs,
    )


__all__ = ["register"]

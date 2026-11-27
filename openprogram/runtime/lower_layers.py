"""What the runtime hands down to models, state, the host and its own lower
parts when it loads.

``openprogram.models``, ``openprogram.state`` and ``openprogram.host`` sit
below the runtime and never import it. The facts they need from it arrive
through ports they own, filled in here:

* ``state.store.current`` — the store the active Context carries;
* ``state.execution_facts`` — the bound session and execution, running
  turns, live work on a session, the saved Agents, the claimed Job;
* ``state.event_sink`` — the event bus;
* ``host.runtime_facts`` — execution records, the worker stopping, the
  foreground turn, the running call and tool call, the turn's source, a
  conversation's working directories and App surface and the client
  broadcast (the turn's tool policy comes from Programs,
  ``programs._host.lower_layers``);
* ``models.providers.recording.capture`` — the named sections of a
  system prompt the runtime composed, for the prompt-cache check.

Inside the runtime the parts are ordered too (``agent_registry`` < ``jobs``
< ``conversation`` < ``scheduler`` < ``harnesses``). What a lower part needs
of a higher one goes through a link module of the runtime, which imports no
part and is filled here (the modules stay small, so filling them loads no
part):

* ``runtime.conversation_link`` — the conversation side of a Job, for
  ``jobs``: its agent turn and driver, killing its process, the
  conversation's permission and branch settings, the inbox, idle signals and
  a spawned agent's card;
* ``runtime.scheduler_link`` — for ``conversation``: a scheduled run's
  ending and pages, the scheduled tasks, the system-notification fallback
  and time limits;
* ``runtime.harness_link`` — for ``conversation``: the outside agent a turn
  runs in.

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


def _sessions_above_execution(execution_id: str) -> list[str]:
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.conversation_scope import sessions_above_execution

    store = default_store()
    execution = store.get_execution(execution_id) if store is not None else None
    return sessions_above_execution(store, execution) if execution is not None else []


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
    from openprogram.runtime.conversation.session_settings.session_model import read_session_chat_model

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


def _session_workdir(session_id: str):
    from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for

    workdir = runtime_location_for(session_id)["workdir"]
    return workdir if isinstance(workdir, str) and workdir else None


def _session_additional_working_dirs(session_id: str):
    from openprogram.runtime.conversation.session_settings.session_config import load_session_run_config

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


def _system_prompt_parts(text: str):
    from openprogram.runtime.context.prompt_assembly.components import prompt_parts

    return prompt_parts(text)


# ── jobs.conversation_link ───────────────────────────────────────────────

def _agent_driver(executions, **options):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    return AgentProductionDriver(executions, **options)


def _agent_capabilities(payload):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    return AgentProductionDriver.capabilities_for_payload(payload)


def _agent_adapter(**options):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter

    return CanonicalAgentAdapter(**options)


def _kill_process(session_id: str, *, execution_id: str):
    from openprogram.runtime.conversation.process_runner import kill_active_subprocess

    return kill_active_subprocess(session_id, execution_id=execution_id)


def _unregister_cancel_event(session_id: str, cancel_event, *, execution_id: str) -> None:
    from openprogram.runtime.conversation.run_control import unregister_cancel_event

    unregister_cancel_event(session_id, cancel_event, execution_id=execution_id)


def _conversation_permission(session_id: str, **options):
    from openprogram.runtime.conversation.session_settings.session_config import conversation_permission

    return conversation_permission(session_id, **options)


def _freeze_branch_request(request):
    from openprogram.runtime.conversation.session_settings.branch_config import freeze_branch_request

    return freeze_branch_request(request)


def _branch_holding(session_id: str, message_id):
    from openprogram.runtime.conversation.session_settings.branch_config import branch_holding

    return branch_holding(session_id, message_id)


def _inbox():
    from openprogram.runtime.conversation.peer_agents import inbox

    return inbox


def _subscribe_idle(callback):
    from openprogram.runtime.conversation.run_control.idle import subscribe_idle

    return subscribe_idle(callback)


def _attach_card(db, session_id: str, node_id):
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import _attach_node

    return _attach_node(db, session_id, node_id)


def _write_attach_placeholder(**card):
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import write_attach_placeholder_for_spawn

    return write_attach_placeholder_for_spawn(**card)


# ── conversation.scheduler_link ──────────────────────────────────────────

def _run_ended(executions, execution, result) -> None:
    from openprogram.runtime.scheduler import run_finish

    run_finish.finish_resumed(executions, execution, result)


def _close_run_pages(execution_id: str) -> None:
    from openprogram.runtime.scheduler import run_finish

    run_finish.close_run_tabs(execution_id)


def _scheduled_tasks():
    from openprogram.runtime.scheduler import service

    return service.list_tasks()


def _notify_unless_shown(notice_id: str, title: str, message: str, unless=None) -> None:
    from openprogram.runtime.scheduler import reminders

    reminders.notify_unless_shown(notice_id, title, message, unless=unless)


def _stop_overdue(now: float):
    from openprogram.runtime.scheduler import time_limits

    return time_limits.stop_overdue(now)


def _limited_runs_open() -> bool:
    from openprogram.runtime.scheduler import time_limits

    return time_limits.limited_runs_open()


# ── conversation.harness_link ────────────────────────────────────────────

def _turn_engine(req):
    from openprogram.runtime.harnesses import turn_engine

    return turn_engine(req)


def _runs_in_harness(request) -> bool:
    from openprogram.runtime.harnesses import is_harness_provider, turn_provider

    return is_harness_provider(turn_provider(request))


def _register_runtime_parts() -> None:
    from openprogram.runtime import conversation_link, harness_link, scheduler_link

    conversation_link.register(
        agent_driver=_agent_driver,
        agent_capabilities=_agent_capabilities,
        agent_adapter=_agent_adapter,
        kill_process=_kill_process,
        unregister_cancel_event=_unregister_cancel_event,
        conversation_permission=_conversation_permission,
        freeze_branch_request=_freeze_branch_request,
        branch_holding=_branch_holding,
        inbox=_inbox,
        subscribe_idle=_subscribe_idle,
        attach_card=_attach_card,
        write_attach_placeholder=_write_attach_placeholder,
    )
    scheduler_link.register(
        run_ended=_run_ended,
        close_run_pages=_close_run_pages,
        scheduled_tasks=_scheduled_tasks,
        notify_unless_shown=_notify_unless_shown,
        stop_overdue=_stop_overdue,
        limited_runs_open=_limited_runs_open,
    )
    harness_link.register(turn_engine=_turn_engine, runs_in_harness=_runs_in_harness)


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
        sessions_above_execution=_sessions_above_execution,
        worker_stopping=_worker_stopping,
        foreground_turn=_foreground_turn,
        current_call_id=_current_call_id,
        current_tool_call_id=_current_tool_call_id,
        turn_request_source=_turn_request_source,
        session_workdir=_session_workdir,
        session_additional_working_dirs=_session_additional_working_dirs,
        surface_context=_surface_context,
        broadcast=_broadcast,
    )
    _register_runtime_parts()


__all__ = ["register"]

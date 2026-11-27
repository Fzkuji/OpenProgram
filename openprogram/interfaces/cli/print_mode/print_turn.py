"""One ``--print`` turn run in this process, and what the run reports.

Without a worker, a ``--print`` turn is admitted and activated the way a
channel or ACP turn is: a durable execution of the conversation turn path
(``process_user_turn`` under the production driver). It runs until the model
answers in text, with the same tool loop, compaction and loop guards as a
turn typed in the Web UI; nothing caps it at the handful of tool rounds a
library ``runtime.exec`` call gets.

The run works in the directory the command was started in: a new
conversation is bound to that directory as its project, so tools start
there, as they do in a Web conversation opened on a folder. No one is behind
the turn to answer an approval card or a question, so its authority says so
(``interaction="non-interactive"``): an operation that would ask is refused
at once and the question tool is not offered, rather than the run waiting
for an answer that never comes.

``--model provider/id``, ``--effort LEVEL`` and ``--permission-mode MODE``
are this turn's own: none is saved on the conversation, and a model the
provider lists runs without being enabled. ``--no-project-binding`` keeps a
new conversation out of the working directory. ``--usage-file``
receives a JSON summary of the run read from the usage ledger, where every
model request of the conversation is recorded with its tokens and cost.
``--transcript-file`` receives the turn as stored, one JSON record per line:
every model request with its tokens, and the model's text, reasoning, tool
calls with their full arguments and tool results, in order.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)

SOURCE = "cli"

# How the run ended, as the usage summary names it.
ENDED_COMPLETED = "completed"
ENDED_FAILED = "failed"
ENDED_CANCELLED = "cancelled"
ENDED_NOT_STARTED = "not_started"

# Which transport ran the turn, as the run's record names it.
TRANSPORT_WORKER = "worker"
TRANSPORT_IN_PROCESS = "in_process"


class PrintTurnError(RuntimeError):
    """The run could not start, or ended without a reply."""


@dataclass
class PrintRun:
    """What one ``--print`` run did, for the reply and the usage summary."""

    session_id: str
    started_at: float = field(default_factory=time.time)
    ended: str = ENDED_NOT_STARTED
    error: str | None = None
    # How the runtime classified a failure: the ``ErrorReason`` value,
    # whether a retry may help, and the stable category of what failed
    # (``model_unavailable`` here; the runtime's own when it names one).
    error_reason: str | None = None
    error_retryable: bool | None = None
    error_kind: str | None = None
    reply: str = ""
    structured_output: Any = None
    # The turn's result as the turn path returned it (in-process runs only).
    result: Any = None
    tool_calls: int | None = None
    model: str | None = None
    execution_id: str | None = None
    # A Worker restart ended the turn and the run went on in the turn that
    # continued it (part of the run's execution tree).
    restarted: bool = False
    # The signal that stopped the run (:func:`stop_signal`), if one did.
    stopped_by: str | None = None
    # The turn was still running when the command stopped waiting for it
    # (see :func:`_stop_run`); the process then leaves with :func:`leave`.
    left_running: bool = False
    # The reasoning effort ``--effort`` asked for, ``None`` without it (the
    # Agent's own effort then applies).
    effort: str | None = None
    # The permission mode the turn was admitted with: the request's in this
    # process, the ack's on the Worker (``--permission-mode`` until then).
    permission_mode: str | None = None
    # What each model request of this process carried on the wire, in the
    # order sent, while :func:`recording_requests` is active
    # (:func:`wire_facts`, with the request's time, kind and execution).
    sent: list[dict[str, Any]] = field(default_factory=list)
    # Which transport ran the turn (:data:`TRANSPORT_WORKER` or
    # :data:`TRANSPORT_IN_PROCESS`), ``None`` before one was picked; and the
    # version the Worker gave, for a turn it ran.
    transport: str | None = None
    worker_version: str | None = None
    # The calls the permission decision refused (``print_events``
    # ``permission_denials``), once the run's events were read.
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    # A slash command answered without a model turn (``/compact``): its name.
    local_command: str | None = None
    # What stdout carries (``--output-format``): ``text``, ``json`` or
    # ``stream-json``.
    output_format: str = "text"
    # ``--no-web``: the turn had no web tool and no provider-side search.
    no_web: bool = False


def parse_model(value: str) -> tuple[str, str]:
    """Split ``provider/id``; raises ``ValueError`` for any other shape."""
    provider, sep, model_id = (value or "").strip().partition("/")
    if not sep or not provider.strip() or not model_id.strip():
        raise ValueError("--model takes PROVIDER/MODEL_ID, e.g. anthropic/claude-sonnet-4-6")
    return provider.strip(), model_id.strip()


class ModelUnavailableError(PrintTurnError):
    """``--model`` names a model that could not be enabled.

    ``listing_error`` is why the provider's model list could not be read
    (an HTTP status, a connection error), or ``None`` when the list was
    read and does not include the model.
    """

    kind = "model_unavailable"

    def __init__(self, model: str, listing_error: str | None = None):
        provider, _, model_id = model.partition("/")
        if listing_error:
            message = (f"could not read {provider}'s model list ({listing_error}), "
                       f"so the model {model_id!r} could not be enabled")
        else:
            message = f"{provider} does not list the model {model_id!r}"
        super().__init__(message)
        self.model = model
        self.listing_error = listing_error


def use_model(model: str) -> None:
    """Make ``provider/id`` usable by this run, for its turn only.

    ``--model`` names the model of one run: the model is not enabled in the
    config and not saved on the conversation, so the App's model list and
    the conversation's own model stay as they were. A model the provider
    lists runs without being enabled (``enabled_models.use_model_in_process``).

    Raises :class:`ModelUnavailableError` when the model is not usable,
    saying whether the provider's list was unreadable or simply does not
    include it.
    """
    from openprogram.models.providers.catalog.enabled_models import use_model_in_process

    provider, model_id = parse_model(model)
    if use_model_in_process(provider, model_id):
        return
    from openprogram.models.listing.listing import _browse_models_with_error

    # The list is read here with its failure: a list that could not be read
    # is not a list without the model.
    rows, listing_error = _browse_models_with_error(provider)
    listed = any(row.get("id") == model_id for row in rows)
    raise ModelUnavailableError(model, None if listed else listing_error)


def chosen_model(agent_id: str, model: str | None) -> str | None:
    """The model a new conversation would run: ``model``, else the Agent's
    own, else the one used last; ``None`` when nothing has been chosen."""
    if model:
        return model
    from openprogram.runtime.conversation.session_settings.session_model import (
        new_session_chat_model,
        override_string,
    )
    provider, model_id = new_session_chat_model(agent_id)
    if provider and model_id:
        return override_string(provider, model_id)
    return None


#: Told to the model beside the instruction (see ``runtime.conversation.turn_admission.run_note``).
from openprogram.runtime.conversation.turn_admission.run_note import RUN_NOTE  # noqa: E402


def non_interactive_owner() -> dict[str, Any]:
    """The owner's authority for a turn no one can answer questions in."""
    from openprogram.runtime.permissions.authority import local_owner_authority

    return {**local_owner_authority(), "interaction": "non-interactive"}


def _response_format_payload(response_format) -> Any:
    from openprogram.interfaces.cli.print_mode.worker_turn import _response_format_payload as payload

    return payload(response_format)


def _prepare_session(session_id: str, agent_id: str, *, cwd: str | os.PathLike,
                     project_binding: bool = True) -> None:
    """Create a new conversation, bound to ``cwd`` unless ``project_binding``
    is off (``--no-project-binding``: nothing is written in ``cwd``, the
    conversation is kept in the profile). The model ``--model`` names is the
    turn's (``model_override``), never pinned here."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.project.project_store import working_directory_project

    db = default_db()
    if not db.is_created(session_id):
        db.create_session(
            session_id, agent_id, title="", source=SOURCE,
            project_id=working_directory_project(cwd) if project_binding else None,
        )


def target_fields(target: Any) -> dict[str, Any]:
    """The request fields that put a turn where ``--at`` points
    (``print_session.PrintTarget``): on a selected branch its id; on another
    recorded branch its id, tip and ``side_branch`` (it moves only that
    branch); after a message inside a branch, ``branch_from`` (a new
    branch, as an edit or a retry makes)."""
    if target is None:
        return {}
    if getattr(target, "branch_from", None):
        return {"branch_from": target.branch_from}
    if getattr(target, "branch_id", None):
        if target.selected:
            return {"branch_id": target.branch_id}
        return {"branch_id": target.branch_id, "branch_from": target.branch_head,
                "advance_head": False, "side_branch": True}
    return {}


def no_web_fields(no_web: bool) -> dict[str, Any]:
    """The request fields of ``--no-web``: a per-turn tool override with the
    Search intent off and every web tool taken away
    (``provider_search.NO_WEB_OVERRIDE``); nothing without it."""
    if not no_web:
        return {}
    from openprogram.runtime.agent.provider_search import NO_WEB_OVERRIDE

    return {"tools_override": dict(NO_WEB_OVERRIDE)}


def slash_request_fields(text: str) -> dict[str, Any]:
    """The fields a typed ``/task`` or ``/merge`` adds, read as the Worker's
    chat command reads them (``local_commands.slash_fields``)."""
    if not text.startswith("/"):
        return {}
    from openprogram.interfaces.server.chat_input import parse_chat_input
    from openprogram.runtime.conversation.turn_admission.local_commands import slash_fields

    fields = slash_fields(parse_chat_input(text))
    return fields if fields.get("slash_action") else {}


def run_in_process(session_id: str, text: str, *, agent_id: str,
                   model: str | None = None, response_format=None,
                   cwd: str | os.PathLike | None = None,
                   run: PrintRun | None = None,
                   effort: str | None = None,
                   permission_mode: str | None = None,
                   target: Any = None,
                   project_binding: bool = True,
                   no_web: bool = False,
                   on_event: Any = None,
                   on_admitted: Any = None,
                   starts_goal: bool = False) -> PrintRun:
    """Run one turn of ``session_id`` in this process and wait for its end.

    ``model`` (``--model``), ``effort`` (``--effort``) and
    ``permission_mode`` (``--permission-mode``) are this turn's own; none is
    stored on the conversation or the Agent. ``target`` is where ``--at``
    puts the turn (:func:`target_fields`); ``project_binding`` off keeps a
    new conversation out of ``cwd``; ``no_web`` (``--no-web``) gives the
    turn no web tool and no provider search. ``on_event`` receives the turn's
    frames as the Worker would broadcast them; ``on_admitted`` is called
    with the run once its execution is admitted. ``starts_goal`` marks the
    message as the one that sets a Goal (``/goal <objective>``). Returns the run with
    ``ended`` set; a failed turn is returned, not raised. Raises
    :class:`PrintTurnError` when the turn could not be admitted or started.
    """
    import asyncio

    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.turn_admission.turn_input import build_turn_request

    run = run or PrintRun(session_id=session_id)
    run.model = model or run.model
    run.effort = effort or run.effort
    run.no_web = no_web or run.no_web
    cwd = cwd or os.getcwd()
    try:
        _prepare_session(session_id, agent_id, cwd=cwd, project_binding=project_binding)
    except Exception as exc:
        raise PrintTurnError(
            f"the conversation could not be set up in {cwd}: {type(exc).__name__}: {exc}"
        ) from exc

    authority = non_interactive_owner()
    msg_id = uuid.uuid4().hex[:12]
    request = build_turn_request(
        session_id, text, agent_id=agent_id, source=SOURCE, msg_id=msg_id,
        authority=authority, unattended=True,
        model_override=model,
        response_format=_response_format_payload(response_format),
        **({"thinking_effort": run.effort} if run.effort else {}),
        **({"permission_mode": permission_mode} if permission_mode else {}),
        **no_web_fields(run.no_web),
        **slash_request_fields(text),
        **target_fields(target),
        **({"starts_goal": True} if starts_goal else {}),
    )
    run.permission_mode = getattr(request, "permission_mode", None)
    adapter = CanonicalAgentAdapter(
        event_sink=on_event if on_event is not None else (lambda _envelope: None))
    try:
        admission = adapter.admit(
            request,
            trusted_actor=authority,
            user_message_id=msg_id,
            assistant_message_id=f"{msg_id}_reply",
            config_snapshot_ref=f"{SOURCE}:{session_id}",
        )
    except Exception as exc:
        raise PrintTurnError(f"the run was not admitted: {exc}") from exc
    run.execution_id = admission.execution_id
    if on_admitted is not None:
        on_admitted(run)
    # The turn runs on the driver's own thread. Waiting for it from a helper
    # thread keeps Ctrl-C (and SIGTERM, see ``interrupt_on_terminate``) out
    # of the wait: an interrupted ``asyncio.run`` would cancel the future the
    # driver resolves when the turn ends, which releases the turn's owner
    # while its thread goes on making model requests nothing can stop.
    #
    # The wait is on an event the helper sets when it is done, not on the
    # thread: on Python 3.12 a signal that interrupts ``Thread.join`` marks
    # a thread that is still running as stopped, and every later join then
    # returns at once, so the command would leave while the turn ran on.
    outcome: dict[str, Any] = {}
    finished = threading.Event()

    def _activate() -> None:
        try:
            outcome["value"] = asyncio.run(adapter.activate(admission))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            outcome["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=_activate, daemon=True,
                     name=f"openprogram-print-{admission.execution_id}").start()
    try:
        while not finished.wait(0.2):
            pass
    except KeyboardInterrupt:
        run.ended = ENDED_CANCELLED
        noted = hold_signals()
        while True:
            try:
                _stop_run(adapter, admission, finished, outcome, run)
                break
            except KeyboardInterrupt:
                # A second signal raised before the hold took it in: it
                # counts as noted, and the stop is delivered after all.
                noted.set()
        raise
    if "error" in outcome:
        exc = outcome["error"]
        # An activation that never began leaves its admission open; one
        # that began is settled by the driver, and this is a no-op.
        try:
            adapter.fail_admission(admission, reason_code="cli_print_activation_failed")
        except Exception:  # noqa: BLE001 - already settled by the driver
            _log.debug("print admission already settled", exc_info=True)
        raise PrintTurnError(f"the run failed: {type(exc).__name__}: {exc}") from exc
    activated = outcome.get("value")
    if activated is None:
        raise PrintTurnError("the run did not start")
    _active, result = activated
    if result is None:
        raise PrintTurnError("the run ended without a result")
    run.result = result
    if getattr(result, "failed", False):
        # A failed turn's result carries no tool calls, whatever ran before
        # the failure, so the count is unknown rather than zero.
        run.ended = ENDED_FAILED
        run.error = result.error or result.error_reason or "the turn failed"
        run.error_reason = getattr(result, "error_reason", None)
        run.error_retryable = getattr(result, "error_retryable", None)
        run.error_kind = getattr(result, "error_kind", None)
        return run
    run.tool_calls = len(getattr(result, "tool_calls", None) or [])
    if getattr(result, "stopped", False):
        run.ended = ENDED_CANCELLED
        run.reply = result.final_text or ""
        return run
    run.ended = ENDED_COMPLETED
    run.reply = result.final_text or ""
    run.structured_output = result.structured_output
    return run


# How a stopped command waits and leaves, the same for both transports.
from openprogram.interfaces.cli.print_mode import print_stop  # noqa: E402
from openprogram.interfaces.cli.print_mode.print_stop import (  # noqa: E402,F401 - this module's names
    hold_signals,
    interrupt_on_terminate,
    leave,
    signals_noted,
    stop_signal,
    stopping,
)

_LEFT_RUNNING = ("the run had not stopped when the command left; the usage "
                 "summary may miss its last requests")


def _stop_run(adapter, admission, finished: threading.Event,
              outcome: dict[str, Any], run: PrintRun) -> None:
    """Cancel an interrupted run and wait for it to stop.

    The cancel goes through the execution's control path, as the Web UI's
    stop button does, so the turn stops at its next safe point instead of
    running on. The run's summary is written only after the turn has
    stopped, so it counts every request the turn made. The wait is the
    shared one (:class:`print_stop.StopClock`): a turn that does not stop
    in time leaves an ``error`` saying the summary may be short, and
    ``left_running`` set: the turn's thread is still busy, so the command
    must not wait for it on its way out (see :func:`leave_stopped`).
    """
    # From here on the command is stopping: a further signal is noted, not
    # raised into the middle of a store write, and shortens the wait below.
    noted = hold_signals()
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.runtime.execution.run_control import cancel_execution

    # A turn not yet activated is closed as cancelled here; once its
    # owner runs this is a no-op and the cancel below reaches it.
    try:
        adapter.fail_admission(admission, reason_code="cli_print_cancelled",
                               target=ExecutionStatus.CANCELLED)
    except Exception:  # noqa: BLE001 - already settled by the driver
        _log.debug("print admission already settled", exc_info=True)
    try:
        cancel_execution(admission.execution_id)
    except Exception:  # noqa: BLE001 - already ended, or never activated
        _log.debug("print run %s not cancelled", admission.execution_id, exc_info=True)
    clock = print_stop.StopClock(noted)
    clock.sent()
    if not clock.wait_for(finished):
        run.error = _LEFT_RUNNING
        run.left_running = True
        return
    value = outcome.get("value")
    result = value[1] if isinstance(value, tuple) and len(value) == 2 else None
    if result is not None:
        run.result = result
        run.tool_calls = len(getattr(result, "tool_calls", None) or [])


def leave_stopped(run: PrintRun, code: int = print_stop.STOPPED_EXIT_CODE) -> None:
    """End a stopped run's command, its files written
    (:func:`print_stop.leave_stopped`). Whenever the process leaves at once,
    the turn's commands are stopped first (:func:`stop_commands`)."""
    print_stop.leave_stopped(code, left_running=run.left_running,
                             before_leaving=lambda: stop_commands(run))


def stop_commands(run: PrintRun) -> None:
    """Stop the commands the run's turn was still waiting on.

    A command runs under a supervisor of its own and outlives the process
    that started it, so a turn left running when the command leaves would
    leave its command running too (a build, a test server). They are
    stopped as the turn would stop them when cancelled; the supervisor
    carries the stop out after this process is gone. A command the turn
    started in the background goes on, as it does after a stopped turn.
    """
    if not run.execution_id:
        return
    from openprogram.host.processes import ProcessStore, control
    from openprogram.host.processes.store import LIVE

    try:
        processes = ProcessStore()
        records = processes.list(run.session_id, (run.execution_id,))
    except Exception:  # noqa: BLE001 - leaving regardless
        _log.warning("commands of run %s not read", run.execution_id, exc_info=True)
        return
    for record in records:
        if (record.get("execution_id") != run.execution_id
                or record.get("status") not in LIVE or record.get("background")):
            continue
        try:
            processes.update(record["id"], reported=True)
            control(processes, record["id"], "stop", wait=False)
        except Exception:  # noqa: BLE001 - one command must not shield the others
            _log.warning("command %s of run %s not stopped", record["id"],
                         run.execution_id, exc_info=True)


# ---------------------------------------------------------------------------
# Usage summary
# ---------------------------------------------------------------------------

def usage_summary(run: PrintRun) -> dict[str, Any]:
    """The run's token usage and cost, read from the usage ledger.

    Every model request is recorded in the ledger with its tokens, the cost
    priced from the model catalog (or reported by the provider) and the
    execution it was made in. The summary counts the requests of the run's
    execution tree (:func:`run_executions`): its own, its sub-agents' at any
    depth and the turn a Worker restart continued it as; not another branch
    of the conversation running at the same time, nor the title the Worker
    writes beside the turn. ``cost_usd`` is ``None`` when any request could
    not be priced.
    """
    rows = _ledger_rows(run)
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_tokens": 0,
              "output_tokens": 0, "cost_usd": 0.0, "unpriced_requests": 0, "requests": 0}
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    # By what each request was for (``chat`` the turn's rounds,
    # ``summarize`` a compaction, ...), so a compaction's cost is told apart.
    by_kind: dict[str, dict[str, Any]] = {}
    chat_requests = 0
    for row in rows:
        kind = by_kind.setdefault(row.keys.get("call_kind") or "chat", {
            "requests": 0, "input_tokens": 0, "cached_input_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "unpriced_requests": 0})
        kind["requests"] += row.events
        kind["input_tokens"] += row.input_tokens
        kind["cached_input_tokens"] += row.cache_read_tokens
        kind["cache_write_tokens"] += row.cache_write_tokens
        kind["output_tokens"] += row.output_tokens
        kind["cost_usd"] += row.cost_total
        kind["unpriced_requests"] += row.unknown_cost_events
        totals["input_tokens"] += row.input_tokens
        totals["cached_input_tokens"] += row.cache_read_tokens
        totals["cache_write_tokens"] += row.cache_write_tokens
        totals["output_tokens"] += row.output_tokens
        totals["cost_usd"] += row.cost_total
        totals["unpriced_requests"] += row.unknown_cost_events
        totals["requests"] += row.events
        key = (row.keys.get("provider") or "", row.keys.get("model_id") or "")
        entry = by_model.setdefault(key, {
            "model": f"{key[0]}/{key[1]}" if key[0] else key[1],
            "requests": 0, "input_tokens": 0, "cached_input_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "unpriced_requests": 0, "_chat_requests": 0,
        })
        if row.keys.get("call_kind") == "chat":
            chat_requests += row.events
            entry["_chat_requests"] += row.events
        entry["requests"] += row.events
        entry["input_tokens"] += row.input_tokens
        entry["cached_input_tokens"] += row.cache_read_tokens
        entry["cache_write_tokens"] += row.cache_write_tokens
        entry["output_tokens"] += row.output_tokens
        entry["cost_usd"] += row.cost_total
        entry["unpriced_requests"] += row.unknown_cost_events

    result_usage = getattr(run.result, "usage", None) or {}
    agent_rounds = result_usage.get("agent_iteration_count")
    if agent_rounds is None and run.ended != ENDED_NOT_STARTED:
        agent_rounds = chat_requests
    first_sent = next(iter(_sent_by_turn(run)), None)
    return {
        "ended": run.ended,
        "error": run.error,
        **run_outcome(run),
        "session_id": run.session_id,
        "execution_id": run.execution_id,
        "model": run.model or _main_model(by_model),
        # The effort ``--effort`` asked for, and the one the turn's first
        # request sent with its tool count (``None`` when this process did
        # not see it sent).
        "effort_requested": run.effort,
        "effort": first_sent["effort"] if first_sent else None,
        "tools_count": len(first_sent["tools"]) if first_sent else None,
        "duration_s": round(max(0.0, time.time() - run.started_at), 3),
        "input_tokens": totals["input_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "cache_write_tokens": totals["cache_write_tokens"],
        "output_tokens": totals["output_tokens"],
        "cost_usd": None if totals["unpriced_requests"] else round(totals["cost_usd"], 6),
        "unpriced_requests": totals["unpriced_requests"],
        "model_requests": totals["requests"],
        "agent_rounds": agent_rounds,
        "tool_calls": run.tool_calls,
        "permission_denials": list(run.permission_denials),
        "local_command": run.local_command,
        "by_model": [
            {**{k: v for k, v in entry.items() if not k.startswith("_")},
             "cost_usd": None if entry["unpriced_requests"] else round(entry["cost_usd"], 6)}
            for entry in sorted(by_model.values(), key=lambda item: -item["requests"])
        ],
        "by_call_kind": {
            name: {**entry, "cost_usd": None if entry["unpriced_requests"]
                   else round(entry["cost_usd"], 6)}
            for name, entry in sorted(by_kind.items())
        },
    }


def run_outcome(run: PrintRun) -> dict[str, Any]:
    """Where the run got to and how its failure was classified.

    ``phase`` is ``in_turn`` once an execution was admitted for the run
    (``execution_id`` known) and ``not_started`` before: a model that could
    not be enabled, a Worker that refused the turn before its ack.
    ``transport`` is which one ran the turn and ``worker_version`` what the
    Worker said it is (``None`` in-process).
    """
    return {
        "phase": "in_turn" if run.execution_id else "not_started",
        "error_reason": run.error_reason,
        "error_retryable": run.error_retryable,
        "error_kind": run.error_kind,
        "restarted": run.restarted,
        "stopped_by": run.stopped_by,
        "transport": run.transport,
        "worker_version": run.worker_version,
    }


def _main_model(by_model: dict) -> str | None:
    """The model that ran most of the turn's own rounds."""
    if not by_model:
        return None
    return max(by_model.values(),
               key=lambda item: (item["_chat_requests"], item["requests"]))["model"]


def run_executions(run: PrintRun) -> frozenset[str]:
    """The executions of the run: the one admitted for it and its tree
    (:func:`~openprogram.runtime.execution.projections.execution_tree.execution_tree`).
    Empty for a run that never started. Read from a fresh view of the
    execution store, which the Worker may have written."""
    if not run.execution_id:
        return frozenset()
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.execution_tree import execution_tree

    try:
        return execution_tree(run.execution_id, store=default_store().fresh_view())
    except Exception:  # noqa: BLE001 - the run's own execution still names its requests
        _log.warning("execution tree of %s unreadable", run.execution_id, exc_info=True)
        return frozenset({run.execution_id})


def _ledger_rows(run: PrintRun) -> list:
    from openprogram.models.usage.ledger import default_ledger

    try:
        rows = default_ledger.query(
            group_by=["provider", "model_id", "call_kind"],
            execution_ids=run_executions(run),
        )
    except Exception:  # noqa: BLE001 - a summary without the ledger is still written
        _log.warning("usage ledger unreadable for %s", run.session_id, exc_info=True)
        return []
    return [row for row in rows if row.events]


def write_usage_file(path: str | os.PathLike, run: PrintRun) -> None:
    """Write the run's usage summary to ``path`` as one JSON object."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(usage_summary(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    replace_file(temporary, target)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

def transcript_records(run: PrintRun, *, store: Any = None) -> list[dict[str, Any]]:
    """The run's events (:func:`print_events.run_events`) as the transcript
    file writes them, one record each: the ``run`` record, the instruction,
    every model request with its tokens, what each returned (reasoning,
    text, tool calls with their full arguments, each call's result as the
    model read it), each reply's end, and a ``gap`` record where the stores
    do not hold something the run did. Nothing is scrubbed."""
    from openprogram.interfaces.cli.print_mode import print_events

    return [print_events.transcript_record(event)
            for event in print_events.run_events(run, store=store)]


#: How a run read back from its state ended, by its execution's status. A
#: status that is not terminal is a process that was killed mid-turn.
_ENDED_BY_STATUS = {"completed": ENDED_COMPLETED, "failed": ENDED_FAILED,
                    "cancelled": ENDED_CANCELLED, "interrupted": ENDED_CANCELLED}
_KILLED = "the run stopped before it ended (its process was killed)"


def run_from_state(session_id: str | None = None) -> PrintRun:
    """The ``--print`` run a profile's state holds, read back from what it
    stored durably: the conversation (``session_id``, or the only one there
    is, as Harbor's Claude Code adapter picks its only session), its latest
    top-level execution with the request it was admitted with, and how that
    execution ended. What lived only in the process (the request bodies it
    saw sent) is absent, so the header's wire fields are ``None``.

    Raises :class:`PrintTurnError` when no single conversation or no
    execution can be found."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_store

    if session_id is None:
        rows = default_db().list_sessions(limit=3, include_archived=True)
        if len(rows) != 1:
            raise PrintTurnError(
                f"the state holds {len(rows) if len(rows) < 3 else 'several'} "
                "conversations; name one with --session")
        session_id = str(rows[0].get("id") or rows[0].get("session_id") or "")
    store = default_store().fresh_view()
    roots = [record for record in store.list_for_session(session_id)
             if not record.parent_execution_id]
    if not roots:
        raise PrintTurnError(f"conversation {session_id} has no run")
    execution = roots[0]  # newest first
    run = PrintRun(session_id=session_id, execution_id=execution.execution_id,
                   started_at=execution.created_at or time.time())
    status = getattr(execution.status, "value", execution.status)
    run.ended = _ENDED_BY_STATUS.get(str(status), ENDED_FAILED)
    if str(status) not in _ENDED_BY_STATUS:
        run.error = _KILLED
    elif run.ended == ENDED_FAILED:
        run.error = execution.reason_code or "the turn failed"
    try:
        request = (store.get_agent_turn_input(execution.execution_id) or {}).get("request") or {}
    except Exception:  # noqa: BLE001 - the transcript still has the stored turn
        _log.warning("admitted request of %s unreadable", execution.execution_id, exc_info=True)
        request = {}
    run.model = request.get("model_override") or None
    run.effort = request.get("thinking_effort") or None
    run.permission_mode = request.get("permission_mode") or None
    run.no_web = isinstance(request.get("tools_override"), dict) and (
        request["tools_override"].get("web") is False)
    run.transport = TRANSPORT_IN_PROCESS if request.get("source") == SOURCE else TRANSPORT_WORKER
    if run.model is None:
        run.model = usage_summary(run).get("model")
    return run


def transcript_from_state(session_id: str | None = None) -> list[dict[str, Any]]:
    """The transcript of the run the active profile's state holds
    (:func:`run_from_state`), built by the same code as ``--transcript-file``
    (:func:`transcript_records`), so a run killed before it wrote its file
    gets the transcript a finished one would have written."""
    return transcript_records(run_from_state(session_id))


@contextmanager
def state_directory(path: str | os.PathLike | None):
    """Within the block, the profile's state is ``path`` (``openprogram
    transcript --state``): a state directory copied out of a container, or
    any profile's. The profile root is ``~/.openprogram``, so ``HOME`` is
    pointed at a scratch folder whose ``.openprogram`` is ``path``. Nothing
    changes for ``None`` or the active state directory itself."""
    from openprogram.state.paths import get_state_dir

    if path is None:
        yield
        return
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise PrintTurnError(f"no state directory at {folder}")
    if folder == get_state_dir().resolve():
        yield
        return
    import tempfile

    saved = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE", "OPENPROGRAM_PROFILE")}
    with tempfile.TemporaryDirectory(prefix="openprogram-state-") as home:
        (Path(home) / ".openprogram").symlink_to(folder, target_is_directory=True)
        os.environ["HOME"] = os.environ["USERPROFILE"] = home
        os.environ.pop("OPENPROGRAM_PROFILE", None)
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def write_transcript_records(path: str | os.PathLike | None,
                             records: list[dict[str, Any]]) -> None:
    """Write transcript records one JSON object per line to ``path``, or to
    stdout for ``None``."""
    lines = "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n"
                    for record in records)
    if path is None:
        sys.stdout.write(lines)
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(lines, encoding="utf-8")
    replace_file(temporary, target)


# ---------------------------------------------------------------------------
# What the requests carried on the wire
# ---------------------------------------------------------------------------

#: Reasoning efforts ``--effort`` takes; ``none`` is ``off``. Off is sent as
#: the provider's own "off" value where it has one (OpenAI Responses:
#: ``reasoning: {effort: "none"}``, the bytes Codex sends) and as no
#: reasoning field elsewhere.
EFFORTS = ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max")


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# What one request carried: the model, effort, ``include``, ``store``, tools
# by name and digests (``models/providers/recording/wire_facts.py``).
from openprogram.models.providers.recording.wire_facts import (  # noqa: E402
    wire_effort,
    wire_facts,
)


@contextmanager
def recording_requests(run: PrintRun):
    """Within the block, note in ``run.sent`` what every model request of
    this process carried (:func:`wire_facts`), when it was sent, its kind
    and its execution. Read at the adapter where the body is serialized,
    so it is what went out, not what a profile asked for. A turn run by
    the Worker is not seen here."""
    from openprogram.models.providers.recording import capture
    from openprogram.models.usage.context import call_context

    def _note(request) -> None:
        # The execution bound to the running context, as the ledger names
        # the call, so a noted request pairs with its ledger row.
        context = call_context()
        run.sent.append({
            "time": time.time(), "call_kind": context.call_kind,
            "execution_id": context.execution_id, **wire_facts(request),
        })

    with capture.observing(_note):
        yield


def _sent_by_turn(run: PrintRun) -> list[dict[str, Any]]:
    """The noted requests of the run's own turn rounds (``chat``)."""
    return [fact for fact in run.sent if fact.get("call_kind") in (None, "chat")]


def _tool_output_weight(model: str | None) -> int | None:
    """The ``tool_output_weight`` the model runs with: its own, else the
    default bound; ``None`` without a model."""
    if not model:
        return None
    from openprogram.state.store.session.files.spill import DEFAULT_TOOL_OUTPUT_WEIGHT

    try:
        from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

        own = getattr(ENABLED_MODELS.get(model), "tool_output_weight", None)
    except Exception:  # noqa: BLE001 - the header names the default then
        own = None
    return int(own) if own else DEFAULT_TOOL_OUTPUT_WEIGHT


def run_header(run: PrintRun) -> dict[str, Any]:
    """The facts of the run itself, for the transcript's ``run`` record: the
    version, the effort asked for, the permission mode, a digest of the
    run note, the build's commit (``None`` outside a launcher build),
    whether ``--no-web`` was given, and what the turn's first request carried (model, effort,
    ``include``, ``store``, every tool by name, a digest of the system
    text; ``web_tools`` are the tools whose name says web). The wire
    fields are ``None`` for a run whose requests this process did not see
    (one the Worker ran, or one that made none)."""
    from importlib.metadata import PackageNotFoundError, version

    from openprogram.interfaces.cli.build_info import build_info

    try:
        installed = version("openprogram")
    except PackageNotFoundError:
        installed = None
    first = next(iter(_sent_by_turn(run)), None) or {}
    tools = first.get("tools")
    built = build_info()
    return {
        "version": installed,
        # The commit the installed build was made from (``openprogram/
        # _build_info.py``, written by the evaluation launcher); ``None``
        # for a source checkout.
        "commit": built.get("commit"),
        "built_at": built.get("built_at"),
        "no_web": run.no_web,
        # The tool result bound of the run's model (tool-output design):
        # it sets how many tokens a tool result may cost, so two runs with
        # different bounds are not compared as equals.
        "tool_output_weight": _tool_output_weight(run.model),
        "effort_requested": run.effort,
        "permission_mode": run.permission_mode,
        "run_note_sha256": _sha256(RUN_NOTE),
        "requests_seen": len(run.sent),
        "sent_model": first.get("model"),
        "sent_effort": first.get("effort"),
        "sent_include": first.get("include"),
        "sent_store": first.get("store"),
        "tools": tools,
        "tools_sha256": first.get("tools_sha256"),
        "web_tools": None if tools is None else [name for name in tools if "web" in name.lower()],
        "system_prompt_sha256": first.get("system_sha256"),
    }


def _pair_sent(rows: list[dict[str, Any]], sent: list[dict[str, Any]]) -> list[dict | None]:
    """For each ledger request row, the noted request it answered: the
    latest one of the same execution and kind sent before the row was
    recorded and not taken by an earlier row. A request sent again after a
    failed attempt is the one answered, so the attempt before it is passed
    over."""
    used: set[int] = set()
    paired: list[dict | None] = []
    for row in rows:
        match = None
        for index in range(len(sent) - 1, -1, -1):
            fact = sent[index]
            if (index in used or fact["time"] > float(row.get("ts") or 0)
                    or fact.get("execution_id") != row.get("execution_id")
                    or (fact.get("call_kind") or "chat") != row.get("call_kind")):
                continue
            match = index
            break
        if match is not None:
            used.add(match)
        paired.append(sent[match] if match is not None else None)
    return paired


def write_transcript_file(path: str | os.PathLike, run: PrintRun,
                          events: list[dict[str, Any]] | None = None) -> None:
    """Write the run's transcript to ``path``, one JSON record per line
    (``events``: the run's events when already read)."""
    from openprogram.interfaces.cli.print_mode import print_events

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    records = (transcript_records(run) if events is None
               else [print_events.transcript_record(event) for event in events])
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    replace_file(temporary, target)


__all__ = [
    "ENDED_CANCELLED", "ENDED_COMPLETED", "ENDED_FAILED",
    "ENDED_NOT_STARTED", "ModelUnavailableError", "PrintRun", "PrintTurnError",
    "RUN_NOTE", "chosen_model", "use_model", "hold_signals",
    "interrupt_on_terminate", "leave", "leave_stopped",
    "non_interactive_owner", "signals_noted", "stop_commands", "stopping",
    "no_web_fields", "parse_model", "run_executions", "run_in_process", "run_outcome",
    "transcript_from_state", "transcript_records",
    "TRANSPORT_IN_PROCESS", "TRANSPORT_WORKER",
    "usage_summary", "EFFORTS", "recording_requests", "run_header", "wire_effort", "wire_facts",
    "write_transcript_file", "write_usage_file",
]

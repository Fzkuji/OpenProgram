"""Terminal chat for bare ``openprogram``.

Welcome banner (tools + skills inventory) followed by a chat loop.
Slash commands (``/help``, ``/web``, ``/quit``, ...) are handled
locally; non-slash input goes through the same chat runtime the Web UI
uses, so behaviour stays aligned.

The bulk of this module's logic — banner inventory, slash-command
handlers, per-turn exec — lives in this application's ``repl`` package and is
re-exported here so existing call sites (``scripts/maintenance/profile_startup.py``,
``openprogram.operations.setup``, ``openprogram.interfaces.cli.commands.conversations.chat``, tests) keep
working unchanged.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys


# Re-exports — every external caller imports through ``openprogram.interfaces.cli.chat``.
from openprogram.interfaces.cli.repl.setup import (  # noqa: E402,F401
    _get_chat_runtime,
    _reset_provider_cache,
    _prompt_first_run_setup,
)
from openprogram.interfaces.cli.repl.banner import (  # noqa: E402,F401
    _tool_inventory,
    _skill_inventory,
    _function_inventory,
    _application_inventory,
    _section_text,
    _print_banner,
)
from openprogram.interfaces.cli.repl.handlers import (  # noqa: E402,F401
    register_repl_builtins,
    _parse_kv_args,
    _handle_slash,
    _handle_login,
    _handle_model,
    _handle_agent_switch,
    _handle_new_session,
    _handle_copy,
    _handle_attach,
    _handle_detach,
    _handle_connections,
)
from openprogram.interfaces.cli.repl.turn import _run_turn_with_history  # noqa: E402,F401

_log = logging.getLogger(__name__)


def _say(run, line: str) -> None:
    """Write the reply line on stdout, for ``--output-format text`` only;
    the other formats write their record at the end (``_finish_output``)."""
    if run is None or getattr(run, "output_format", "text") == "text":
        print(line)


def _version_warning(server: dict) -> None:
    """Say once on stderr when the Worker runs another release than this CLI."""
    from openprogram.interfaces.server.handshake import package_version

    mine, theirs = package_version(), server.get("version")
    if mine and isinstance(theirs, str) and theirs != mine and not getattr(
            _version_warning, "said", False):
        _version_warning.said = True
        print(f"[warning] the Worker runs openprogram {theirs}, this command is {mine}",
              file=sys.stderr)


def _busy(run, exc) -> bool:
    """A refusal because the target branch is running: ``branch_busy``."""
    if getattr(exc, "code", None) not in ("run_active", "branch_busy"):
        return False
    if run is not None:
        run.error_kind = "branch_busy"
    print("[error] the branch is running another turn; pass --at with a message "
          "to start another branch, or wait for it", file=sys.stderr)
    return True


def _print_oneshot_via_worker(endpoint, session_id: str, text: str, *,
                              response_format=None, model: str | None = None,
                              project_id: str | None = None,
                              run=None, effort: str | None = None,
                              permission_mode: str | None = None,
                              branch_id: str | None = None, stream=None,
                              no_web: bool = False) -> None:
    """Run one ``--print`` turn on the worker and print what came back.

    The reply prints as text, or as one JSON line when a response format
    was given (with ``--output-format text``). ``model``, ``effort`` and
    ``permission_mode`` are this turn's own (``turn_*`` in the command). No
    one can answer an approval, so the Worker refuses a call that would ask
    at once. A worker-side error prints to stderr and exits 1; a
    structured-output failure raises the matching ``StructuredOutputError``
    so the command exits with the same code as the in-process path.
    ``run`` (a :class:`~openprogram.interfaces.cli.print_mode.print_turn.PrintRun`)
    records how the turn ended for the usage summary.
    """
    import json as _json
    from openprogram.interfaces.cli.print_mode import print_turn as _print_turn
    from openprogram.interfaces.cli.print_mode.worker_turn import (
        WorkerTurnCancelled,
        WorkerTurnError,
        WorkerTurnStopped,
        run_turn_via_worker,
        structured_output_error,
    )

    def _on_execution(execution_id: str) -> None:
        if run is not None:
            run.execution_id = execution_id
            if stream is not None:
                stream.init(run)

    def _on_server(server: dict) -> None:
        if run is not None:
            run.worker_version = server.get("version")
        _version_warning(server)

    def _on_notice(switch: dict) -> None:
        # Another of the provider's credentials took over: one line on
        # stderr (a stream carries it as a ``credential_switch`` event).
        from openprogram.interfaces.cli.print_mode.print_events import credential_switch_line
        print(f"[notice] {credential_switch_line(switch)}", file=sys.stderr)

    def _on_ack(ack: dict) -> None:
        # The mode the Worker admitted the turn with: ``--permission-mode``,
        # else the branch's own.
        if run is not None and isinstance(ack.get("permission_mode"), str):
            run.permission_mode = ack["permission_mode"]

    try:
        turn = run_turn_via_worker(
            endpoint, session_id, text, response_format=response_format,
            model=model, project_id=project_id, effort=effort,
            permission_mode=permission_mode, branch_id=branch_id, no_web=no_web,
            # No one answers a --print run, terminal or not: the worker
            # refuses what would ask, as the in-process run does.
            non_interactive=True,
            ask=None,
            on_text=(lambda delta: stream.delta(run, "text", delta))
            if stream is not None and run is not None else None,
            # A further Ctrl-C or SIGTERM while the stop waits for the
            # worker is noted, and shortens the wait.
            on_stop=_print_turn.hold_signals,
            # The usage and transcript files name the run's execution.
            on_execution=_on_execution,
            on_ack=_on_ack,
            on_notice=_on_notice if stream is None else None,
            # The record names the Worker's version, whatever then happens.
            on_server=_on_server,
        )
    except WorkerTurnStopped as stop:
        _print_turn.hold_signals()
        if run is not None:
            run.ended = _print_turn.ENDED_CANCELLED
        if not stop.confirmed:
            # Said plainly: the stop was asked for, but the turn may go on.
            message = (f"{stop.detail}; the turn may still be running on the "
                       "worker, and the Web UI shows it")
            if run is not None:
                run.error = message
            print(f"[cancelled] {message}", file=sys.stderr)
        elif stop.status == "completed":
            print("[cancelled] the turn had already finished on the worker; "
                  "the Web UI shows its reply", file=sys.stderr)
        else:
            print("[cancelled]", file=sys.stderr)
        sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
    except (WorkerTurnCancelled, KeyboardInterrupt):
        _print_turn.hold_signals()
        if run is not None:
            run.ended = _print_turn.ENDED_CANCELLED
        print("[cancelled]", file=sys.stderr)
        sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
    except WorkerTurnError as exc:
        if run is not None:
            run.ended = _print_turn.ENDED_FAILED
            run.error = str(exc)
            run.error_reason, run.error_retryable, run.error_kind = (
                exc.reason, exc.retryable, exc.kind)
        if _busy(run, exc):
            sys.exit(1)
        if response_format is not None:
            raise structured_output_error(exc) from exc
        message = str(exc)
        if not message.startswith("[error]"):
            message = f"[error] {message}"
        print(message, file=sys.stderr)
        sys.exit(1)
    if (model or effort or permission_mode) and turn.worker_capabilities and (
            "turn_overrides" not in turn.worker_capabilities):
        print("[warning] the Worker is older than this command and ran the turn with "
              "the conversation's own model, effort and permission mode",
              file=sys.stderr)
    if no_web and turn.worker_capabilities and "no_web" not in turn.worker_capabilities:
        print("[warning] the Worker is older than this command and ran the turn with "
              "its web tools; use --standalone for a turn without them",
              file=sys.stderr)
    if run is not None:
        run.ended = _print_turn.ENDED_COMPLETED
        run.tool_calls = turn.tool_calls
        run.restarted = turn.restarted
        run.reply = turn.reply
        run.local_command = turn.local_command
        if turn.wire_facts:
            run.sent = [fact for fact in turn.wire_facts if isinstance(fact, dict)]
    if response_format is None:
        _say(run, turn.reply)
        return
    value = turn.structured_output
    if value is None and "structured_output" in turn.worker_capabilities:
        # The Worker puts a structured reply in its field: none came.
        from openprogram.models.providers.structured_output import (
            StructuredOutputGenerationError,
        )
        raise StructuredOutputGenerationError(
            "the worker returned no structured output", code="generation_failed",
        )
    if value is None:
        # A worker that predates the structured_output field in the result
        # frame still returns the JSON as the reply text.
        try:
            value = _json.loads(turn.reply)
        except (TypeError, ValueError):
            from openprogram.models.providers.structured_output import (
                StructuredOutputGenerationError,
            )
            raise StructuredOutputGenerationError(
                "the worker returned no structured output", code="generation_failed",
            ) from None
    if run is not None:
        run.structured_output = value
    _say(run, _json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _compact(run, session_id: str, args: dict, *, endpoint=None, agent_id: str | None = None) -> None:
    """``--print "/compact"``: compact the conversation on the Worker, or
    here without one, as the Web UI's ``/compact`` does. Stopped, it reads
    as stopped (exit by signal); failed, exit 1."""
    import threading
    from openprogram.interfaces.cli.print_mode import print_turn as _print_turn
    from openprogram.runtime.conversation.turn_admission import local_commands

    run.local_command = "compact"
    if endpoint is not None:
        from openprogram.interfaces.cli.print_mode.worker_turn import WorkerTurnError, compact_via_worker
        try:
            text = compact_via_worker(endpoint, session_id, **args)
        except KeyboardInterrupt:
            _print_turn.hold_signals()
            run.ended = _print_turn.ENDED_CANCELLED
            print("[cancelled] the compaction may still be running on the worker",
                  file=sys.stderr)
            sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
        except WorkerTurnError as exc:
            run.ended, run.error = _print_turn.ENDED_FAILED, str(exc)
            if _busy(run, exc):
                sys.exit(1)
            print(f"[error] {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        if agent_id is None:
            from openprogram.runtime.agent_registry.builtin_agents import ensure_default_agent
            agent_id = ensure_default_agent().id
        stop = threading.Event()
        box: dict = {}
        done = threading.Event()
        frames: list[str] = []

        def _work() -> None:
            try:
                box["value"] = local_commands.compact(
                    session_id, agent_id=agent_id, cancel_event=stop,
                    on_event=lambda env: frames.append(str(
                        ((env or {}).get("data") or {}).get("content") or ""))
                    if ((env or {}).get("data") or {}).get("type") == "local_command" else None,
                    **args)
            except BaseException as exc:  # noqa: BLE001 - reported on the main thread
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=_work, daemon=True, name="openprogram-print-compact").start()
        try:
            while not done.wait(0.2):
                pass
        except KeyboardInterrupt:
            stop.set()
            noted = _print_turn.hold_signals()
            _print_turn.print_stop.StopClock(noted).wait_for(done)
            run.ended = _print_turn.ENDED_CANCELLED
            print("[cancelled]", file=sys.stderr)
            sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
        if "error" in box:
            exc = box["error"]
            run.ended, run.error = _print_turn.ENDED_FAILED, f"compact failed: {exc}"
            print(f"[error] {run.error}", file=sys.stderr)
            sys.exit(1)
        value = box.get("value") or {}
        covered = value.get("covered_count") if isinstance(value, dict) else None
        text = frames[-1] if frames and not covered else (
            f"Compacted {covered} turns into a summary." if covered else "Compacted.")
    run.ended = _print_turn.ENDED_COMPLETED
    run.reply = text
    _say(run, text)


def _print_oneshot_in_process(session_id: str, text: str, *, response_format=None,
                              model: str | None = None, run,
                              effort: str | None = None,
                              permission_mode: str | None = None,
                              target=None, project_binding: bool = True,
                              no_web: bool = False, stream=None) -> None:
    """Run one ``--print`` turn in this process and print its reply.

    A slash command is taken as the Worker takes it
    (``runtime/conversation/turn_admission/local_commands.py``): a registered command
    answers here and no model turn runs; one that hands back text goes on
    with it; ``/task`` and ``/merge`` mark the turn; any other text, an
    unknown ``/name`` included, goes to the model as written.

    Success prints the reply (or the structured output as one JSON line) on
    stdout. A failed turn prints ``[error] ...`` on stderr and exits 1, a
    cancelled one exits by its signal; a structured-output failure raises
    the matching ``StructuredOutputError`` for the command's exit code. With
    no model chosen yet, the command says what to run and exits 1.
    """
    import json as _json
    from openprogram.interfaces.cli.print_mode import print_turn as _print_turn
    from openprogram.runtime.agent_registry.builtin_agents import ensure_default_agent
    from openprogram.runtime.conversation.turn_admission import local_commands

    agent = ensure_default_agent()
    command = local_commands.resolve(text, session_id)
    starts_goal = False
    if command is not None:
        outcome = local_commands.run(command, session_id)
        if outcome.invoke is not None:
            run.ended, run.error = _print_turn.ENDED_FAILED, (
                f"/{outcome.command} runs on the App's Worker; start the App, or run it there")
            print(f"[error] {run.error}", file=sys.stderr)
            sys.exit(1)
        if outcome.send_text:
            text = outcome.send_text
            starts_goal = outcome.starts_goal
        else:
            run.local_command = outcome.command
            run.ended = _print_turn.ENDED_COMPLETED
            run.reply = outcome.text
            _say(run, outcome.text)
            return
    if _print_turn.chosen_model(agent.id, model) is None:
        # No one answers a --print run, so the setup wizard is never offered.
        run.error = "no model is configured"
        print(
            "[error] No model is configured. Run `openprogram setup`, or connect a "
            "provider with `openprogram providers login <provider> --api-key-stdin` "
            "and pass `--model <provider>/<model>`.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _on_event(envelope) -> None:
        data = (envelope or {}).get("data") if isinstance(envelope, dict) else None
        if not isinstance(data, dict) or data.get("type") != "stream_event":
            return
        event = data.get("event")
        if isinstance(event, dict) and event.get("type") == "text":
            from openprogram.state.reply_text import stream_text
            stream.delta(run, "text", stream_text(event))
        elif isinstance(event, dict) and event.get("type") == "thinking":
            stream.delta(run, "reasoning", str(event.get("text") or ""))

    try:
        _print_turn.run_in_process(
            session_id, text, agent_id=agent.id, model=model,
            response_format=response_format, run=run,
            permission_mode=permission_mode, target=target,
            project_binding=project_binding, no_web=no_web,
            on_event=_on_event if stream is not None else None,
            on_admitted=stream.init if stream is not None else None,
            starts_goal=starts_goal,
            **({"effort": effort} if effort else {}),
        )
    except KeyboardInterrupt:
        print("[cancelled]", file=sys.stderr)
        sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
    except _print_turn.PrintTurnError as exc:
        run.error = str(exc)
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    if run.ended == _print_turn.ENDED_CANCELLED:
        print("[cancelled]", file=sys.stderr)
        sys.exit(_print_turn.print_stop.stopped_exit_code(_print_turn.stop_signal()))
    if run.ended == _print_turn.ENDED_FAILED:
        result = run.result
        code = getattr(result, "structured_error_code", None)
        if response_format is not None and code:
            from openprogram.interfaces.cli.print_mode.worker_turn import (
                WorkerTurnError,
                structured_output_error,
            )
            raise structured_output_error(WorkerTurnError(
                run.error or "", code=code,
                issues=getattr(result, "structured_output_issues", None),
            ))
        message = run.error or "the turn failed"
        print(message if message.startswith("[error]") else f"[error] {message}",
              file=sys.stderr)
        sys.exit(1)
    if response_format is None:
        _say(run, run.reply)
        return
    value = run.structured_output
    if value is None:
        from openprogram.models.providers.structured_output import (
            StructuredOutputGenerationError,
        )
        raise StructuredOutputGenerationError(
            "the turn returned no structured output", code="generation_failed",
        )
    _say(run, _json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _run_oneshot(session_id: str | None, text: str, *, new_session: bool,
                 response_format=None, model: str | None = None,
                 usage_file: str | None = None,
                 transcript_file: str | None = None,
                 effort: str | None = None, **options) -> None:
    """One ``--print`` turn: on the running worker when there is one (and
    ``--standalone`` is not given), otherwise in this process; then the
    output record and files, if asked for.

    The command exits when its turn ends, so no side work (a title, a
    branch name, topic sorting) starts in it: see ``side_work``.
    """
    from openprogram.runtime.conversation import side_work

    with side_work.held():
        _run_oneshot_held(session_id, text, new_session=new_session,
                          response_format=response_format, model=model,
                          usage_file=usage_file, transcript_file=transcript_file,
                          effort=effort, **options)


def _run_oneshot_held(session_id: str | None, text: str, *, new_session: bool,
                      response_format, model: str | None, usage_file: str | None,
                      transcript_file: str | None, effort: str | None = None,
                      standalone: bool = False, output_format: str | None = None,
                      include_partial_messages: bool = False,
                      permission_mode: str | None = None, continue_last: bool = False,
                      new_session_id: str | None = None, at: str | None = None,
                      no_project_binding: bool = False, no_web: bool = False) -> None:
    from openprogram.interfaces.cli.print_mode import print_events
    from openprogram.interfaces.cli.print_mode import print_turn as _print_turn
    from openprogram.interfaces.cli.print_mode.print_session import PrintSessionError, resolve_target
    from openprogram.interfaces.cli.print_mode.worker_turn import resolve_worker
    from openprogram.runtime.conversation.turn_admission import local_commands
    from openprogram.state.store.project.project_store import working_directory_project

    fmt = output_format or "text"
    run = _print_turn.PrintRun(session_id=session_id or "", model=model, effort=effort,
                               permission_mode=permission_mode,
                               output_format=fmt, no_web=no_web)
    stream = (print_events.Stream(partial=include_partial_messages)
              if fmt == "stream-json" else None)
    try:
        with _print_turn.interrupt_on_terminate():
            try:
                target = resolve_target(
                    resume=None if new_session else session_id, continue_last=continue_last,
                    new_session_id=new_session_id, at=at)
            except PrintSessionError as exc:
                run.error = str(exc)
                print(f"[error] {exc}", file=sys.stderr)
                sys.exit(1)
            session_id = run.session_id = target.session_id
            if model:
                try:
                    _print_turn.use_model(model)
                except _print_turn.ModelUnavailableError as exc:
                    provider = model.partition("/")[0]
                    run.error = str(exc)
                    run.error_kind = exc.kind
                    print(
                        f"[error] {exc}. Check the provider's credential "
                        f"(`openprogram providers status {provider}`) and the model id.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            # A one-shot turn goes to the running worker when there is one:
            # the worker holds the App's browser, the channels and the
            # sessions the Web UI shows, none of which a runtime in this
            # process can reach. ``--standalone`` keeps it here.
            endpoint = None if standalone else resolve_worker()
            if endpoint is not None and target.branch_from:
                print("[note] a new branch from a message runs in this process "
                      "(--standalone)", file=sys.stderr)
                endpoint = None
            compact = local_commands.compact_args(text)
            if compact is not None:
                run.transport = (_print_turn.TRANSPORT_WORKER if endpoint is not None
                                 else _print_turn.TRANSPORT_IN_PROCESS)
                _compact(run, session_id, compact, endpoint=endpoint)
                return
            if endpoint is not None:
                run.transport = _print_turn.TRANSPORT_WORKER
                project_id = (working_directory_project(os.getcwd())
                              if target.new and not no_project_binding else None)
                _print_oneshot_via_worker(
                    endpoint, session_id, text, response_format=response_format,
                    model=model, project_id=project_id, run=run, effort=effort,
                    permission_mode=permission_mode, branch_id=target.branch_id,
                    stream=stream, no_web=no_web,
                )
                return
            run.transport = _print_turn.TRANSPORT_IN_PROCESS
            with _print_turn.recording_requests(run):
                _print_oneshot_in_process(
                    session_id, text, response_format=response_format, model=model,
                    run=run, effort=effort, permission_mode=permission_mode,
                    target=target, project_binding=not no_project_binding,
                    no_web=no_web, stream=stream,
                )
    finally:
        # A stopped run's command is on its way out: from here until the
        # process ends a further signal is noted (``hold_signals``), and acted
        # on once the files are written (``leave_stopped``).
        run.stopped_by = _print_turn.stop_signal()
        stopped = run.ended == _print_turn.ENDED_CANCELLED or _print_turn.stopping()
        if stopped:
            _print_turn.hold_signals()
        with contextlib.nullcontext() if stopped else _print_turn.signals_noted():
            _finish_output(run, stream, usage_file=usage_file,
                           transcript_file=transcript_file)
        if stopped:
            _print_turn.leave_stopped(
                run, _print_turn.print_stop.stopped_exit_code(run.stopped_by))


def _finish_output(run, stream, *, usage_file: str | None,
                   transcript_file: str | None) -> None:
    """After the turn: the refused calls on stderr, the files, and the
    ``json`` record or the rest of the ``stream-json`` events on stdout."""
    import json as _json
    from openprogram.interfaces.cli.print_mode import print_events
    from openprogram.interfaces.cli.print_mode import print_turn as _print_turn

    events: list = []
    if run.execution_id:
        try:
            events = print_events.run_events(run)
        except Exception as exc:  # noqa: BLE001 - the reply is already out
            _log.warning("events of run %s unreadable", run.execution_id, exc_info=True)
            print(f"[warning] the run's events could not be read: {exc}", file=sys.stderr)
    run.permission_denials = print_events.permission_denials(events)
    for event in events:
        if event.get("type") == "permission_denied":
            print(print_events.denial_line(event), file=sys.stderr)
    if usage_file:
        try:
            _print_turn.write_usage_file(usage_file, run)
        except Exception as exc:  # noqa: BLE001 - the reply is already out
            print(f"[warning] usage file not written: {exc}", file=sys.stderr)
    if transcript_file:
        try:
            _print_turn.write_transcript_file(transcript_file, run,
                                              events if run.execution_id else None)
        except Exception as exc:  # noqa: BLE001 - the reply is already out
            print(f"[warning] transcript file not written: {exc}", file=sys.stderr)
    if run.output_format == "text":
        return
    try:
        record = {**_print_turn.usage_summary(run), "reply": run.reply,
                  "structured_output": run.structured_output}
    except Exception as exc:  # noqa: BLE001 - the record names what it could not read
        record = {"ended": run.ended, "error": run.error or str(exc),
                  **_print_turn.run_outcome(run), "session_id": run.session_id,
                  "reply": run.reply, "structured_output": run.structured_output}
    if stream is not None:
        stream.finish(run, events, record)
    else:
        print(_json.dumps(record, ensure_ascii=False, default=str), flush=True)


def run_cli_chat(oneshot: str | None = None,
                 resume: str | None = None,
                 tui: bool = True,
                 response_format=None,
                 no_alt_screen: bool = False,
                 screen_reader: bool = False,
                 model: str | None = None,
                 usage_file: str | None = None,
                 transcript_file: str | None = None,
                 effort: str | None = None,
                 **print_options) -> None:
    """Launch the terminal chat.

    ``oneshot`` runs one turn and exits (still persisted so it shows
    up in the sidebar of a later Web UI session). ``model``
    (``provider/id``) pins a new conversation's model for it, and
    ``usage_file`` receives the run's usage summary as JSON, and
    ``transcript_file`` its turn as JSON lines.

    ``resume`` picks up a prior session id under the current default
    agent instead of starting a fresh one. ``print_options`` are the other
    ``--print`` options (``_run_oneshot_held``).

    ``tui`` defaults True on every platform. Ink performs a real raw-input
    capability check at startup; unsupported terminals fall back to Rich.
    ``oneshot`` always uses the Rich path (one-shot doesn't render
    a TUI).
    """
    import uuid as _uuid
    from rich.console import Console
    console = Console()

    if resume:
        session_id = resume
    else:
        session_id = "local_" + _uuid.uuid4().hex[:10]

    # The Ink client talks to the worker over WebSocket and discovers the
    # default agent/model there.  Initialising a second provider runtime in
    # this short-lived launcher makes a Windows cold start do the expensive
    # work twice and delays worker startup long enough to look hung.
    # Initialise the in-process runtime only for one-shot/Rich execution, or
    # after Ink has genuinely failed and needs the fallback.
    if tui and not oneshot:
        try:
            from openprogram.interfaces.cli.ink import run_ink_tui
            run_ink_tui(
                session_id=session_id,
                no_alt_screen=no_alt_screen,
                screen_reader=screen_reader,
            )
            return
        except Exception as e:  # noqa: BLE001
            # cli.py:_maybe_redirect_for_tui() may have dup2'd stdout/stderr
            # to the profile's logs/ink-startup.log on POSIX. The fallback
            # REPL must restore them before it prints or prompts.
            from openprogram.interfaces.cli import application as _cli
            for std_fd, saved_attr in ((1, "_TUI_TTY_OUT"), (2, "_TUI_TTY_ERR")):
                saved = getattr(_cli, saved_attr, None)
                if saved is not None:
                    try:
                        os.dup2(saved, std_fd)
                    except OSError:
                        pass
            console.print(
                f"[yellow]TUI failed to start ({type(e).__name__}: {e}); "
                f"falling back to REPL.[/]"
            )

    if oneshot:
        _run_oneshot(
            resume, oneshot, new_session=not resume,
            response_format=response_format, model=model, usage_file=usage_file,
            transcript_file=transcript_file, effort=effort, **print_options,
        )
        return

    # Provider detection probes 5+ providers (CLI binaries + API hosts)
    # on cold cache; that takes several seconds. Tell the user something
    # is happening so the TUI launch doesn't look frozen.
    with console.status("Detecting providers…", spinner="dots"):
        provider, rt = _get_chat_runtime()
    if rt is None:
        if not _prompt_first_run_setup(console):
            sys.exit(1)
        provider, rt = _get_chat_runtime()
        if rt is None:
            sys.exit(1)
    model_name = getattr(rt, "model", "?")

    from openprogram.runtime.agent_registry.builtin_agents import ensure_default_agent
    agent = ensure_default_agent()

    if resume:
        console.print("[dim]↪ Resuming previous session.[/]")

    # Show the channels worker status without asking the user to start
    # anything: the primary thing this REPL does is chat. Channels are
    # an opt-in feature; we surface the status only if a worker is
    # already running so the user knows their bindings are live.
    try:
        from openprogram.operations.worker import current_worker_pid
        pid = current_worker_pid()
        if pid:
            console.print(
                f"[dim]↪ channels worker running (PID {pid})  "
                f"— bindings active (attach/detach in the Web UI)[/]"
            )
    except Exception:
        # Informational banner only.
        _log.debug("channels worker status unavailable", exc_info=True)

    _print_banner(console, provider, model_name,
                  agent_id=getattr(agent, "id", "") or "",
                  session_id=session_id)

    while True:
        try:
            user_input = console.input("\n[bold bright_blue]❯[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/]")
            return
        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_slash(user_input, console, rt,
                             agent=agent, session_id=session_id):
                return
            continue
        # Stream the reply token-by-token to the terminal. The turn
        # function writes directly to stdout via ``rt.on_stream``; we
        # don't print again after it returns or the text would
        # duplicate. The returned ``reply`` is the canonical full
        # string — passed to TTS which needs the whole utterance.
        reply = _run_turn_with_history(
            agent, session_id, user_input, console=console,
        )
        # Fire-and-forget TTS; no-ops unless tts.provider is set.
        try:
            from openprogram.interfaces.speech.tts import speak
            speak(reply)
        except Exception:
            # TTS is optional; the reply is already printed.
            _log.debug("TTS failed for session %s", session_id, exc_info=True)

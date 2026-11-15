"""``openprogram --print`` as a headless command.

The instruction comes from the argument, standard input or both; usage
errors exit 2, a stop exits 128 + the signal's number; a call that would ask
for approval is refused with a fixed note the model reads, and the run lists
it; ``--output-format json`` / ``stream-json`` give one record or a stream of
versioned events; ``--resume`` / ``--continue`` / ``--session-id`` / ``--at``
name the conversation and the point; ``--model`` is the turn's own;
``--no-project-binding`` writes nothing in the folder; a provider's standard
variable serves when no key is stored; ``/compact`` and registered builtins
run without a model turn.
"""
from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
import types

import pytest

from openprogram.interfaces.cli import chat as chat_module
from openprogram.interfaces.cli import print_events, print_input, print_session, print_stop
from openprogram.interfaces.cli import print_turn, worker_turn
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    TextContent,
    ToolCall,
    Usage,
)
from tests.component.cli.test_oneshot_via_worker import SESSION, fake_worker
from tests.component.cli.test_print_turn import (  # noqa: F401 - fixtures
    _STUCK_RUN,
    MODEL,
    _never_in_a_real_profile,
    _this_checkout,
    provider,
)


# ---------------------------------------------------------------------------
# The instruction
# ---------------------------------------------------------------------------

class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_a_dash_or_no_value_reads_the_whole_of_stdin():
    assert print_input.resolve_prompt("-", io.StringIO("fix it\n")) == "fix it\n"
    assert print_input.resolve_prompt(None, io.StringIO("from pipe")) == "from pipe"


def test_text_and_a_pipe_append_the_pipe_as_a_block():
    text = print_input.resolve_prompt("explain", io.StringIO("line 1\nline 2"))
    assert text == "explain\n\n<stdin>\nline 1\nline 2\n</stdin>"


def test_a_pipe_with_bytes_on_a_descriptor_is_read(tmp_path):
    path = tmp_path / "in.txt"
    path.write_bytes(b"data\n")
    with open(path, "rb") as stream:
        assert print_input.resolve_prompt("go", stream) == "go\n\n<stdin>\ndata\n</stdin>"


@pytest.mark.skipif(os.name != "posix", reason="an open pipe nobody writes to")
def test_an_open_pipe_that_stays_empty_is_not_waited_for():
    read_end, write_end = os.pipe()
    try:
        with os.fdopen(read_end, "rb", closefd=True) as stream:
            started = time.monotonic()
            assert print_input.resolve_prompt("go", stream) == "go"
            assert time.monotonic() - started < print_input.PIPED_INPUT_GRACE_S + 0.5
    finally:
        os.close(write_end)


def test_a_terminal_is_not_read():
    assert print_input.resolve_prompt("go", _Tty("ignored")) == "go"


@pytest.mark.parametrize("arg, stdin, schema, message", [
    ("-", io.StringIO("x"), True, "cannot both read from stdin"),
    ("-", _Tty(""), False, "needs a prompt"),
    ("-", io.StringIO("  \n"), False, "is empty"),
    ("   ", io.StringIO(""), False, "needs a prompt"),
])
def test_an_instruction_that_cannot_be_formed_is_a_usage_error(arg, stdin, schema, message):
    with pytest.raises(print_input.PromptInputError, match=message):
        print_input.resolve_prompt(arg, stdin, schema_from_stdin=schema)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

def _parse(monkeypatch, *argv):
    from openprogram.interfaces.cli import application

    monkeypatch.setattr(sys, "argv", ["openprogram", *argv])
    return application._parse_arguments()[1]


def test_an_empty_dash_p_is_a_print_run_not_the_chat(monkeypatch):
    from openprogram.interfaces.cli import application

    args = _parse(monkeypatch, "-p", "", "--output-format", "json")
    assert application._runs_print(args)
    assert not application._runs_print(_parse(monkeypatch))
    parser = types.SimpleNamespace(error=lambda message: (_ for _ in ()).throw(
        SystemExit(2)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as left:
        application._run_print_prompt(args, parser, True)
    assert left.value.code == 2


def test_dash_p_is_print_and_alone_reads_stdin(monkeypatch):
    assert _parse(monkeypatch, "-p", "hi").print_prompt == "hi"
    assert _parse(monkeypatch, "-p").print_prompt == "-"
    args = _parse(monkeypatch, "-p", "x", "--output-format", "stream-json",
                  "--include-partial-messages", "--permission-mode", "acceptEdits",
                  "--standalone", "--no-project-binding")
    from openprogram.interfaces.cli.application import _print_options
    assert _print_options(args) == {
        "standalone": True, "output_format": "stream-json", "include_partial_messages": True,
        "permission_mode": "acceptEdits", "no_project_binding": True,
    }


@pytest.mark.parametrize("argv", [
    ("--standalone",),
    ("--output-format", "json"),
    ("-p", "x", "--include-partial-messages"),
    ("-p", "x", "--continue", "--resume", "abc"),
    ("-p", "x", "--session-id", "s1", "--continue"),
    ("-p", "x", "--at", "m1"),
    ("-p", "x", "--output-format", "yaml"),
])
def test_conflicting_options_exit_2(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as left:
        _parse(monkeypatch, *argv)
    assert left.value.code == 2


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------

def test_each_stop_signal_exits_128_plus_its_number():
    assert print_stop.stopped_exit_code("SIGINT") == 130
    assert print_stop.stopped_exit_code("SIGTERM") == 143
    assert print_stop.stopped_exit_code("SIGHUP") == 129
    assert print_stop.stopped_exit_code("SIGBREAK") == 143
    assert print_stop.stopped_exit_code(None) == 130


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize("sig, code", [
    (signal.SIGINT, 130), (signal.SIGTERM, 143), (signal.SIGHUP, 129),
], ids=["sigint", "sigterm", "sighup"])
def test_a_stopped_run_exits_with_its_signal_s_code(tmp_path, sig, code):
    marks, work, home = tmp_path / "marks", tmp_path / "work", tmp_path / "home"
    for folder in (marks, work, home):
        folder.mkdir()
    script = tmp_path / "stuck_run.py"
    script.write_text(_STUCK_RUN, encoding="utf-8")
    env = {**os.environ, **_this_checkout(), "HOME": str(home), "OPENPROGRAM_NO_AUTO_UPDATE": "1",
           "STUCK_MARKS": str(marks), "STUCK_GRACE": "1"}
    child = subprocess.Popen([sys.executable, str(script)], cwd=work, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while not (marks / "stuck").exists():
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < deadline, "the turn never reached its model request"
            time.sleep(0.05)
        child.send_signal(sig)
        _out, err = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert child.returncode == code, err
    assert json.loads((marks / "usage.json").read_text(encoding="utf-8"))["ended"] == "cancelled"


# ---------------------------------------------------------------------------
# Approval no one can give
# ---------------------------------------------------------------------------

def test_a_call_that_would_ask_is_refused_with_the_fixed_note(tmp_path, monkeypatch):
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions import authority, refusal
    from openprogram.runtime.permissions.policy import permission_decision
    from openprogram.state import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()

    async def _exec(call_id, args, cancel, on_update):
        return AgentToolResult(content=[], details={})

    tool = AgentTool(name="bash", description="", parameters={}, label="bash", execute=_exec)
    request = TurnRequest(session_id="s", user_text="", agent_id="main", source="cli",
                          permission_mode="ask", run_note="no one is watching",
                          **{**authority.local_owner_authority(), "speaker_kind": "runtime",
                             "interaction": "non-interactive"})
    decision = permission_decision(tool, request, {"command": "touch made.txt"})
    assert (decision.outcome, decision.code) == ("deny", "NON_INTERACTIVE")
    assert decision.refusal.text.endswith(refusal.NON_INTERACTIVE_TEXT)
    assert "touch made.txt" in decision.refusal.text


class _OneBash:
    """Asks for one bash command, then answers with what it was told."""

    def __init__(self) -> None:
        self.calls = 0
        self.result = ""

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        message = AssistantMessage(content=[], api=model.api, provider=model.provider,
                                   model=model.id, stop_reason="stop", timestamp=0,
                                   usage=Usage(input=10, output=1, total_tokens=11))
        yield EventStart(partial=message)
        if not context.tools:
            message.content = [TextContent(text="Title")]
            yield EventDone(reason="stop", message=message)
            return
        self.calls += 1
        if self.calls == 1:
            message.content = [ToolCall(id="b1", name="bash",
                                        arguments={"command": "touch made.txt"})]
            message.stop_reason = "toolUse"
        else:
            for item in context.messages or []:
                if getattr(item, "role", None) == "toolResult":
                    self.result = "".join(getattr(p, "text", "") or "" for p in item.content)
            message.content = [TextContent(text="went on without it")]
        yield EventDone(reason=message.stop_reason, message=message)


def _install(monkeypatch, fake):
    from openprogram.models.providers.catalog import api_registry
    from tests.component.cli.test_print_turn import API

    monkeypatch.setitem(api_registry._registry, API, fake)


def test_a_refused_call_is_told_to_the_model_listed_and_named_on_stderr(
        provider, monkeypatch, tmp_path, capsys):
    provider(0)
    fake = _OneBash()
    _install(monkeypatch, fake)

    chat_module.run_cli_chat(oneshot="make the file", tui=False, model=MODEL,
                             permission_mode="ask", output_format="json")

    out = capsys.readouterr()
    record = json.loads(out.out.strip().splitlines()[-1])
    from openprogram.runtime.permissions.refusal import NON_INTERACTIVE_TEXT
    assert NON_INTERACTIVE_TEXT in fake.result
    assert not (tmp_path / "task" / "made.txt").exists()
    assert record["ended"] == "completed" and record["reply"] == "went on without it"
    assert [(d["tool"], d["code"]) for d in record["permission_denials"]] == [
        ("bash", "NON_INTERACTIVE")]
    assert "permission denied: bash (touch made.txt)" in out.err


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

def test_json_prints_one_record_with_the_reply(provider, capsys):
    provider(1)
    chat_module.run_cli_chat(oneshot="write", tui=False, model=MODEL, output_format="json")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["reply"] == "wrote 1 files" and record["ended"] == "completed"
    assert record["permission_denials"] == [] and record["local_command"] is None


def test_stream_json_starts_with_init_ends_with_result_and_counts_every_line(provider, capsys):
    provider(2)
    chat_module.run_cli_chat(oneshot="write", tui=False, model=MODEL,
                             output_format="stream-json")
    events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert events[0]["type"] == "init" and events[-1]["type"] == "result"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert {event["schema_version"] for event in events} == {print_events.SCHEMA_VERSION}
    assert events[0]["data"]["transport"] == print_turn.TRANSPORT_IN_PROCESS
    assert events[-1]["data"]["reply"] == "wrote 2 files"
    assert "run" not in [event["type"] for event in events]
    assert not any(event["type"].endswith("_delta") for event in events)


def test_partial_deltas_are_written_only_when_asked_for():
    run = types.SimpleNamespace(transport="worker", worker_version=None, model=None, effort=None,
                                permission_mode=None, session_id="s", execution_id="e", sent=[])
    quiet, loud = io.StringIO(), io.StringIO()
    print_events.Stream(quiet).delta(run, "text", "hel")
    stream = print_events.Stream(loud, partial=True)
    stream.delta(run, "text", "hel")
    stream.delta(run, "reasoning", "hmm")
    stream.finish(run, [], {"reply": "hello"})
    stream.delta(run, "text", "late")
    assert quiet.getvalue() == ""
    kinds = [json.loads(line)["type"] for line in loud.getvalue().splitlines()]
    assert kinds == ["init", "text_delta", "reasoning_delta", "result"]


def test_the_worker_s_ack_names_the_permission_mode_of_the_run(monkeypatch):
    """On the Worker the run's ``permission_mode`` is the one the ack says
    the turn runs with, ``--permission-mode`` until then."""
    from tests.component.cli.test_oneshot_via_worker import _ack, _response

    def script(connection, command):
        assert command["turn_permission_mode"] == "ask"
        _ack(connection, command, permission_mode="ask")
        _response(connection, SESSION, type="result", content="ok", msg_id="u1")

    out = io.StringIO()
    stream = print_events.Stream(out)
    run = print_turn.PrintRun(session_id=SESSION)
    with fake_worker(script) as (endpoint, _seen):
        chat_module._print_oneshot_via_worker(endpoint, SESSION, "go", run=run,
                                              permission_mode="ask", stream=stream)
    init = json.loads(out.getvalue().splitlines()[0])
    assert init["type"] == "init" and init["data"]["permission_mode"] == "ask"
    assert run.permission_mode == "ask"


def test_a_denial_line_names_the_tool_and_what_it_touched():
    event = {"type": "permission_denied",
             "data": {"tool": "write", "arguments": {"file_path": "/etc/x"}, "code": "C"}}
    assert print_events.denial_line(event) == "permission denied: write (/etc/x)"
    assert print_events.permission_denials([event, {"type": "tool_result", "data": {}}]) == [
        {"tool": "write", "tool_call_id": None, "code": "C", "rule": None}]


# ---------------------------------------------------------------------------
# Model, project binding, credentials
# ---------------------------------------------------------------------------

def test_the_named_model_is_the_turn_s_and_the_folder_gets_no_marker(provider, tmp_path, capsys):
    from openprogram.runtime.context.session_db import default_db

    provider(0)
    chat_module.run_cli_chat(oneshot="hi", tui=False, model=MODEL, output_format="json",
                             no_project_binding=True, new_session_id="local_unbound1")
    record = json.loads(capsys.readouterr().out.strip())
    assert record["model"] == MODEL and record["session_id"] == "local_unbound1"
    row = default_db().get_session("local_unbound1") or {}
    assert not row.get("model_override") and not row.get("provider_override")
    # Kept in the profile's default project, not one for this folder.
    assert row.get("project_id") in (None, "", "default")
    assert not (tmp_path / "task" / ".openprogram").exists()


def test_a_standard_variable_serves_only_without_a_stored_key(monkeypatch):
    from openprogram.models.auth import resolver
    from openprogram.models.providers.settings import env_api_keys

    monkeypatch.setattr(env_api_keys, "_env_keys_in_process", False)
    monkeypatch.setenv("OPENAI_API_KEY", "Bearer sk-env")
    monkeypatch.setattr(resolver, "resolve_store_api_key_sync", lambda _p: None)
    assert env_api_keys.resolve_provider_key("openai") is None
    env_api_keys.read_env_keys_in_this_process()
    assert env_api_keys.resolve_provider_key("openai") == "sk-env"
    monkeypatch.setattr(resolver, "resolve_store_api_key_sync", lambda _p: "sk-stored")
    assert env_api_keys.resolve_provider_key("openai") == "sk-stored"


# ---------------------------------------------------------------------------
# Which conversation
# ---------------------------------------------------------------------------

def test_a_conversation_that_is_not_there_is_not_created(provider, capsys):
    provider(0)
    with pytest.raises(SystemExit) as left:
        chat_module.run_cli_chat(oneshot="hi", tui=False, model=MODEL, resume="local_nothere")
    assert left.value.code == 1
    assert "No conversation local_nothere" in capsys.readouterr().err


def test_a_new_conversation_id_in_use_is_refused(monkeypatch):
    monkeypatch.setattr(print_session, "_exists", lambda sid: sid == "taken")
    with pytest.raises(print_session.PrintSessionError, match="already exists"):
        print_session.resolve_target(new_session_id="taken")
    assert print_session.resolve_target(new_session_id="free") == print_session.PrintTarget(
        "free", True)


def test_continue_takes_the_latest_conversation_of_this_folder(tmp_path):
    """Real store and registry: the session list's rows carry no project,
    so the folder's conversations are the ones its project lists."""
    from openprogram.runtime.context.session_db import default_db

    mine, other, empty = tmp_path / "mine", tmp_path / "other", tmp_path / "empty"
    for folder in (mine, other, empty):
        folder.mkdir()
    db = default_db()
    for sid, folder, at in (("older", mine, 100.0), ("newest_mine", mine, 200.0),
                            ("elsewhere", other, 300.0)):
        db.create_session(sid, "main", project_path=str(folder),
                          created_at=at, updated_at=at)
    assert print_session.resolve_target(continue_last=True, cwd=mine).session_id == "newest_mine"
    assert print_session.resolve_target(continue_last=True, cwd=other).session_id == "elsewhere"
    with pytest.raises(print_session.PrintSessionError, match="No conversation to continue"):
        print_session.resolve_target(continue_last=True, cwd=empty)


def _branches(monkeypatch, *, known=()):
    row = {"active_branch_id": "main",
           "branch_refs": {"main": {"head_id": "m9"}, "side": {"head_id": "s4"}}}
    monkeypatch.setattr(print_session, "_exists", lambda sid: True)
    monkeypatch.setattr(print_session, "_db", lambda: types.SimpleNamespace(
        get_session=lambda sid: row, message_exists=lambda sid, ref: ref in known))


def test_at_a_tip_runs_on_that_branch_and_inside_one_starts_a_branch(monkeypatch):
    _branches(monkeypatch, known=("m3",))
    tip = print_session.resolve_target(resume="c", at="s4")
    assert (tip.branch_id, tip.selected) == ("side", False)
    assert print_turn.target_fields(tip) == {"branch_id": "side", "branch_from": "s4",
                                             "advance_head": False, "side_branch": True}
    selected = print_session.resolve_target(resume="c", at="main")
    assert print_turn.target_fields(selected) == {"branch_id": "main"}
    inside = print_session.resolve_target(resume="c", at="m3")
    assert print_turn.target_fields(inside) == {"branch_from": "m3"}
    with pytest.raises(print_session.PrintSessionError, match="No branch or message"):
        print_session.resolve_target(resume="c", at="nowhere")


# ---------------------------------------------------------------------------
# Commands a run answers itself
# ---------------------------------------------------------------------------

def test_compact_reads_its_arguments():
    from openprogram.runtime.conversation import local_commands

    assert local_commands.compact_args("/compact") == {}
    assert local_commands.compact_args("/compact 4000") == {"keep_recent_tokens": 4000}
    assert local_commands.compact_args("/compact soon") is None
    assert local_commands.compact_args("compact") is None


def test_compact_on_the_worker_sends_the_compact_action():
    def script(connection, command):
        assert command == {"action": "compact", "session_id": SESSION, "keep_recent_tokens": 10}
        connection.send_frame({"type": "chat_response", "data": {
            "session_id": SESSION, "type": "compaction_finished", "content": "Compacted 3 turns."}})

    with fake_worker(script) as (endpoint, _seen):
        assert worker_turn.compact_via_worker(
            endpoint, SESSION, keep_recent_tokens=10) == "Compacted 3 turns."


def test_a_compaction_the_worker_refuses_is_an_error():
    def script(connection, command):
        connection.send_frame({"type": "chat_response", "data": {
            "session_id": SESSION, "type": "error", "content": "busy", "code": "run_active"}})

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnError) as raised:
            worker_turn.compact_via_worker(endpoint, SESSION)
    assert raised.value.code == "run_active"


def test_compact_in_this_process_runs_without_a_model_turn(provider, monkeypatch, capsys):
    from openprogram.runtime.conversation import local_commands

    fake = provider(0)
    seen = {}

    def compact(session_id, *, agent_id, cancel_event=None, on_event=None, **args):
        seen.update(session_id=session_id, args=args)
        return {"covered_count": 0}

    monkeypatch.setattr(local_commands, "compact", compact)
    chat_module.run_cli_chat(oneshot="/compact 500", tui=False, output_format="json")
    record = json.loads(capsys.readouterr().out.strip())
    assert seen["args"] == {"keep_recent_tokens": 500}
    assert record["local_command"] == "compact" and record["ended"] == "completed"
    assert fake.calls == 0


def _builtin(monkeypatch, out):
    from openprogram.runtime.commands import dispatch

    def invoke(text, *, session_id="", cwd=None):
        if not text.startswith("/hello"):
            return dispatch.InvokeResult(ok=False, kind="error", error="unknown")
        return dispatch.InvokeResult(ok=True, kind="local", command_name="hello",
                                     local_handler=lambda ctx, raw: out, raw_args="")

    monkeypatch.setattr(dispatch, "invoke", invoke)


def test_a_registered_builtin_answers_without_a_model_turn(provider, monkeypatch, capsys):
    fake = provider(0)
    _builtin(monkeypatch, {"text": "hi there"})
    chat_module.run_cli_chat(oneshot="/hello", tui=False, model=MODEL)
    assert capsys.readouterr().out == "hi there\n"
    assert fake.calls == 0


def test_an_unknown_slash_name_goes_to_the_model_as_written(provider, monkeypatch, capsys):
    fake = provider(0)
    _builtin(monkeypatch, {"text": "unused"})
    chat_module.run_cli_chat(oneshot="/nosuch thing", tui=False, model=MODEL)
    assert capsys.readouterr().out == "wrote 0 files\n"
    assert fake.calls == 1 and "/nosuch thing" in fake.user_texts[0]


# ---------------------------------------------------------------------------
# The Worker's side
# ---------------------------------------------------------------------------

def test_the_worker_reads_turn_keys_as_this_turn_s_only(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat import turn as turn_module
    from openprogram.interfaces.server.ws_actions.chat.turn_request import (
        TURN_OVERRIDES,
        _ChatRequest,
        read_turn_overrides,
    )
    from openprogram.models.providers.catalog import enabled_models

    values = read_turn_overrides({"turn_model": "p/m", "turn_effort": "low",
                                  "turn_permission_mode": "plan"})
    assert (values["turn_model"], values["turn_effort"], values["turn_permission_mode"]) == (
        "p/m", "low", "plan")
    sticky = {o.attribute: o.sticky for o in TURN_OVERRIDES}
    assert not any(sticky[name] for name in ("turn_model", "turn_effort", "turn_permission_mode"))

    used = []
    monkeypatch.setattr(enabled_models, "use_model_in_process",
                        lambda p, m: used.append((p, m)) or True)
    req = _ChatRequest(cmd={}, text="go", session_id="s", agent_id=None, thinking_effort=None,
                       tools_flag=None, web_search_flag=None, permission_mode=None,
                       sandbox_flag=None, surface_ref=None, service_tier=None,
                       response_format=None, attachments=None,
                       **{key: values[key] for key in ("turn_model", "turn_effort",
                                                       "turn_permission_mode")})
    assert turn_module._one_turn_fields(req) == {
        "model_override": "p/m", "thinking_effort": "low", "permission_mode": "plan"}
    assert used == [("p", "m")]


def test_a_turn_model_on_an_agent_trial_replaces_the_trial_s_saved_model(monkeypatch):
    """``--model`` with an Agent: the trial binding names the saved model and
    the frame names this turn's; the turn's wins instead of the two colliding
    as one keyword twice."""
    from openprogram.interfaces.server.ws_actions import agent_invocation
    from openprogram.interfaces.server.ws_actions.chat import turn as turn_module
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest
    from openprogram.models.providers.catalog import enabled_models
    from openprogram.runtime.conversation import production_driver, turn_input
    from openprogram.runtime.permissions import rule

    monkeypatch.setattr(enabled_models, "use_model_in_process", lambda p, m: True)
    monkeypatch.setattr(agent_invocation, "request_agent_invocation", lambda sid, inv: {
        "profile_snapshot": {"id": "keeper"},
        "memory_policy_override": {"mode": "read_only"},
        "model_override": "trial/saved"})
    built = {}

    def build(session_id, text, **fields):
        built.update(fields)
        return types.SimpleNamespace(run_note=None, branch_from=None)

    monkeypatch.setattr(turn_input, "build_turn_request", build)
    monkeypatch.setattr(rule, "load_merged_rules", lambda sid: [])
    monkeypatch.setattr(turn_module, "_db_agent_id", lambda sid: "keeper")

    class Adapter:
        def __init__(self, **kw):
            pass

        def admit(self, request, **kw):
            return types.SimpleNamespace(execution_id="exec-1")

    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", Adapter)
    server = types.SimpleNamespace(_slot_branch=lambda sid, mid: "b1",
                                   _release_run_reservation=lambda sid, mid: None)
    req = _ChatRequest(cmd={}, text="go", session_id="s", agent_id="keeper",
                       thinking_effort=None, tools_flag=None, web_search_flag=None,
                       permission_mode=None, sandbox_flag=None, surface_ref=None,
                       service_tier=None, response_format=None, attachments=None,
                       turn_model="p/m")
    turn = turn_module._admit_turn(server, req, {}, "m1", None, "default", {"raw": "go"},
                                   {}, None, lambda ctx: None)
    assert turn.execution_id == "exec-1"
    assert built["model_override"] == "p/m"
    assert built["profile_snapshot"] == {"id": "keeper"}
    assert built["branch_id"] == "b1"


def test_wire_facts_of_a_watched_execution_are_taken_once(monkeypatch):
    from openprogram.models.providers.recording import wire_facts

    monkeypatch.setattr(wire_facts, "wire_facts", lambda request: {"model": request})
    from openprogram.models.usage import context as usage_context

    current = {"id": "exec-w"}
    monkeypatch.setattr(usage_context, "call_context", lambda: types.SimpleNamespace(
        execution_id=current["id"], call_kind="chat"))
    wire_facts.watch("exec-w", "msg-w")
    wire_facts._note("p/m")
    current["id"] = "exec-other"
    wire_facts._note("p/other")
    facts = wire_facts.take("msg-w")
    assert [fact["model"] for fact in facts] == ["p/m"]
    assert wire_facts.take("msg-w") is None


def test_the_relay_reads_wire_facts_from_the_result_frame():
    def script(connection, command):
        assert command["turn_model"] == "p/m" and command["turn_effort"] == "high"
        assert "agent_overrides" not in command
        connection.send_frame({"type": "chat_ack", "data": {
            "session_id": SESSION, "msg_id": "u1", "execution_id": "exec1",
            "client_request_id": command.get("client_request_id")}})
        connection.send_frame({"type": "chat_response", "data": {
            "session_id": SESSION, "type": "result", "content": "ok", "msg_id": "u1",
            "wire_facts": [{"model": "p/m", "effort": "high"}]}})

    with fake_worker(script) as (endpoint, _seen):
        turn = worker_turn.run_turn_via_worker(endpoint, SESSION, "go", model="p/m",
                                               effort="high")
    assert turn.wire_facts == [{"model": "p/m", "effort": "high"}]


def test_required_memory_that_failed_is_not_retried():
    from openprogram.models.providers.utils.errors import taxonomy_fields
    from openprogram.state.memory.policy import MemoryPolicyError

    assert taxonomy_fields(MemoryPolicyError("down")) == ("unknown", False, None)


# ---------------------------------------------------------------------------
# Two standalone runs on one profile
# ---------------------------------------------------------------------------

_QUICK_RUN = r'''
import os, sys
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, ModelCost, TextContent)

class Quick:
    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        message = AssistantMessage(content=[TextContent(text="done")], api=model.api,
                                   provider=model.provider, model=model.id,
                                   stop_reason="stop", timestamp=0)
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)

api_registry._registry["print-quick-api"] = Quick()
ENABLED_MODELS["quick/model"] = Model(
    id="model", name="quick", api="print-quick-api", provider="quick",
    base_url="http://print-quick.invalid",
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.0, cache_write=0.0, source="configured"))
from openprogram.interfaces.cli import main
sys.argv = ["openprogram", "-p", "hi", "--standalone", "--model", "quick/model",
            "--session-id", sys.argv[1], "--output-format", "json"]
main()
'''


def test_two_standalone_runs_on_one_profile_both_finish(tmp_path):
    work, home = tmp_path / "work", tmp_path / "home"
    work.mkdir()
    home.mkdir()
    script = tmp_path / "quick_run.py"
    script.write_text(_QUICK_RUN, encoding="utf-8")
    env = {**os.environ, **_this_checkout(), "HOME": str(home), "OPENPROGRAM_NO_AUTO_UPDATE": "1"}
    children = [subprocess.Popen([sys.executable, str(script), f"local_both{n}"], cwd=work,
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for n in (1, 2)]
    results = [child.communicate(timeout=120) for child in children]
    for child, (out, err) in zip(children, results):
        assert child.returncode == 0, err
        record = json.loads(out.strip().splitlines()[-1])
        assert record["ended"] == "completed" and record["reply"] == "done"
    assert sorted(json.loads(out.strip().splitlines()[-1])["session_id"]
                  for out, _err in results) == ["local_both1", "local_both2"]

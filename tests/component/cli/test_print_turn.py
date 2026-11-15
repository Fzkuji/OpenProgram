"""A ``--print`` turn run in this process.

Without a worker, ``openprogram --print`` admits and activates a turn through
the conversation turn path, as a channel or ACP turn does. The model here is
a fake API provider registered for the test, so the real turn path runs (tool
loop, tools, persistence, usage ledger) with no network: it goes on past the
twenty rounds a library ``runtime.exec`` call stops at, its tools work in the
directory the command runs in, no approval is waited for, a failure exits
non-zero with the error on stderr, and the usage file reports the run.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import sys
import threading
import time
import types

import pytest

from openprogram.interfaces.cli import chat as chat_module
from openprogram.interfaces.cli import print_events, print_stop, print_turn, worker_turn
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventError,
    EventStart,
    Model,
    ModelCost,
    TextContent,
    ToolCall,
    Usage,
)

API = "print-turn-test-api"
PROVIDER = "printtest"
MODEL_ID = "fake-model"
MODEL = f"{PROVIDER}/{MODEL_ID}"


def _last_user_text(messages) -> str:
    for message in reversed(messages or []):
        if getattr(message, "role", None) != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        return "".join(getattr(part, "text", "") or "" for part in content)
    return ""


class FakeProvider:
    """Calls ``write`` once per round for ``rounds`` rounds, then answers.

    With ``fail_at`` the provider reports an error on that call instead.
    """

    def __init__(self, rounds: int, *, fail_at: int | None = None,
                 delay: float = 0.0, read: str | None = None) -> None:
        self.rounds = rounds
        self.fail_at = fail_at
        self.delay = delay
        # With ``read``, each round reads that file instead of writing one.
        self.read = read
        self.calls = 0
        # The last user message's text the model was sent, per round.
        self.user_texts: list[str] = []
        # The text of every tool result the model was sent, by call id.
        self.tool_results: dict[str, str] = {}
        # Requests beside the turn (a title, a branch name, topic sorting).
        self.side_calls = 0

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        if not context.tools:
            # A call beside the turn (the conversation's title): a short
            # answer reporting no tokens, so the ledger holds the turn's
            # own requests only.
            self.side_calls += 1
            message = AssistantMessage(
                content=[TextContent(text="Title")], api=model.api,
                provider=model.provider, model=model.id, stop_reason="stop", timestamp=0)
            yield EventStart(partial=message)
            yield EventDone(reason="stop", message=message)
            return
        self.calls += 1
        n = self.calls
        self.user_texts.append(_last_user_text(context.messages))
        for message in context.messages or []:
            if getattr(message, "role", None) == "toolResult":
                self.tool_results[message.tool_call_id] = "".join(
                    getattr(part, "text", "") or "" for part in message.content)
        if self.delay:
            await asyncio.sleep(self.delay)
        message = AssistantMessage(
            content=[], api=model.api, provider=model.provider, model=model.id,
            stop_reason="stop", timestamp=0,
            usage=Usage(input=100, output=10, cache_read=50, total_tokens=160),
        )
        yield EventStart(partial=message)
        if self.fail_at is not None and n >= self.fail_at:
            message.stop_reason = "error"
            message.error_message = "upstream 500"
            yield EventError(reason="error", error=message)
            return
        if n <= self.rounds and self.read:
            message.content = [ToolCall(
                id=f"call{n}", name="read", arguments={"file_path": self.read},
            )]
            message.stop_reason = "toolUse"
        elif n <= self.rounds:
            message.content = [ToolCall(
                id=f"call{n}", name="write",
                arguments={"file_path": f"round_{n}.txt", "content": str(n)},
            )]
            message.stop_reason = "toolUse"
        else:
            message.content = [TextContent(text=f"wrote {n - 1} files")]
        yield EventDone(reason=message.stop_reason, message=message)


@pytest.fixture(autouse=True)
def _never_in_a_real_profile():
    """These tests run real turns. Run outside the repository's test tree (a
    copy in a scratch folder, ``--noconftest``) they would write their fake
    conversations and projects into the person's own data, so they refuse."""
    import pwd

    from openprogram.state.paths import get_state_dir

    home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    state = os.path.realpath(str(get_state_dir()))
    real = [os.path.join(home, name) for name in (".openprogram", ".openprogram-dev")]
    if any(state == folder or state.startswith(folder + os.sep) for folder in real):
        pytest.fail(f"refusing to run a real turn against {state}: run this file from the "
                    "repository's tests/ tree, where conftest.py gives each test its own data folder")
    yield


@pytest.fixture
def provider(monkeypatch, tmp_path):
    """Register the fake provider and its model; run in a fresh directory."""
    def install(rounds: int, **kwargs) -> FakeProvider:
        fake = FakeProvider(rounds, **kwargs)
        monkeypatch.setitem(api_registry._registry, API, fake)
        monkeypatch.setitem(ENABLED_MODELS, MODEL, Model(
            id=MODEL_ID, name="fake", api=API, provider=PROVIDER,
            base_url="http://print-turn.invalid",
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.1, cache_write=0.0,
                           source="configured"),
        ))
        return fake

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    work = tmp_path / "task"
    work.mkdir()
    monkeypatch.chdir(work)
    return install


def test_print_runs_past_twenty_tool_rounds_in_the_working_directory(provider, tmp_path, capsys):
    fake = provider(25)
    usage = tmp_path / "usage.json"

    chat_module.run_cli_chat(oneshot="write the files", tui=False, model=MODEL,
                             usage_file=str(usage))

    out = capsys.readouterr()
    assert out.out == "wrote 25 files\n"
    assert fake.calls == 26
    work = tmp_path / "task"
    assert sorted(p.name for p in work.glob("round_*.txt")) == sorted(
        f"round_{n}.txt" for n in range(1, 26))

    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert summary["ended"] == "completed"
    assert summary["error"] is None
    assert summary["model"] == MODEL
    assert summary["agent_rounds"] == 26
    assert summary["tool_calls"] == 25
    assert summary["model_requests"] == 26
    assert summary["input_tokens"] == 2600
    assert summary["cached_input_tokens"] == 1300
    assert summary["output_tokens"] == 260
    # 26 * (100 * $1 + 10 * $2 + 50 * $0.1) per million tokens
    assert summary["cost_usd"] == pytest.approx(26 * 125 / 1_000_000)
    assert summary["by_model"][0]["model"] == MODEL
    # The command exits when the turn ends: nothing is started beside it
    # that the exit would cut off mid-request.
    assert fake.side_calls == 0
    from openprogram.runtime.conversation import side_work
    assert side_work.allowed()


def test_every_round_tells_the_model_no_one_will_answer(provider, tmp_path, capsys):
    # A model that thinks someone is watching ends its turn asking whether
    # to go on, which ends a --print run with the task half done.
    fake = provider(3)

    chat_module.run_cli_chat(oneshot="write the files", tui=False, model=MODEL,
                             usage_file=str(tmp_path / "usage.json"))

    assert capsys.readouterr().out == "wrote 3 files\n"
    assert len(fake.user_texts) == 4
    # Once per request, and the same bytes every round, so the provider's
    # cached prefix still matches on the next round.
    assert [text.count(print_turn.RUN_NOTE) for text in fake.user_texts] == [1, 1, 1, 1]
    assert len(set(fake.user_texts)) == 1
    # The date the turn was sent on first, the note before the message (the
    # sections beside the message, such as tool_availability, sit between).
    text = fake.user_texts[0]
    assert text.startswith("Today is ")
    assert f"\n\n{print_turn.RUN_NOTE}" in text
    assert text.index(print_turn.RUN_NOTE) < text.index("write the files")


def test_the_transcript_file_holds_every_request_call_and_result_in_order(
        provider, tmp_path, capsys):
    provider(2)
    transcript = tmp_path / "logs" / "transcript.jsonl"

    chat_module.run_cli_chat(oneshot="write the files", tui=False, model=MODEL,
                             transcript_file=str(transcript))

    assert capsys.readouterr().out == "wrote 2 files\n"
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    turn = [r for r in records if r["type"] != "request" or r["call_kind"] == "chat"]
    assert [r["type"] for r in turn] == [
        "run", "user",
        "request", "tool_call", "tool_result",
        "request", "tool_call", "tool_result",
        "request", "assistant_text", "reply",
    ]
    run, user = turn[0], turn[1]
    assert run["model"] == MODEL and run["ended"] == "completed"
    assert run["run_note"] == print_turn.RUN_NOTE
    assert user["text"] == "write the files"
    calls = [r for r in turn if r["type"] == "tool_call"]
    assert [c["arguments"] for c in calls] == [
        {"file_path": "round_1.txt", "content": "1"},
        {"file_path": "round_2.txt", "content": "2"},
    ]
    results = [r for r in turn if r["type"] == "tool_result"]
    assert [r["tool_call_id"] for r in results] == ["call1", "call2"]
    assert all(r["content"].startswith("Wrote 1 bytes to ") and not r["is_error"]
               for r in results)
    requests = [r for r in turn if r["type"] == "request"]
    assert [r["n"] for r in requests] == sorted(r["n"] for r in requests)
    assert all(r["input_tokens"] == 100 and r["cached_input_tokens"] == 50
               and r["output_tokens"] == 10 for r in requests)
    # Every record that has a time carries one, in order.
    times = [r["time"] for r in turn if r.get("time")]
    assert len(times) == 1 + 3 + 2 + 1 and times == sorted(times)
    assert turn[-1]["text"] == "wrote 2 files"


def test_the_transcript_keeps_a_long_tool_result_whole(provider, tmp_path, capsys):
    # Read from what the run stored, as for a turn the Worker ran.
    big = tmp_path / "task" / "big.txt"
    big.write_text("".join(f"line {i} " + "x" * 60 + "\n" for i in range(400)),
                   encoding="utf-8")
    fake = provider(1, read=str(big))
    transcript = tmp_path / "transcript.jsonl"

    chat_module.run_cli_chat(oneshot="read it", tui=False, model=MODEL,
                             transcript_file=str(transcript))

    capsys.readouterr()
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    [result] = [r for r in records if r["type"] == "tool_result"]
    sent = fake.tool_results["call1"]
    assert len(sent) > 20000
    assert result["content"] == sent
    assert result["complete"] is True


class _Store:
    def __init__(self, messages):
        self.messages = messages

    def get_messages(self, session_id):
        return self.messages


def _tool_row(call_id: str, ts: float, content: str) -> dict:
    return {"role": "tool", "timestamp": ts, "content": content,
            "extra": {"tool_use": {"tool_call_id": call_id, "arguments": {}}}}


def _request_row(ts: float) -> dict:
    return {"ts": ts, "call_kind": "chat", "provider": PROVIDER, "model_id": MODEL_ID,
            "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "output_tokens": 1}


def _scope(monkeypatch, requests, executions=("exec-run",), anchors=("u", "a")):
    """The run is ``executions``, which wrote the messages ``anchors`` and
    made ``requests``."""
    monkeypatch.setattr(print_events, "_run_scope", lambda run: {
        "executions": frozenset(executions), "anchors": dict.fromkeys(anchors, executions[0]),
        "parents": {}, "unread": False})
    monkeypatch.setattr(print_events, "_request_rows", lambda run, ids: list(requests))


def test_a_call_with_no_stored_result_keeps_later_requests_after_it(monkeypatch):
    # Request 1 asked for two calls at once; the run stopped before the
    # first stored its result, the second stored one at t=120, and request
    # 2 then wrote the answer. The first call has no time of its own.
    _scope(monkeypatch, [_request_row(110.0), _request_row(130.0)])
    # Stored as the model read it: whole, whatever its length or its end.
    whole = "y" * 4000 + "... (+96000 more)"
    store = _Store([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        _tool_row("b", 120.0, whole),
        {"role": "assistant", "id": "a", "timestamp": 101.0, "completed_at": 140.0, "content": "done",
         "extra": {"blocks": [
             {"type": "tool", "tool_call_id": "a", "tool": "bash", "input": {}},
             {"type": "tool", "tool_call_id": "b", "tool": "read", "input": {}},
             {"type": "text", "text": "done"},
         ]}},
    ])
    run = print_turn.PrintRun(session_id="s", started_at=100.0)

    records = print_turn.transcript_records(run, store=store)

    assert [(r["type"], r.get("n") or r.get("tool_call_id")) for r in records[1:]] == [
        ("user", None),
        ("request", 1), ("tool_call", "a"), ("tool_result", "a"), ("gap", "a"),
        ("tool_call", "b"), ("tool_result", "b"),
        ("request", 2), ("assistant_text", None), ("reply", None),
    ]
    stored = {r["tool_call_id"]: r for r in records if r["type"] == "tool_result"}
    assert stored["b"]["content"] == whole and stored["b"]["complete"] is True
    # The call the run stopped during: no result, and said so.
    assert stored["a"]["content"] is None and stored["a"]["complete"] is False
    assert [r for r in records if r["type"] == "gap"] == [
        {"type": "gap", "what": "tool_result", "tool_call_id": "a"}]


def test_reasoning_kept_only_for_the_model_writes_no_record(monkeypatch):
    """A reasoning item with no summary is stored for the next request; the
    transcript writes reasoning only when there is text to read."""
    _scope(monkeypatch, [_request_row(110.0)])
    store = _Store([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        {"role": "assistant", "id": "a", "timestamp": 101.0, "completed_at": 140.0, "content": "done",
         "extra": {"blocks": [
             {"type": "thinking", "text": "", "signature": "{\"type\": \"reasoning\"}", "round_start": True},
             {"type": "thinking", "text": "Checked.", "signature": "{\"type\": \"reasoning\"}"},
             {"type": "text", "text": "done"},
         ]}},
    ])
    records = print_turn.transcript_records(print_turn.PrintRun(session_id="s", started_at=100.0),
                                            store=store)
    assert [(r["type"], r.get("text")) for r in records[1:] if r["type"] in ("reasoning", "assistant_text")] == [
        ("reasoning", "Checked."), ("assistant_text", "done")]
    assert "signature" not in json.dumps(records)


def test_a_result_the_worker_journaled_is_written_whole(monkeypatch):
    """A turn the Worker ran: this process has none of its tool events; the
    session log's journal has the result as the model read it, and it wins
    over a row an older Worker stored as a preview."""
    from openprogram.runtime.execution.agent_journal import TOOL_RESULT

    _scope(monkeypatch, [_request_row(110.0)], executions=("exec-worker",))
    whole = "z" * 9000
    preview = whole[:4000] + "... (+5000 more)"

    class _LoggedStore(_Store):
        def read_turn_records(self, session_id, execution_id, record_type=None):
            assert execution_id is None and record_type == TOOL_RESULT
            text = {"type": "text", "text": whole}
            return [
                {"ts": int(115e9), "type": TOOL_RESULT, "data": {
                    "execution_id": "exec-worker", "tool_call_id": "b", "is_error": False,
                    "result": {"role": "toolResult", "content": [text]}}},
                # Another run of the conversation, even at the same time.
                {"ts": int(115e9), "type": TOOL_RESULT, "data": {
                    "execution_id": "exec-old", "tool_call_id": "old", "is_error": False,
                    "result": {"content": [text]}}},
            ]

    store = _LoggedStore([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        _tool_row("b", 120.0, preview),
        {"role": "assistant", "id": "a", "timestamp": 101.0, "completed_at": 140.0, "content": "done",
         "extra": {"blocks": [{"type": "tool", "tool_call_id": "b", "tool": "read", "input": {}},
                              {"type": "text", "text": "done"}]}},
    ])
    records = print_turn.transcript_records(print_turn.PrintRun(session_id="s", started_at=100.0),
                                            store=store)
    [result] = [r for r in records if r["type"] == "tool_result"]
    assert result["content"] == whole and result["complete"] is True


def test_the_transcript_holds_the_run_s_messages_not_another_branch_s(monkeypatch):
    """Another branch of the conversation answered while the run ran: its
    messages share the session and the time window, not the run."""
    _scope(monkeypatch, [_request_row(110.0)])
    store = _Store([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        {"role": "user", "id": "other", "timestamp": 102.0, "content": "elsewhere"},
        {"role": "assistant", "id": "other_reply", "timestamp": 103.0, "completed_at": 104.0,
         "content": "not this run", "extra": {"blocks": [{"type": "text", "text": "not this run"}]}},
        {"role": "assistant", "id": "a", "timestamp": 101.0, "completed_at": 140.0, "content": "done",
         "extra": {"blocks": [{"type": "text", "text": "done"}]}},
    ])
    records = print_turn.transcript_records(print_turn.PrintRun(session_id="s", started_at=100.0),
                                            store=store)
    assert [(r["type"], r.get("text")) for r in records[1:]] == [
        ("user", "go"), ("request", None), ("assistant_text", "done"), ("reply", "done")]


def test_print_failure_exits_nonzero_with_the_error_on_stderr(provider, tmp_path, capsys):
    provider(5, fail_at=2)
    usage = tmp_path / "usage.json"

    with pytest.raises(SystemExit) as info:
        chat_module.run_cli_chat(oneshot="go", tui=False, model=MODEL, usage_file=str(usage))

    assert info.value.code == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "[error] upstream 500"
    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert summary["ended"] == "failed"
    assert summary["error"] == "upstream 500"
    assert summary["model_requests"] == 2
    assert summary["tool_calls"] is None
    # Why it failed travels as the runtime classified it, not only as text.
    assert summary["phase"] == "in_turn"
    assert (summary["error_reason"], summary["error_retryable"]) == ("unknown", False)


def test_print_without_a_model_and_no_terminal_fails_fast(monkeypatch, tmp_path, capsys):
    from openprogram.runtime.agent_registry import builtin_agents

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(builtin_agents, "ensure_default_agent",
                        lambda: types.SimpleNamespace(id="main"))
    monkeypatch.setattr(print_turn, "chosen_model", lambda agent_id, model: None)

    def no_wizard(console):
        raise AssertionError("the setup must not be offered without a terminal")

    monkeypatch.setattr(chat_module, "_prompt_first_run_setup", no_wizard)
    usage = tmp_path / "usage.json"

    with pytest.raises(SystemExit) as info:
        chat_module.run_cli_chat(oneshot="hi", tui=False, usage_file=str(usage))

    assert info.value.code == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "No model is configured" in out.err
    assert "--model" in out.err
    assert json.loads(usage.read_text(encoding="utf-8"))["ended"] == "not_started"


@pytest.mark.parametrize("listing_error, message", [
    (None, "prov does not list the model 'nope'"),
    ("HTTP 429 for https://prov.invalid",
     "could not read prov's model list (HTTP 429 for https://prov.invalid)"),
])
def test_a_model_that_cannot_be_enabled_fails_before_the_run(monkeypatch, tmp_path, capsys,
                                                              listing_error, message):
    monkeypatch.setattr(worker_turn, "resolve_worker",
                        lambda: pytest.fail("no run may start"))

    def unavailable(model):
        raise print_turn.ModelUnavailableError(model, listing_error)

    monkeypatch.setattr(print_turn, "use_model", unavailable)
    usage = tmp_path / "usage.json"

    with pytest.raises(SystemExit) as info:
        chat_module.run_cli_chat(oneshot="hi", tui=False, model="prov/nope",
                                 usage_file=str(usage))

    assert info.value.code == 1
    assert f"[error] {message}" in capsys.readouterr().err
    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert summary["ended"] == "not_started"
    assert message in summary["error"]
    assert (summary["phase"], summary["error_kind"]) == ("not_started", "model_unavailable")


def _listing(monkeypatch, rows, error=None):
    from openprogram.models.listing import listing, toggle

    monkeypatch.setattr(listing, "_browse_models_with_error",
                        lambda provider, force_refresh=False: ([dict(r) for r in rows], error))
    by_id = {row["id"]: row for row in rows}
    spec = (lambda provider, model_id: dict(by_id[model_id]) if model_id in by_id else None)
    monkeypatch.setattr(toggle, "spec_row_for", spec)
    monkeypatch.setattr(listing, "spec_row_for", spec)


def test_naming_a_model_uses_it_for_the_run_without_enabling_it(monkeypatch):
    from openprogram.models.providers.settings.storage import _read_providers_cfg

    row = {"id": "listed-1", "name": "Listed", "api": "openai-completions",
           "base_url": "http://listed.invalid/v1", "context_window": 8000,
           "max_tokens": 1000, "input": ["text"]}
    _listing(monkeypatch, [row])
    monkeypatch.delitem(ENABLED_MODELS, "openrouter/listed-1", raising=False)

    try:
        assert print_turn.use_model("openrouter/listed-1") is None
        # Usable in this process; the App's enabled models are as they were.
        assert ENABLED_MODELS["openrouter/listed-1"].id == "listed-1"
        rows = (_read_providers_cfg().get("openrouter") or {}).get("models") or []
        assert "listed-1" not in [r.get("id") for r in rows]
        with pytest.raises(print_turn.ModelUnavailableError) as info:
            print_turn.use_model("openrouter/not-listed")
        assert info.value.listing_error is None
        assert str(info.value) == "openrouter does not list the model 'not-listed'"
    finally:
        ENABLED_MODELS.pop("openrouter/listed-1", None)


def test_a_model_list_that_could_not_be_read_is_named_as_the_cause(monkeypatch):
    _listing(monkeypatch, [], error="HTTP 401 for https://api.deepseek.invalid")
    monkeypatch.delitem(ENABLED_MODELS, "deepseek/deepseek-chat", raising=False)

    with pytest.raises(print_turn.ModelUnavailableError) as info:
        print_turn.use_model("deepseek/deepseek-chat")

    assert info.value.listing_error == "HTTP 401 for https://api.deepseek.invalid"
    assert "does not list" not in str(info.value)
    assert "HTTP 401" in str(info.value)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="needs a directory this user cannot write")
def test_a_folder_that_cannot_take_the_project_marker_runs_unbound(provider, tmp_path, capsys):
    provider(1)
    locked = tmp_path / "read-only"
    locked.mkdir()
    (locked / "README").write_text("x")
    locked.chmod(0o555)
    try:
        assert print_turn.working_directory_project(locked) is None
        assert "could not be opened as the conversation's project" in capsys.readouterr().err
        assert not (locked / ".openprogram").exists()

        os.chdir(locked)
        chat_module.run_cli_chat(oneshot="go", tui=False, model=MODEL)
        assert capsys.readouterr().out == "wrote 1 files\n"
    finally:
        locked.chmod(0o755)


@pytest.fixture(autouse=True)
def _signal_handlers_restored(monkeypatch):
    """A stopped run keeps its signal handlers until the process ends; the
    test process puts its own back."""
    saved = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr(print_stop, "_held", False)
    monkeypatch.setattr(print_stop, "_stop_signal", None)
    print_stop._noted.clear()
    yield
    import atexit
    atexit.unregister(print_stop._ignore_signals)
    for number, handler in saved.items():
        signal.signal(number, handler)
    print_stop._noted.clear()


class _Left(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _raise_left(code):
    raise _Left(code)


class _Adapter:
    def fail_admission(self, *_args, **_kwargs):
        pass


def _stop_with_a_signal_during_the_cancel(monkeypatch, *, stops_after):
    """Run ``_stop_run`` where a second SIGTERM lands while the cancel is
    delivered; the turn finishes ``stops_after`` seconds after the cancel
    (never when ``None``). Returns the run and how long the stop took."""
    from openprogram.runtime.execution import run_control

    finished = threading.Event()

    def cancel(_execution_id):
        os.kill(os.getpid(), signal.SIGTERM)
        if stops_after is not None:
            threading.Timer(stops_after, finished.set).start()

    monkeypatch.setattr(run_control, "cancel_execution", cancel)
    run = print_turn.PrintRun(session_id="s")
    began = time.monotonic()
    print_turn._stop_run(_Adapter(), types.SimpleNamespace(execution_id="e"),
                         finished, {}, run)
    return run, time.monotonic() - began


def test_a_second_signal_during_the_cancel_still_waits_for_a_turn_that_stops(monkeypatch):
    run, took = _stop_with_a_signal_during_the_cancel(monkeypatch, stops_after=0.4)

    assert print_stop._noted.is_set()
    assert not run.left_running
    assert run.error is None
    assert 0.35 <= took < 1.5


def test_a_second_signal_leaves_a_turn_that_does_not_stop_after_the_short_wait(monkeypatch):
    monkeypatch.setattr(print_stop, "SECOND_SIGNAL_GRACE_S", 0.3)
    run, took = _stop_with_a_signal_during_the_cancel(monkeypatch, stops_after=None)

    assert run.left_running
    assert "had not stopped" in run.error
    assert 0.25 <= took < 1.5


def test_a_signal_after_a_stopped_run_is_written_still_exits_130(monkeypatch):
    monkeypatch.setattr(print_stop, "leave", _raise_left)
    run = print_turn.PrintRun(session_id="s", ended=print_turn.ENDED_CANCELLED)
    print_turn.hold_signals()

    # No signal while it stopped: the process goes on to its normal exit...
    print_turn.leave_stopped(run, 130)
    # ...and a signal during that exit leaves with the same code.
    with pytest.raises(_Left) as left:
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.5)
    assert left.value.code == 130


def test_only_the_first_signal_is_raised_and_the_rest_are_held(monkeypatch):
    with pytest.raises(KeyboardInterrupt):
        with print_turn.interrupt_on_terminate():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.5)
    assert print_turn.stopping()
    assert not print_stop._noted.is_set()

    # Past the block too, while the command stops: noted, not raised.
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(0.1)
    assert print_stop._noted.is_set()


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs a spare signal to stand in")
def test_the_windows_stop_signal_stops_the_run_like_sigterm(monkeypatch):
    """On Windows a supervisor stops a child with CTRL_BREAK_EVENT, which
    Python delivers as SIGBREAK; SIGTERM never arrives there. Not run on
    Windows: a spare POSIX signal stands in for the number."""
    stand_in = signal.SIGUSR1
    saved = signal.getsignal(stand_in)
    monkeypatch.setattr(signal, "SIGBREAK", stand_in, raising=False)
    try:
        with pytest.raises(KeyboardInterrupt):
            with print_turn.interrupt_on_terminate():
                os.kill(os.getpid(), stand_in)
                time.sleep(0.5)
        assert print_turn.stopping()
        # A second one while the command stops is noted, not raised.
        os.kill(os.getpid(), stand_in)
        time.sleep(0.1)
        assert print_stop._noted.is_set()
        print_stop._ignore_signals()
        assert signal.getsignal(stand_in) is signal.SIG_IGN
    finally:
        signal.signal(stand_in, saved)


def test_the_end_of_the_exit_ignores_signals(monkeypatch):
    import atexit

    registered = []
    monkeypatch.setattr(atexit, "register", registered.append)
    print_turn.leave_stopped(print_turn.PrintRun(session_id="s"), 130)
    assert registered == [print_stop._ignore_signals]

    print_stop._ignore_signals()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN


def test_a_signal_noted_while_the_run_stopped_leaves_once_its_files_are_written(monkeypatch):
    monkeypatch.setattr(print_stop, "leave", _raise_left)
    run = print_turn.PrintRun(session_id="s", ended=print_turn.ENDED_CANCELLED)
    print_turn.hold_signals()
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(0.1)

    with pytest.raises(_Left) as left:
        print_turn.leave_stopped(run, 130)
    assert left.value.code == 130


def _interrupt_main():
    import _thread
    _thread.interrupt_main()


def _terminate():
    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.parametrize("stop", [_interrupt_main, _terminate], ids=["ctrl-c", "sigterm"])
def test_an_interrupted_run_is_cancelled_before_the_command_leaves(provider, tmp_path, capsys,
                                                                   stop):
    fake = provider(60, delay=0.2)
    usage = tmp_path / "usage.json"

    def stop_after_two_rounds():
        deadline = time.monotonic() + 30
        while fake.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        stop()

    threading.Thread(target=stop_after_two_rounds, daemon=True).start()
    with pytest.raises(SystemExit) as info:
        chat_module.run_cli_chat(oneshot="write the files", tui=False, model=MODEL,
                                 usage_file=str(usage))

    # 128 + the signal's number: 130 for Ctrl-C, 143 for SIGTERM.
    assert info.value.code == (130 if stop is _interrupt_main else 143)
    assert "[cancelled]" in capsys.readouterr().err
    calls = fake.calls
    assert calls < 60
    time.sleep(1.0)
    # The turn stopped: no model request after the command left, and none
    # beside it (a title, a branch name) when the stopped turn ended.
    assert fake.calls == calls
    assert fake.side_calls == 0
    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert summary["ended"] == "cancelled"
    assert summary["error"] is None
    # Which signal stopped it, whatever code the command exits with.
    assert summary["stopped_by"] == {_interrupt_main: "SIGINT", _terminate: "SIGTERM"}[stop]
    # Every finished request is counted; the one the cancel cut short
    # reported no usage.
    assert calls - 1 <= summary["model_requests"] <= calls


def _this_checkout() -> dict:
    """The child imports this checkout's ``openprogram``, not an installed
    copy: it starts in a scratch folder, where the checkout is not on the
    path."""
    import openprogram

    root = os.path.dirname(os.path.dirname(os.path.abspath(openprogram.__file__)))
    paths = [root, *filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep))]
    return {"PYTHONPATH": os.pathsep.join(paths)}


# A turn that does not take the cancel: its model request blocks its own
# event loop, so nothing reaches it until the request returns. Run as a real
# command in a child process, since the command then ends its process.
_STUCK_RUN = r'''
import asyncio, os, sys, time
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, ModelCost, TextContent)

MARKS = os.environ["STUCK_MARKS"]

class Stuck:
    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        message = AssistantMessage(content=[TextContent(text="done")], api=model.api,
                                   provider=model.provider, model=model.id,
                                   stop_reason="stop", timestamp=0)
        yield EventStart(partial=message)
        if not context.tools:
            open(os.path.join(MARKS, "side"), "w").close()
        else:
            open(os.path.join(MARKS, "stuck"), "w").close()
            time.sleep(30)
        yield EventDone(reason="stop", message=message)

api_registry._registry["print-stuck-api"] = Stuck()
ENABLED_MODELS["stuck/model"] = Model(
    id="model", name="stuck", api="print-stuck-api", provider="stuck",
    base_url="http://print-stuck.invalid",
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.0, cache_write=0.0, source="configured"))
from openprogram.interfaces.cli import print_turn, worker_turn
worker_turn.resolve_worker = lambda: None
from openprogram.interfaces.cli import print_stop
print_stop.CANCEL_GRACE_S = float(os.environ["STUCK_GRACE"])
from openprogram.interfaces.cli import main
sys.argv = ["openprogram", "--print=wait", "--model", "stuck/model",
            "--usage-file", os.path.join(MARKS, "usage.json"),
            "--transcript-file", os.path.join(MARKS, "transcript.jsonl")]
main()
'''


@pytest.mark.parametrize("second_signal", [True, False], ids=["second-sigterm", "grace-ends"])
def test_a_turn_that_does_not_stop_still_ends_the_command_cleanly(tmp_path, second_signal):
    import subprocess

    marks = tmp_path / "marks"
    work = tmp_path / "work"
    home = tmp_path / "home"
    for folder in (marks, work, home):
        folder.mkdir()
    script = tmp_path / "stuck_run.py"
    script.write_text(_STUCK_RUN, encoding="utf-8")
    env = {**os.environ, **_this_checkout(), "HOME": str(home), "OPENPROGRAM_NO_AUTO_UPDATE": "1",
           "STUCK_MARKS": str(marks), "STUCK_GRACE": "1" if not second_signal else "30"}
    child = subprocess.Popen([sys.executable, str(script)], cwd=work, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while not (marks / "stuck").exists():
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < deadline, "the turn never reached its model request"
            time.sleep(0.05)
        stopped = time.monotonic()
        child.send_signal(signal.SIGTERM)
        if second_signal:
            time.sleep(0.5)
            child.send_signal(signal.SIGTERM)
        _out, err = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    took = time.monotonic() - stopped

    assert child.returncode == 143, err  # stopped by SIGTERM
    # The command leaves when it stops waiting, not when the stuck request
    # returns 30 s later: after the second signal, or the grace period.
    assert took < (3 if second_signal else 6)
    assert "[cancelled]" in err
    assert "Fatal Python error" not in err
    assert "cannot schedule new futures" not in err
    assert not list(work.glob("core*"))
    summary = json.loads((marks / "usage.json").read_text(encoding="utf-8"))
    assert summary["ended"] == "cancelled"
    assert "had not stopped" in summary["error"]
    assert (marks / "transcript.jsonl").exists()
    assert not (marks / "side").exists()


# A turn whose tool runs a long command (its shell's pid written first), as a
# real command in a child process: the command runs under a supervisor of its
# own, so it outlives the run unless it is stopped.
_COMMAND_RUN = r'''
import os, sys
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, ModelCost, ToolCall)

MARKS = os.environ["COMMAND_MARKS"]

class Commands:
    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        message = AssistantMessage(
            content=[ToolCall(id="c1", name="bash", arguments={
                "command": f"echo $$ > {MARKS}/pid; exec sleep 30", "description": "wait"})],
            api=model.api, provider=model.provider, model=model.id,
            stop_reason="toolUse", timestamp=0)
        yield EventStart(partial=message)
        yield EventDone(reason="toolUse", message=message)

api_registry._registry["print-command-api"] = Commands()
ENABLED_MODELS["command/model"] = Model(
    id="model", name="command", api="print-command-api", provider="command",
    base_url="http://print-command.invalid",
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.0, cache_write=0.0, source="configured"))
from openprogram.interfaces.cli import print_turn, worker_turn
worker_turn.resolve_worker = lambda: None
from openprogram.interfaces.cli import print_stop
print_stop.CANCEL_GRACE_S = float(os.environ["COMMAND_GRACE"])
if os.environ.get("COMMAND_IGNORES_STOP"):
    # A turn the cancel never reaches, as a turn stuck elsewhere is.
    from openprogram.runtime.execution import run_control
    run_control.cancel_execution = lambda *_args, **_kwargs: None
from openprogram.interfaces.cli import main
sys.argv = ["openprogram", "--print=wait", "--model", "command/model",
            "--usage-file", os.path.join(MARKS, "usage.json")]
main()
'''


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("left_running", [False, True],
                         ids=["two-signals-at-once", "turn-left-running"])
def test_the_command_a_stopped_turn_ran_does_not_outlive_it(tmp_path, left_running):
    import subprocess

    marks = tmp_path / "marks"
    work = tmp_path / "work"
    home = tmp_path / "home"
    for folder in (marks, work, home):
        folder.mkdir()
    script = tmp_path / "command_run.py"
    script.write_text(_COMMAND_RUN, encoding="utf-8")
    env = {**os.environ, **_this_checkout(), "HOME": str(home), "OPENPROGRAM_NO_AUTO_UPDATE": "1",
           "COMMAND_MARKS": str(marks), "COMMAND_GRACE": "0.5" if left_running else "30"}
    if left_running:
        # The turn never takes the cancel, so the grace period runs out with
        # its command still running.
        env["COMMAND_IGNORES_STOP"] = "1"
    child = subprocess.Popen([sys.executable, str(script)], cwd=work, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pid = None
    try:
        deadline = time.monotonic() + 60
        while not (marks / "pid").exists() or not (marks / "pid").read_text().strip():
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < deadline, "the tool's command never started"
            time.sleep(0.05)
        pid = int((marks / "pid").read_text())
        child.send_signal(signal.SIGTERM)
        if not left_running:
            # A second signal right behind the first, noted while the cancel
            # is delivered.
            time.sleep(0.02)
            child.send_signal(signal.SIGTERM)
        _out, err = child.communicate(timeout=20)
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), "the tool's command outlived the run"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)

    assert child.returncode == 143, err  # stopped by SIGTERM
    summary = json.loads((marks / "usage.json").read_text(encoding="utf-8"))
    assert summary["ended"] == "cancelled"
    if left_running:
        assert "had not stopped" in summary["error"]
    else:
        # The turn stopped within the short wait a second signal leaves it.
        assert summary["error"] is None


def test_the_in_process_turn_has_no_one_to_ask():
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.policy import can_approve, nobody_answers

    request = TurnRequest(session_id="s", user_text="x", agent_id="main", source="cli",
                          **print_turn.non_interactive_owner())
    assert request.permission_mode == "bypass"
    assert request.authority_tier == "owner"
    assert not can_approve(request)
    assert nobody_answers(request)


def test_usage_summary_counts_the_run_and_its_sub_agents(monkeypatch):
    """The run's usage is its execution tree's: its own requests and its
    sub-agents', not another branch of the conversation running at the same
    time, nor the title the Worker writes beside it."""
    import time

    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.ledger import default_ledger

    trees: list = []

    def tree(execution_id, *, store=None):
        trees.append(execution_id)
        return frozenset({"exec_run", "exec_child"})

    monkeypatch.setattr("openprogram.runtime.execution.execution_tree.execution_tree", tree)
    run = print_turn.PrintRun(session_id="local_usage1", started_at=time.time() - 1)
    run.ended = print_turn.ENDED_COMPLETED
    run.execution_id = "exec_run"
    run.tool_calls = 1
    now = time.time()
    default_ledger.append_many([
        UsageEvent(ts=now, session_id="local_usage1", execution_id="exec_run", call_kind="chat",
                   provider="p", model_id="a", input_tokens=10, output_tokens=2,
                   cache_read_tokens=5, cost_total=0.5, cost_source="configured"),
        UsageEvent(ts=now, session_id="local_usage1", execution_id="exec_child",
                   call_kind="subagent", provider="p", model_id="b", input_tokens=1,
                   output_tokens=1, cost_source="unknown"),
        # Another branch of the same conversation, at the same time.
        UsageEvent(ts=now, session_id="local_usage1", execution_id="exec_other",
                   call_kind="chat", provider="p", model_id="a", input_tokens=999,
                   output_tokens=999, cost_source="configured"),
        # The title written beside the turn, on a thread of its own.
        UsageEvent(ts=now, session_id="local_usage1", call_kind="title", provider="p",
                   model_id="a", input_tokens=77, output_tokens=7, cost_source="configured"),
    ])

    summary = print_turn.usage_summary(run)

    assert trees == ["exec_run"]
    assert summary["input_tokens"] == 11
    assert summary["cached_input_tokens"] == 5
    assert summary["output_tokens"] == 3
    assert summary["model_requests"] == 2
    assert summary["agent_rounds"] == 1
    assert summary["unpriced_requests"] == 1
    assert summary["cost_usd"] is None
    assert summary["model"] == "p/a"
    by_model = {entry["model"]: entry for entry in summary["by_model"]}
    assert by_model["p/a"]["cost_usd"] == 0.5
    assert by_model["p/b"]["cost_usd"] is None


def test_a_run_that_never_started_counts_no_requests():
    import time

    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.ledger import default_ledger

    run = print_turn.PrintRun(session_id="local_usage2", started_at=time.time() - 1)
    default_ledger.append_many([UsageEvent(
        ts=time.time(), session_id="local_usage2", execution_id="exec_web", call_kind="chat",
        provider="p", model_id="a", input_tokens=10, output_tokens=2, cost_source="configured")])

    summary = print_turn.usage_summary(run)

    assert (summary["model_requests"], summary["input_tokens"]) == (0, 0)


def test_the_run_counts_its_own_requests_beside_a_turn_on_another_branch(
        provider, tmp_path, capsys, monkeypatch):
    """A turn on another branch of the conversation, running while this one
    does, records its requests in the same session and time window; the
    run's usage and transcript leave them out."""
    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.ledger import default_ledger

    fake = provider(1)
    original = FakeProvider.stream_simple

    async def beside_another_branch(self, model, context, options=None):
        from openprogram.models.usage.context import call_context

        assert call_context().session_id
        default_ledger.append_many([UsageEvent(
            session_id=call_context().session_id, execution_id="exec_other_branch",
            call_kind="chat", provider=PROVIDER, model_id=MODEL_ID, input_tokens=5000,
            output_tokens=500, cost_source="configured")])
        async for event in original(self, model, context, options):
            yield event

    monkeypatch.setattr(FakeProvider, "stream_simple", beside_another_branch)
    usage = tmp_path / "usage.json"
    transcript = tmp_path / "transcript.jsonl"

    chat_module.run_cli_chat(oneshot="write it", tui=False, model=MODEL,
                             usage_file=str(usage), transcript_file=str(transcript))

    capsys.readouterr()
    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert fake.calls == 2
    assert (summary["model_requests"], summary["input_tokens"]) == (2, 200)
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert [r["input_tokens"] for r in records if r["type"] == "request"] == [100, 100]


def test_parse_model_takes_provider_and_id():
    assert print_turn.parse_model(" anthropic/claude-x ") == ("anthropic", "claude-x")
    assert print_turn.parse_model("openrouter/moonshot/kimi") == ("openrouter", "moonshot/kimi")
    for bad in ("claude-x", "/x", "p/", ""):
        with pytest.raises(ValueError):
            print_turn.parse_model(bad)


def test_cli_passes_model_usage_and_transcript_files_to_the_print_call(monkeypatch):
    from openprogram.interfaces.cli import application as cli

    seen = {}
    monkeypatch.setattr(cli, "_cmd_cli_chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "hi", "--model", "p/m",
                                      "--usage-file", "u.json", "--transcript-file", "t.jsonl"])
    cli.main()
    assert seen["oneshot"] == "hi"
    assert seen["model"] == "p/m"
    assert seen["usage_file"] == "u.json"
    assert seen["transcript_file"] == "t.jsonl"


@pytest.mark.parametrize("argv, message", [
    (["--model", "p/m"], "--model requires --print"),
    (["--usage-file", "u.json"], "--usage-file requires --print"),
    (["--transcript-file", "t.jsonl"], "--transcript-file requires --print"),
    (["--print", "hi", "--model", "nomodel"], "--model takes PROVIDER/MODEL_ID"),
])
def test_cli_rejects_print_options_used_wrongly(monkeypatch, capsys, argv, message):
    from openprogram.interfaces.cli import application as cli

    monkeypatch.setattr(cli, "_cmd_cli_chat", lambda **kwargs: pytest.fail("must not run"))
    monkeypatch.setattr(sys, "argv", ["openprogram", *argv])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert message in capsys.readouterr().err


def test_the_run_note_lives_in_the_runtime_with_the_same_bytes():
    """A run no one watches carries the run note, in process or through the
    Worker: the Worker's chat handler reads it from the runtime, not from this
    CLI module, and its bytes are those the benchmark measured."""
    import ast
    import hashlib
    from pathlib import Path

    from openprogram.interfaces.server.ws_actions.chat import turn as ws_turn
    from openprogram.runtime.conversation import run_note

    assert print_turn.RUN_NOTE is run_note.RUN_NOTE
    assert hashlib.sha256(run_note.RUN_NOTE.encode()).hexdigest() == (
        "d8d683cab4f433ffd825b2e3fdb15e5f29b4ba5f25cd2cafa0beba57ef1a2e0b")
    tree = ast.parse(Path(ws_turn.__file__).read_text(encoding="utf-8"))
    assert not [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("openprogram.interfaces.cli")]


# ---------------------------------------------------------------------------
# --effort and what the requests carried (run header)
# ---------------------------------------------------------------------------

class WireFakeProvider(FakeProvider):
    """The fake provider, serializing each request as the OpenAI Responses
    adapter does and handing the body to the capture point first: the
    reasoning field and ``include`` only with an effort, ``store`` false."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.efforts: list = []

    async def stream_simple(self, model, context, options=None):
        from openprogram.models.providers.recording import capture

        effort = getattr(options, "reasoning", None)
        if context.tools:
            self.efforts.append(effort)
        body = {"model": model.id, "input": [], "store": False,
                "instructions": context.system_prompt or "",
                "tools": [{"type": "function", "name": tool.name} for tool in context.tools or []]}
        if effort:
            body["reasoning"] = {"effort": effort, "summary": "auto"}
            body["include"] = ["reasoning.encrypted_content"]
        capture.capture_request(model, body, wire=capture.RESPONSES)
        async for event in super().stream_simple(model, context, options):
            yield event


@pytest.fixture
def wire_provider(provider, monkeypatch):
    """The wire fake on a model that offers reasoning levels."""
    def install(rounds: int) -> WireFakeProvider:
        provider(rounds)
        fake = WireFakeProvider(rounds)
        monkeypatch.setitem(api_registry._registry, API, fake)
        model = ENABLED_MODELS[MODEL]
        monkeypatch.setitem(ENABLED_MODELS, MODEL, model.model_copy(update={
            "reasoning": True, "thinking_levels": ["low", "medium", "high"],
            "default_thinking_level": "medium"}))
        return fake

    return install


def _run_files(tmp_path, **options):
    usage, transcript = tmp_path / "usage.json", tmp_path / "transcript.jsonl"
    chat_module.run_cli_chat(oneshot="write the files", tui=False, model=MODEL,
                             usage_file=str(usage), transcript_file=str(transcript), **options)
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    return json.loads(usage.read_text(encoding="utf-8")), records


def test_effort_sets_the_turn_s_reasoning_and_the_files_say_what_was_sent(
        wire_provider, tmp_path, capsys):
    import hashlib

    fake = wire_provider(2)
    summary, records = _run_files(tmp_path, effort="high")

    assert capsys.readouterr().out == "wrote 2 files\n"
    assert fake.efforts == ["high", "high", "high"]
    assert summary["effort_requested"] == "high" and summary["effort"] == "high"
    assert summary["tools_count"] >= 1
    run = records[0]
    assert run["type"] == "run" and run["effort_requested"] == "high"
    assert run["sent_effort"] == "high" and run["sent_model"] == MODEL
    assert run["sent_include"] == ["reasoning.encrypted_content"]
    assert run["sent_store"] is False
    assert "write" in run["tools"] and len(run["tools"]) == summary["tools_count"]
    assert len(run["tools_sha256"]) == 64 and len(run["system_prompt_sha256"]) == 64
    assert run["run_note_sha256"] == hashlib.sha256(print_turn.RUN_NOTE.encode()).hexdigest()
    assert isinstance(run["permission_mode"], str) and run["requests_seen"] >= 3
    assert run["web_tools"] == [name for name in run["tools"] if "web" in name.lower()]
    turn = [r for r in records if r["type"] == "request" and r["call_kind"] == "chat"]
    assert len(turn) == 3
    assert all(r["effort"] == "high" and r["include"] == ["reasoning.encrypted_content"]
               and r["store"] is False and r["tools_count"] == summary["tools_count"]
               and r["tools_sha256"] == run["tools_sha256"] for r in turn)


def test_without_effort_the_agent_s_own_applies_and_is_recorded_as_sent(
        wire_provider, tmp_path, capsys):
    fake = wire_provider(1)
    summary, records = _run_files(tmp_path)

    assert summary["effort_requested"] is None and records[0]["effort_requested"] is None
    # Whatever the Agent's effort is, the files say what went out.
    assert summary["effort"] == records[0]["sent_effort"] == fake.efforts[0]


@pytest.mark.parametrize("effort", ["off", "none"])
def test_effort_off_sends_no_reasoning(wire_provider, tmp_path, capsys, effort):
    fake = wire_provider(1)
    summary, records = _run_files(tmp_path, effort=effort)

    assert fake.efforts == [None, None]
    assert summary["effort_requested"] == effort and summary["effort"] is None
    assert records[0]["sent_include"] is None


def test_a_run_whose_requests_were_not_seen_records_no_wire_facts():
    run = print_turn.PrintRun(session_id="s", effort="low")
    header = print_turn.run_header(run)
    assert header["effort_requested"] == "low"
    assert header["sent_effort"] is None and header["tools"] is None
    assert header["web_tools"] is None and header["requests_seen"] == 0


def test_a_retried_request_is_not_paired_with_the_ledger_row_it_did_not_answer():
    sent = [
        {"time": 1.0, "execution_id": "e", "call_kind": "chat", "n": "first, failed"},
        {"time": 2.0, "execution_id": "e", "call_kind": "chat", "n": "first, sent again"},
        {"time": 4.0, "execution_id": "e", "call_kind": "chat", "n": "second"},
        {"time": 4.5, "execution_id": "e", "call_kind": "title", "n": "title"},
    ]
    rows = [{"ts": 3.0, "execution_id": "e", "call_kind": "chat"},
            {"ts": 5.0, "execution_id": "e", "call_kind": "chat"},
            {"ts": 5.0, "execution_id": "e", "call_kind": "title"},
            {"ts": 6.0, "execution_id": "other", "call_kind": "chat"}]
    paired = print_turn._pair_sent(rows, sent)
    assert [fact and fact["n"] for fact in paired] == [
        "first, sent again", "second", "title", None]


@pytest.mark.parametrize("wire, body, effort", [
    ("openai-responses", {"reasoning": {"effort": "low", "summary": "auto"}}, "low"),
    ("openai-responses", {}, None),
    ("openai-completions", {"reasoning_effort": "high"}, "high"),
    ("anthropic-messages", {"output_config": {"effort": "max"}}, "max"),
    ("anthropic-messages", {"thinking": {"type": "enabled", "budget_tokens": 8000}}, "budget:8000"),
    ("anthropic-messages", {"thinking": {"type": "disabled"}}, None),
    ("google-generative-ai", {"config": {"thinkingConfig": {"thinkingLevel": "high"}}}, "high"),
    ("google-generative-ai", {"config": {"thinkingConfig": {"thinkingBudget": 0}}}, "budget:0"),
])
def test_the_effort_is_read_from_each_wire_s_own_field(wire, body, effort):
    assert print_turn.wire_effort(wire, body) == effort


def test_a_failing_observer_never_stops_the_request():
    from openprogram.models.providers.recording import capture

    seen = []

    def broken(_request):
        raise RuntimeError("observer bug")

    model = types.SimpleNamespace(provider="p", api="a", id="m")
    with capture.observing(broken), capture.observing(seen.append):
        capture.capture_request(model, {"model": "m", "input": []}, wire=capture.RESPONSES)
    capture.capture_request(model, {"model": "m", "input": []}, wire=capture.RESPONSES)
    assert [request.model_id for request in seen] == ["m"]


def test_cli_passes_the_effort_to_the_print_call(monkeypatch):
    from openprogram.interfaces.cli import application as cli

    seen = {}
    monkeypatch.setattr(cli, "_cmd_cli_chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "hi", "--effort", "xhigh"])
    cli.main()
    assert seen["effort"] == "xhigh"
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "hi"])
    cli.main()
    assert "effort" not in seen


@pytest.mark.parametrize("argv, message", [
    (["--effort", "high"], "--effort requires --print"),
    (["--print", "hi", "--effort", "extreme"], "invalid choice"),
])
def test_cli_rejects_an_effort_used_wrongly(monkeypatch, capsys, argv, message):
    from openprogram.interfaces.cli import application as cli

    monkeypatch.setattr(cli, "_cmd_cli_chat", lambda **kwargs: pytest.fail("must not run"))
    monkeypatch.setattr(sys, "argv", ["openprogram", *argv])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("effort", ["low", "off"])
def test_the_worker_takes_the_effort_for_the_turn_only(monkeypatch, tmp_path, effort):
    seen: dict = {}

    def fake_run(_endpoint, session_id, _text, **kwargs):
        seen.update(kwargs)
        return worker_turn.WorkerTurn(reply="ok", session_id=session_id, tool_calls=0)

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: object())
    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fake_run)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    chat_module.run_cli_chat(oneshot="ping", tui=False, effort=effort)

    # ``turn_effort`` in the chat command: this turn's, never the branch's.
    assert seen["effort"] == effort and "agent_id" not in seen


# ---------------------------------------------------------------------------
# --no-web, the build in the header, and the transcript rebuilt from state
# ---------------------------------------------------------------------------

def test_no_web_sends_no_web_tool_and_says_so(wire_provider, tmp_path, capsys):
    wire_provider(1)
    _, plain = _run_files(tmp_path / "plain")
    wire_provider(1)
    _, records = _run_files(tmp_path / "off", no_web=True)

    run = records[0]
    assert run["no_web"] is True and plain[0]["no_web"] is False
    assert run["web_tools"] == []
    assert not [name for name in run["tools"] if "web" in name.lower()]
    # Only the web tools are gone.
    assert set(run["tools"]) == {name for name in plain[0]["tools"]
                                 if "web" not in name.lower()}


def test_cli_passes_no_web_to_the_print_call_and_only_there(monkeypatch, capsys):
    from openprogram.interfaces.cli import application as cli

    seen = {}
    monkeypatch.setattr(cli, "_cmd_cli_chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "hi", "--no-web"])
    cli.main()
    assert seen["no_web"] is True
    monkeypatch.setattr(sys, "argv", ["openprogram", "--no-web"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "--no-web requires --print" in capsys.readouterr().err


def test_the_worker_turn_carries_no_web(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_run(_endpoint, session_id, _text, **kwargs):
        seen.update(kwargs)
        return worker_turn.WorkerTurn(reply="ok", session_id=session_id, tool_calls=0)

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: object())
    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fake_run)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    chat_module.run_cli_chat(oneshot="ping", tui=False, no_web=True)

    assert seen["no_web"] is True


def test_the_worker_turns_no_web_into_a_tools_override():
    from openprogram.interfaces.server import handshake
    from openprogram.runtime.agent.provider_search import NO_WEB_OVERRIDE, no_web_tools

    assert "no_web" in handshake.CHAT_CAPABILITIES
    assert no_web_tools(NO_WEB_OVERRIDE) and not no_web_tools(None)
    assert not no_web_tools({"inherit": True})
    assert print_turn.no_web_fields(True) == {"tools_override": NO_WEB_OVERRIDE}
    assert print_turn.no_web_fields(False) == {}


def test_the_header_names_the_build_the_run_came_from(monkeypatch):
    from openprogram.interfaces.cli import build_info

    run = print_turn.PrintRun(session_id="s")
    monkeypatch.setitem(sys.modules, build_info.MODULE, None)  # a source checkout
    header = print_turn.run_header(run)
    assert header["commit"] is None and header["built_at"] is None
    assert build_info.version_text("1.2.3") == "1.2.3"
    assert "tool_output_weight" in header

    module = types.ModuleType(build_info.MODULE)
    module.COMMIT, module.BUILT_AT = "0123456789abcdef0123", "2026-11-13T00:00:00Z"
    monkeypatch.setitem(sys.modules, build_info.MODULE, module)
    header = print_turn.run_header(run)
    assert (header["commit"], header["built_at"]) == (module.COMMIT, module.BUILT_AT)
    assert build_info.version_text("1.2.3") == "1.2.3 (0123456789ab)"
    namespace: dict = {}
    exec(build_info.module_source("abc", "now"), namespace)
    assert (namespace["COMMIT"], namespace["BUILT_AT"]) == ("abc", "now")


def test_the_usage_summary_counts_requests_by_what_they_were_for(provider, tmp_path, capsys):
    provider(1)
    summary, _ = _run_files(tmp_path)
    assert summary["by_call_kind"]["chat"]["requests"] == summary["model_requests"] == 2


def _comparable(records):
    """Records but what lived only in the process (the run record's header)."""
    return [record for record in records if record["type"] != "run"]


def test_a_transcript_is_rebuilt_from_the_state_the_run_left(provider, tmp_path, capsys):
    provider(2)
    _, written = _run_files(tmp_path)
    capsys.readouterr()
    run = written[0]

    rebuilt = print_turn.transcript_from_state(run["session_id"])

    assert _comparable(rebuilt) == _comparable(written)
    head = rebuilt[0]
    assert head["type"] == "run"
    for key in ("session_id", "model", "ended", "execution_id"):
        assert head[key] == run[key], key


class _Sessions:
    """The real database, listing only ``ids``."""

    def __init__(self, db, ids):
        self.db, self.ids = db, ids

    def list_sessions(self, **_):
        return [{"id": sid} for sid in self.ids]

    def __getattr__(self, name):
        return getattr(self.db, name)


def test_the_only_conversation_in_the_state_needs_no_name(provider, tmp_path, capsys,
                                                         monkeypatch):
    # As in a trial's fresh home, where the run is the one conversation.
    from openprogram.runtime.context import session_db

    provider(1)
    _, written = _run_files(tmp_path)
    sid = written[0]["session_id"]
    db = session_db.default_db()
    monkeypatch.setattr(session_db, "default_db", lambda: _Sessions(db, [sid]))
    assert _comparable(print_turn.transcript_from_state()) == _comparable(written)
    monkeypatch.setattr(session_db, "default_db", lambda: _Sessions(db, [sid, "other"]))
    with pytest.raises(print_turn.PrintTurnError, match="name one with --session"):
        print_turn.transcript_from_state()


def test_the_transcript_command_writes_the_rebuilt_file(provider, tmp_path, monkeypatch,
                                                        capsys):
    from openprogram.interfaces.cli import application as cli
    from openprogram.state.paths import get_state_dir

    provider(1)
    _, written = _run_files(tmp_path)
    out = tmp_path / "rebuilt.jsonl"
    monkeypatch.setattr(sys, "argv", ["openprogram", "transcript", "--state",
                                      str(get_state_dir()), "--session",
                                      written[0]["session_id"], "--output", str(out)])
    cli.main()
    rebuilt = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert _comparable(rebuilt) == _comparable(written)

    monkeypatch.setattr(sys, "argv", ["openprogram", "transcript", "--session", "nope",
                                      "--output", str(out)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 1 and "has no run" in capsys.readouterr().err


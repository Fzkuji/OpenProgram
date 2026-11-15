"""The Harbor adapter's container commands and usage parsing, without Harbor."""
from __future__ import annotations

import json
import shlex
import sys

import pytest

from scripts.benchmarks.harbor_agent import commands


def _words(command: str) -> list[str]:
    return shlex.split(command)


def test_the_command_module_loads_without_harbor():
    assert not any(name == "harbor" or name.startswith("harbor.") for name in sys.modules)


@pytest.mark.parametrize("kwargs", [
    {"wheel_path": None, "git_ref": None},
    {"wheel_path": "/w/openprogram-1.0-py3-none-any.whl", "git_ref": "abc123"},
    {"wheel_path": None, "git_ref": "  "},
    {"wheel_path": None, "git_ref": "main; rm -rf /"},
])
def test_the_package_must_be_pinned_to_one_source(kwargs):
    with pytest.raises(commands.AdapterConfigError):
        commands.package_spec(**kwargs)


@pytest.mark.parametrize("git_ref", [1.234e59, 1234567, True])
def test_a_git_ref_harbor_read_as_json_is_refused_not_rewritten(git_ref):
    # ``--ak git_ref=1234e56`` reaches the adapter as the float 1.234e+59.
    with pytest.raises(commands.AdapterConfigError, match="must be a string"):
        commands.package_spec(wheel_path=None, git_ref=git_ref)


def test_package_spec_names_a_wheel_or_a_git_ref():
    wheel = "/installed-agent/openprogram/wheels/openprogram-1.1.1-py3-none-any.whl"
    assert commands.package_spec(wheel_path=wheel, git_ref=None) == wheel
    assert commands.package_spec(wheel_path=None, git_ref="v1.1.1") == (
        "openprogram @ git+https://github.com/FuzzyKuji/OpenProgram@v1.1.1"
    )
    assert commands.package_spec(
        wheel_path=None, git_ref="0123abc", git_url="https://example.test/op",
    ) == "openprogram @ git+https://example.test/op@0123abc"


def test_an_uploaded_wheel_keeps_its_file_name():
    assert commands.wheel_remote_path("/tmp/dist/openprogram-1.1.1-py3-none-any.whl") == (
        "/installed-agent/openprogram/wheels/openprogram-1.1.1-py3-none-any.whl"
    )
    with pytest.raises(commands.AdapterConfigError):
        commands.wheel_remote_path("/tmp/dist/openprogram.tar.gz")


def test_install_uses_pinned_uv_and_a_managed_python_under_the_install_root():
    command = commands.install_command(
        "openprogram @ git+https://github.com/FuzzyKuji/OpenProgram@v1.1.1",
        uv_version="0.11.16", python_version="3.12",
    )

    assert "https://astral.sh/uv/0.11.16/install.sh" in command
    assert "UV_PYTHON_PREFERENCE=only-managed" in command
    assert ("tool install --python 3.12 "
            "--constraints /installed-agent/openprogram/wheels/constraints.txt "
            "'openprogram @ git+https://github.com/FuzzyKuji/OpenProgram@v1.1.1'") in command
    # The versions resolved are recorded for the trial's logs.
    assert ('pip freeze --python "$root/tools/openprogram/bin/python" '
            "> /installed-agent/openprogram/packages.txt") in command
    assert "root=/installed-agent/openprogram;" in command
    # Readable by a non-root agent user, and checked before setup counts as done.
    assert 'chmod -R a+rX "$root"' in command
    assert command.endswith('"$root/bin/openprogram" --version')
    assert command.startswith("set -eu;")


@pytest.mark.parametrize("model_name, provider, expected", [
    ("deepseek/deepseek-chat", None, "deepseek/deepseek-chat"),
    ("openrouter/moonshotai/kimi-k2", None, "openrouter/moonshotai/kimi-k2"),
    ("moonshot/kimi-for-coding", "kimi_coding", "kimi_coding/kimi-for-coding"),
])
def test_harbor_model_names_map_to_openprogram_models(model_name, provider, expected):
    assert commands.openprogram_model(model_name, provider) == expected


@pytest.mark.parametrize("model_name", [None, "", "deepseek-chat", "deepseek/", "/x"])
def test_a_model_without_a_provider_is_refused(model_name):
    with pytest.raises(commands.AdapterConfigError):
        commands.openprogram_model(model_name)


@pytest.mark.parametrize("provider, override, name", [
    ("deepseek", None, "DEEPSEEK_API_KEY"),
    ("openai", None, "OPENAI_API_KEY"),
    ("google", None, "GEMINI_API_KEY"),
    ("kimi_coding", None, "KIMI_API_KEY"),
    ("ai-gateway.x", None, "AI_GATEWAY_X_API_KEY"),
    ("openai", " MY_KEY ", "MY_KEY"),
])
def test_the_key_goes_under_the_variable_openprogram_reads(provider, override, name):
    assert commands.key_env_name(provider, override) == name


@pytest.mark.parametrize("override", ["1KEY", "A-B", "X; rm -rf /"])
def test_a_key_variable_that_is_not_a_name_is_refused(override):
    with pytest.raises(commands.AdapterConfigError):
        commands.key_env_name("openai", override)


def test_no_login_step_and_no_stored_key():
    # The key is the one process's environment variable (as Harbor's Codex
    # adapter passes OPENAI_API_KEY): nothing is written to log in.
    assert not hasattr(commands, "login_command")
    command = commands.run_command("deepseek/deepseek-chat", key_env="DEEPSEEK_API_KEY")
    assert 'export DEEPSEEK_API_KEY="$OPENPROGRAM_HARBOR_API_KEY"; ' \
        "unset OPENPROGRAM_HARBOR_API_KEY;" in command
    assert "providers login" not in command and "marker" not in command


def test_run_reads_the_instruction_on_stdin_from_the_environment():
    command = commands.run_command("deepseek/deepseek-chat")
    words = _words(command)

    assert "--print=-" in words
    assert 'printf %s "$OPENPROGRAM_HARBOR_INSTRUCTION" |' in command
    model = words.index("--model")
    assert words[model + 1] == "deepseek/deepseek-chat"
    usage = words.index("--usage-file")
    assert words[usage + 1] == "/logs/agent/openprogram-usage.json"
    assert "2> /logs/agent/openprogram.stderr.txt" in command
    assert "| tee /logs/agent/openprogram.txt;" in command
    assert 'echo "$!" > /logs/agent/openprogram.pid' in command
    assert "/logs/agent/openprogram-packages.txt" in command
    assert command.endswith('exit "$status"')
    assert "OPENPROGRAM_NO_AUTO_UPDATE=1" in command


def test_run_follows_harbor_logs_dir():
    command = commands.run_command("a/b", logs_dir="/logs/user-agent")

    assert "--usage-file /logs/user-agent/openprogram-usage.json" in command
    assert "tee /logs/user-agent/openprogram.txt" in command


def _usage(**overrides):
    usage = {
        "ended": "completed", "error": None, "session_id": "s1",
        "execution_id": "e1", "model": "deepseek/deepseek-chat", "duration_s": 12.5,
        "input_tokens": 1000, "cached_input_tokens": 9000, "cache_write_tokens": 500,
        "output_tokens": 300, "cost_usd": 0.0125, "unpriced_requests": 0,
        "model_requests": 7, "agent_rounds": 6, "tool_calls": 11,
        "by_model": [
            {"model": "deepseek/deepseek-chat", "requests": 6, "input_tokens": 900,
             "cached_input_tokens": 9000, "cache_write_tokens": 500, "output_tokens": 280,
             "cost_usd": 0.012, "unpriced_requests": 0},
            {"model": "deepseek/deepseek-reasoner", "requests": 1, "input_tokens": 100,
             "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 20,
             "cost_usd": 0.0005, "unpriced_requests": 0},
        ],
    }
    usage.update(overrides)
    return usage


def test_context_counts_all_input_and_cache_reads_like_harbor():
    fields = commands.context_fields(_usage())

    assert fields["n_input_tokens"] == 1000 + 9000 + 500
    assert fields["n_cache_tokens"] == 9000
    assert fields["n_output_tokens"] == 300
    assert fields["cost_usd"] == pytest.approx(0.0125)
    assert fields["model_usage"] == {
        "deepseek/deepseek-chat": {
            "n_input_tokens": 10400, "n_cache_tokens": 9000,
            "n_output_tokens": 280, "cost_usd": pytest.approx(0.012),
        },
        "deepseek/deepseek-reasoner": {
            "n_input_tokens": 100, "n_cache_tokens": 0,
            "n_output_tokens": 20, "cost_usd": pytest.approx(0.0005),
        },
    }
    meta = fields["metadata"]["openprogram"]
    assert meta["tool_calls"] == 11
    assert meta["agent_rounds"] == 6
    assert meta["cache_write_tokens"] == 500
    assert meta["ended"] == "completed"


def test_an_unpriced_run_reports_no_cost_rather_than_a_partial_one():
    usage = _usage(cost_usd=None, unpriced_requests=1)
    usage["by_model"][1]["cost_usd"] = None

    fields = commands.context_fields(usage)

    assert fields["cost_usd"] is None
    assert fields["model_usage"]["deepseek/deepseek-reasoner"]["cost_usd"] is None
    assert fields["model_usage"]["deepseek/deepseek-chat"]["cost_usd"] == pytest.approx(0.012)


def test_a_run_that_never_started_reports_zero_tokens():
    fields = commands.context_fields({"ended": "not_started", "error": "no model",
                                      "by_model": []})

    assert (fields["n_input_tokens"], fields["n_output_tokens"]) == (0, 0)
    assert fields["model_usage"] is None
    assert fields["metadata"]["openprogram"]["error"] == "no model"


@pytest.mark.parametrize("error, kind", [
    ("could not read deepseek's model list (HTTP 401 for https://api.deepseek.com), "
     "so the model 'deepseek-chat' could not be enabled", "authentication"),
    ("could not read deepseek's model list (HTTP 429 for https://api.deepseek.com)",
     "rate_limit"),
    ("could not read x's model list (HTTP 503 for https://x.invalid)", "server"),
    ("could not read x's model list (ConnectTimeout for https://x.invalid)", "network"),
    ("could not read x's model list (ConnectError for https://x.invalid)", "network"),
    ("deepseek does not list the model 'deepseek-chat'", "model_not_found"),
    ("no model is configured", None),
])
def test_a_run_that_never_started_is_classified_by_its_cause(error, kind):
    assert commands.error_kind({"ended": "not_started", "error": error}) == kind


#: What a turn that started records when OpenAI refuses the key (seen in a
#: local trial with a placeholder key).
OPENAI_401 = ("Error code: 401 - {'error': {'message': 'Incorrect API key provided: "
              "sk-inval**********heck. You can find your API key at "
              "https://platform.openai.com/account/api-keys.', 'type': "
              "'invalid_request_error', 'code': 'invalid_api_key', 'param': None}, "
              "'status': 401}")


@pytest.mark.parametrize("error, kind", [
    (OPENAI_401, "authentication"),
    ("Error code: 429 - rate limit reached for requests", "rate_limit"),
    ("Error code: 503 - upstream unavailable", "server"),
    ("ConnectError for https://api.openai.com/v1/responses", "network"),
    ("the model wrote no reply", None),
])
def test_a_turn_the_provider_ended_is_classified_by_its_cause(error, kind):
    # Once the turn started, OpenProgram's own retries are spent before it
    # fails on a provider error: still the provider's fault, not the agent's.
    assert commands.error_kind({"ended": "failed", "error": error}) == kind


@pytest.mark.parametrize("recorded, error, kind", [
    # What --print recorded where it caught the exception wins over wording.
    ({"error_reason": "auth"}, "the turn failed", "authentication"),
    ({"error_reason": "rate_limit"}, "Error code: 503", "rate_limit"),
    ({"error_reason": "provider"}, "", "server"),
    ({"error_kind": "model_unavailable", "error_reason": "invalid"}, "", "model_not_found"),
    ({"error_kind": "stream_idle", "error_reason": "transport"}, "", "stalled"),
    # transport is a stall or a dropped connection: the wording decides.
    ({"error_reason": "transport"}, "no openai stream event for 300s", "stalled"),
    ({"error_reason": "transport"}, "connection reset by peer", "network"),
    # A classified failure that is not the provider's stays the agent's,
    # whatever the text says; unknown leaves it to the text.
    ({"error_reason": "invalid"}, "Error code: 429 - rate limit", None),
    ({"error_reason": "unknown"}, "Error code: 429 - rate limit", "rate_limit"),
])
def test_the_recorded_classification_is_looked_up_before_the_wording(recorded, error, kind):
    usage = {"ended": "failed", "error": error, **recorded}
    assert commands.error_kind(usage) == kind
    # It reaches the trial's metadata, which the launcher classifies from.
    meta = commands.context_fields(usage)["metadata"]["openprogram"]
    assert commands.error_kind(meta) == kind


@pytest.mark.parametrize("usage", [
    None,
    {"ended": "completed"},
    {"ended": "cancelled", "error": "HTTP 429 for https://x.invalid"},
])
def test_only_a_failed_run_gets_an_infrastructure_cause(usage):
    assert commands.error_kind(usage) is None


def test_stop_command_terminates_then_kills():
    command = commands.stop_command(logs_dir="/logs/agent", grace_sec=5)

    assert 'kill -TERM "$pid"' in command
    assert '[ "$waited" -lt 5 ]' in command
    assert "marker" not in command
    assert command.endswith("true")


def test_the_rebuild_writes_a_missing_transcript_from_the_state():
    command = commands.rebuild_transcript_command(logs_dir="/logs/agent")
    assert command == (
        "[ -s /logs/agent/openprogram-transcript.jsonl ] || "
        "/installed-agent/openprogram/bin/openprogram transcript "
        "--output /logs/agent/openprogram-transcript.jsonl "
        "2>> /logs/agent/openprogram.stderr.txt")
    # Not a --print command: Harbor's matcher may type its failure.
    assert commands.harbor_matcher_applies(command)


def test_a_completed_run_with_model_requests_is_a_success():
    assert commands.run_failure(_usage()) is None


@pytest.mark.parametrize("usage, message", [
    (None, "wrote no usage summary"),
    ({"ended": "failed", "error": "401 Unauthorized"}, "ended 'failed': 401 Unauthorized"),
    ({"ended": "cancelled"}, "ended 'cancelled'"),
    ({"ended": "completed", "model_requests": 0}, "without a single model request"),
])
def test_a_zero_exit_without_a_completed_turn_is_a_failure(usage, message):
    assert message in commands.run_failure(usage)


def test_usage_text_parses_to_a_dict_or_none():
    assert commands.parse_usage(json.dumps(_usage()))["tool_calls"] == 11
    assert commands.parse_usage("") is None
    assert commands.parse_usage("[1, 2]") is None
    assert commands.parse_usage("{truncated") is None


_TRANSCRIPT = [
    {"type": "run", "session_id": "s1", "model": "openai/gpt-x", "run_note": "no one watches",
     "ended": "completed"},
    {"type": "user", "time": "2026-01-01T00:00:00.000+00:00", "text": "do it"},
    {"type": "request", "n": 1, "call_kind": "title", "model": "openai/gpt-x",
     "input_tokens": 5, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 1},
    {"type": "request", "n": 2, "call_kind": "chat", "time": "2026-01-01T00:00:01.000+00:00",
     "model": "openai/gpt-x", "input_tokens": 100, "cached_input_tokens": 50,
     "cache_write_tokens": 0, "output_tokens": 10},
    {"type": "reasoning", "text": "think"},
    {"type": "assistant_text", "text": "writing"},
    {"type": "tool_call", "tool_call_id": "c1", "name": "write",
     "arguments": {"file_path": "a.txt", "content": "x"}},
    {"type": "tool_call", "tool_call_id": "c2", "name": "bash", "arguments": "ls"},
    {"type": "tool_result", "tool_call_id": "c1", "time": "2026-01-01T00:00:02.000+00:00",
     "is_error": False, "content": "Wrote 1 bytes", "complete": True},
    {"type": "tool_result", "tool_call_id": "c2", "is_error": True, "content": {"err": 1}},
    {"type": "request", "n": 3, "call_kind": "chat", "time": "2026-01-01T00:00:03.000+00:00",
     "model": "openai/gpt-x", "input_tokens": 20, "cached_input_tokens": 150,
     "cache_write_tokens": 0, "output_tokens": 5},
    {"type": "assistant_text", "text": "done"},
    {"type": "reply", "text": "done"},
]


def test_the_transcript_becomes_one_agent_step_per_model_request():
    text = "\n".join(json.dumps(r) for r in _TRANSCRIPT) + "\nnot json\n"
    records = commands.parse_transcript(text)
    usage = {"input_tokens": 125, "cached_input_tokens": 200, "output_tokens": 16,
             "cost_usd": 0.01, "ended": "completed"}

    data = commands.trajectory(records, usage, agent_version="1.0")

    user, first, last = data["steps"]
    assert [s["step_id"] for s in data["steps"]] == [1, 2, 3]
    assert user == {"step_id": 1, "source": "user", "message": "do it",
                    "timestamp": "2026-01-01T00:00:00.000+00:00",
                    "extra": {"run_note": "no one watches"}}
    assert first["message"] == "writing" and first["reasoning_content"] == "think"
    assert first["metrics"] == {"prompt_tokens": 150, "completion_tokens": 10,
                                "cached_tokens": 50}
    assert first["tool_calls"] == [
        {"tool_call_id": "c1", "function_name": "write",
         "arguments": {"file_path": "a.txt", "content": "x"}},
        {"tool_call_id": "c2", "function_name": "bash", "arguments": {"input": "ls"}},
    ]
    results = first["observation"]["results"]
    assert [r["source_call_id"] for r in results] == ["c1", "c2"]
    assert results[0]["content"] == "Wrote 1 bytes"
    assert results[1]["content"] == '{"err": 1}' and results[1]["extra"]["is_error"]
    # Whether the text is whole travels with it; an older transcript has no mark.
    assert results[0]["extra"]["complete"] is True and "complete" not in results[1]["extra"]
    assert last["message"] == "done" and "tool_calls" not in last
    assert data["final_metrics"]["total_prompt_tokens"] == 325
    assert data["final_metrics"]["total_steps"] == 3
    assert data["final_metrics"]["extra"]["other_requests"] == 1
    assert data["agent"] == {"name": "openprogram", "version": "1.0",
                             "model_name": "openai/gpt-x"}


def test_a_transcript_without_the_instruction_gives_no_trajectory():
    assert commands.trajectory([], None) is None
    assert commands.trajectory(commands.parse_transcript(""), {}) is None


def test_the_trajectory_is_valid_atif_when_harbor_is_installed():
    models = pytest.importorskip("harbor.models.trajectories")
    data = commands.trajectory(_TRANSCRIPT, {"cost_usd": None})
    assert models.Trajectory.model_validate(data).steps[1].tool_calls[0].function_name == "write"


def test_run_writes_the_transcript_beside_the_usage_summary():
    words = _words(commands.run_command("a/b"))

    transcript = words.index("--transcript-file")
    assert words[transcript + 1] == "/logs/agent/openprogram-transcript.jsonl"


def test_the_bundle_install_needs_no_network():
    command = commands.bundle_install_command()
    assert "curl" not in command and "uv" not in _words(command)
    assert f"tar -xzf {commands.BUNDLE_PATH} -C /;" in command
    assert command.endswith(f"{commands.OPENPROGRAM} --version")


def test_the_bundle_is_only_used_where_it_can_run():
    check = commands.BUNDLE_USABLE_CHECK
    for needed in ("x86_64", "GNU_LIBC_VERSION", "command -v tar", "command -v gzip"):
        assert needed in check


@pytest.mark.parametrize("usage, stalled", [
    ({"ended": "failed", "error": "no openai stream event for 300s"}, True),
    ({"ended": "failed", "error": "no anthropic stream response for 300s"}, True),
    # Providers that bound their own stream (Chat Completions, Codex, Gemini
    # CLI) name the stall in their own words; the turn reports it verbatim
    # once its re-sends are spent.
    ({"ended": "failed", "error": "no deepseek chunk event for 300s"}, True),
    ({"ended": "failed", "error": "no openrouter response for 300s"}, True),
    ({"ended": "failed", "error": "no Codex WebSocket bytes for 600.0s"}, True),
    ({"ended": "failed", "error": "no SSE data event for 300.0s"}, True),
    ({"ended": "failed", "error": "no gemini-subscription event event for 300s"}, True),
    ({"ended": "failed", "error": "upstream 500"}, False),
    ({"ended": "failed", "error": "no such file or directory"}, False),
    ({"ended": "completed", "error": None}, False),
    ({"ended": "cancelled", "error": "no openai stream event for 300s"}, False),
    (None, False),
])
def test_a_stream_that_went_silent_is_told_apart_from_the_agent_failing(usage, stalled):
    # Seen in real trials: the turn ended after 300 s with no stream event,
    # and the task was scored as the agent's failure.
    assert (commands.error_kind(usage) == "stalled") is stalled


def test_every_attempt_is_kept_in_the_ledger_the_last_line_of_each_winning(tmp_path):
    ledger = tmp_path / "jobs" / "j1.attempts.jsonl"
    fields = commands.context_fields({
        "ended": "failed", "error": "no openai stream event for 300s",
        "input_tokens": 100, "cached_input_tokens": 900, "output_tokens": 20,
        "cost_usd": 0.163})
    commands.append_attempt(ledger, trial="freecad__a", attempt="one", fields=fields,
                            error_kind="stalled")
    commands.append_attempt(ledger, trial="freecad__a", attempt="two",
                            fields={**fields, "cost_usd": 0.1})
    commands.append_attempt(ledger, trial="freecad__a", attempt="two",
                            fields={**fields, "cost_usd": 0.2})
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    attempts = commands.read_attempts(ledger)
    assert sorted(attempts) == ["one", "two"]
    assert attempts["one"] == {
        "trial": "freecad__a", "attempt": "one", "agent": "openprogram",
        "error_kind": "stalled", "cost_usd": 0.163,
        "n_input_tokens": 1000, "n_cache_tokens": 900, "n_output_tokens": 20,
        "ended": "failed", "error": "no openai stream event for 300s"}
    assert attempts["two"]["cost_usd"] == 0.2
    assert commands.read_attempts(tmp_path / "missing.jsonl") == {}


# ------------------------------------------------- one attempt, step by step
#
# OpenProgramAgent.run hands its steps to commands.run_attempt, so the order
# and the cleanup are checked here without Harbor.

class _Steps:
    def __init__(self, **raises):
        self.raises = raises
        self.done: list[str] = []
        self.warnings: list[str] = []

    def step(self, name):
        async def run():
            self.done.append(name)
            if name in self.raises:
                raise self.raises[name]
        return run

    def attempt(self):
        import asyncio

        return asyncio.run(commands.run_attempt(
            turn=self.step("turn"), stop=self.step("stop"),
            finish=self.step("finish"), rebuild=self.step("rebuild"),
            warn=self.warnings.append))


def test_an_attempt_runs_the_turn_then_finishes_then_rebuilds():
    steps = _Steps()
    steps.attempt()
    assert steps.done == ["turn", "finish", "rebuild"]


def test_a_failed_turn_still_finishes_and_rebuilds():
    steps = _Steps(turn=RuntimeError("turn failed"))
    with pytest.raises(RuntimeError, match="turn failed"):
        steps.attempt()
    assert steps.done == ["turn", "finish", "rebuild"]


def test_a_failing_finish_still_rebuilds_and_warns():
    steps = _Steps(finish=OSError("container gone"))
    steps.attempt()
    assert steps.done == ["turn", "finish", "rebuild"]
    assert "container gone" in steps.warnings[0]


def test_a_failing_rebuild_only_warns():
    steps = _Steps(rebuild=OSError("no state"))
    steps.attempt()
    assert "not rebuilt" in steps.warnings[0]


def test_harbors_agent_timeout_stops_the_run_before_the_finish():
    import asyncio

    steps = _Steps(turn=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        steps.attempt()
    assert steps.done == ["turn", "stop", "finish", "rebuild"]


def test_a_failing_finish_does_not_hide_the_attempts_own_error():
    steps = _Steps(turn=RuntimeError("turn failed"), finish=OSError("container gone"))
    with pytest.raises(RuntimeError, match="turn failed"):
        steps.attempt()
    assert steps.warnings and "container gone" in steps.warnings[0]


def test_only_the_run_command_is_kept_from_harbors_error_matcher():
    # Its output is the model's text and tool output, which can quote any
    # provider error ("invalid api key"); the usage summary types it instead.
    assert not commands.harbor_matcher_applies(commands.run_command("openai/m"))
    assert commands.harbor_matcher_applies(commands.install_command("/w/x.whl"))


def test_the_agent_hands_its_run_and_error_typing_to_these_functions():
    """agent.py imports Harbor, so its tests are skipped where Harbor is not
    installed; this reads its source to check the two overrides are there
    and use the functions tested above."""
    import ast
    from pathlib import Path

    source = (Path(commands.__file__).with_name("agent.py")).read_text(encoding="utf-8")
    agent = next(node for node in ast.parse(source).body
                 if isinstance(node, ast.ClassDef) and node.name == "OpenProgramAgent")
    methods = {node.name: ast.unparse(node) for node in agent.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "commands.run_attempt(" in methods["run"]
    assert "commands.harbor_matcher_applies(" in methods["_classify_exec_error"]
    assert "commands.error_kind(" in methods["_run_turn"]
    # Harbor's reasoning_effort reaches the run command, and the class
    # declares the capabilities below.
    assert "effort=self._effort" in methods["_run_turn"]
    assert "commands.effort_flag(reasoning_effort)" in methods["__init__"]
    assert "AgentCapabilities(**commands.CAPABILITIES)" in ast.unparse(agent)
    assert "SUPPORTS_ATIF" not in ast.unparse(agent)


def test_the_adapter_declares_atif_reasoning_effort_and_web_tools():
    assert commands.CAPABILITIES == {"atif": True, "reasoning_effort": True,
                                     "native_web_tools": True}


def test_harbors_disable_web_search_becomes_no_web():
    words = _words(commands.run_command("openai/m", no_web=True))
    assert "--no-web" in words
    assert words.index("--model") < words.index("--no-web") < words.index("--usage-file")
    assert "--no-web" not in _words(commands.run_command("openai/m"))


def test_the_agent_turns_web_tools_off_through_harbors_hook():
    import ast
    from pathlib import Path

    source = (Path(commands.__file__).with_name("agent.py")).read_text(encoding="utf-8")
    agent = next(node for node in ast.parse(source).body
                 if isinstance(node, ast.ClassDef) and node.name == "OpenProgramAgent")
    methods = {node.name: ast.unparse(node) for node in agent.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "set_web_tools_disabled" in methods
    assert "no_web=self._no_web" in methods["_run_turn"]
    assert "commands.run_attempt(" in methods["run"] and "rebuild=" in methods["run"]
    assert "login" not in methods["run"]


@pytest.mark.parametrize("effort", commands.HARBOR_EFFORTS)
def test_harbors_reasoning_effort_becomes_the_effort_flag(effort):
    words = _words(commands.run_command("openai/m", effort=effort))
    assert words[words.index("--effort") + 1] == effort
    # The flag sits between the model and the files, inside the run.
    assert words.index("--model") < words.index("--effort") < words.index("--usage-file")


def test_without_an_effort_the_run_command_is_unchanged():
    command = commands.run_command("openai/m")
    assert "--effort" not in _words(command)
    assert command == commands.run_command("openai/m", effort=None)


@pytest.mark.parametrize("effort", ["", "extreme", 3, "off"])
def test_an_effort_harbor_does_not_name_is_refused(effort):
    with pytest.raises(commands.AdapterConfigError, match="reasoning_effort"):
        commands.effort_flag(effort)


@pytest.mark.parametrize("recorded, kind", [
    # A recorded category decides alone, whatever the reason and the words.
    ({"error_kind": "stream_total", "error_reason": "unknown"}, "stalled"),
    ({"error_kind": "auth", "error_reason": "unknown"}, "authentication"),
    ({"error_kind": "memory_unavailable", "error_reason": "rate_limit"}, None),
    # Reasons the table names as the agent's own failure.
    ({"error_reason": "context"}, None),
    ({"error_reason": "policy"}, None),
    ({"error_reason": "blocked"}, "network"),
    ({"error_reason": "timeout"}, "network"),
])
def test_the_recorded_tables_settle_a_failure_without_its_wording(recorded, kind):
    usage = {"ended": "failed", "error": "Error code: 429 - no openai stream event for 300s",
             **recorded}
    assert commands.error_kind(usage) == kind
    assert commands.error_kind({**usage, "ended": "not_started"}) == kind


def test_the_effort_and_tool_count_reach_the_trial_metadata():
    meta = commands.context_fields(_usage(effort_requested="high", effort="high",
                                          tools_count=17))["metadata"]["openprogram"]
    assert (meta["effort_requested"], meta["effort"], meta["tools_count"]) == ("high", "high", 17)


def test_the_requests_by_kind_reach_the_trial_metadata():
    by_kind = {"chat": {"requests": 6}, "summarize": {"requests": 1}}
    meta = commands.context_fields(_usage(by_call_kind=by_kind))["metadata"]["openprogram"]
    assert meta["by_call_kind"] == by_kind

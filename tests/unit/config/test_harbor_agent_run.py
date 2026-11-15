"""The Harbor adapter's run path against a stand-in environment.

Needs Harbor itself (``harbor==0.25.0``), which is not an OpenProgram
dependency: the module is skipped where Harbor is not installed. The order
of an attempt's steps and which commands Harbor's matcher may type are
tested without Harbor in ``test_harbor_agent_commands.py``; this module
checks the adapter wires them into Harbor's real base class.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("harbor.agents.installed.base")

from harbor.agents.installed.base import (  # noqa: E402
    AgentAuthenticationError,
    ApiRateLimitError,
    ApiResponseStalledError,
    NonZeroAgentExitCodeError,
)
from harbor.models.agent.context import AgentContext  # noqa: E402

from scripts.benchmarks.harbor_agent import agent as adapter  # noqa: E402
from scripts.benchmarks.harbor_agent import commands  # noqa: E402

#: Model text that echoes provider error words; the run's output holds it.
ECHO = ("The test prints 'HTTP 429 Too Many Requests: rate limit exceeded' "
        "and 'invalid api key'; Error: overloaded_error")


class FakeEnvironment:
    """Records every command; answers each by the first matching rule."""

    def __init__(self, rules):
        self.rules = rules
        self.calls: list[tuple[str, str | None]] = []
        self.default_user = None

    async def exec(self, command, user=None, env=None, cwd=None, timeout_sec=None):
        self.calls.append((command, user))
        for needle, result in self.rules:
            if needle in command:
                if isinstance(result, BaseException):
                    raise result
                return SimpleNamespace(return_code=result[0], stdout=result[1], stderr="")
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    def steps(self) -> list[str]:
        names = []
        for command, _user in self.calls:
            for step, needle in (("login", "providers login"), ("run", "--print="),
                                 ("stop", "kill -TERM"),
                                 ("finish", commands.EVIDENCE_DIR_NAME),
                                 ("usage", "cat ")):
                if needle in command:
                    names.append(step)
                    break
        return names


class Agent(adapter.OpenProgramAgent):
    @property
    def model_connection(self):
        return SimpleNamespace(api_key="sk-test-value")


@pytest.fixture
def agent(tmp_path: Path) -> Agent:
    wheel = tmp_path / "openprogram-1.0-py3-none-any.whl"
    wheel.write_bytes(b"")
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("")
    logs = tmp_path / "logs"
    logs.mkdir()
    return Agent(logs_dir=logs, model_name="deepseek/deepseek-chat",
                 wheel=str(wheel), constraints=str(constraints))


def _usage(**fields) -> tuple[int, str]:
    return 0, json.dumps({"model_requests": 3, **fields})


def _run(agent, environment):
    return asyncio.run(agent.run("do the task", environment, AgentContext()))


def test_model_text_in_the_run_output_never_picks_the_error_type(agent):
    # Harbor's own matcher reads stdout and stderr; the run's output is the
    # model's text and tool output, so it must not decide the type.
    run = commands.run_command("x", "deepseek/deepseek-chat")
    error = agent._classify_exec_error(run, SimpleNamespace(
        return_code=1, stdout=ECHO, stderr=""))

    assert type(error) is NonZeroAgentExitCodeError


def test_other_commands_keep_harbors_matcher(agent):
    login = commands.login_command("deepseek")
    error = agent._classify_exec_error(login, SimpleNamespace(
        return_code=1, stdout="HTTP 429 Too Many Requests", stderr=""))

    assert isinstance(error, ApiRateLimitError)


def test_a_failed_run_is_typed_by_the_usage_summary_not_its_output(agent):
    environment = FakeEnvironment([
        ("--print=", (1, ECHO)),
        ("cat ", _usage(ended="failed", error="the agent gave up")),
    ])

    with pytest.raises(NonZeroAgentExitCodeError) as raised:
        _run(agent, environment)

    assert type(raised.value) is NonZeroAgentExitCodeError


@pytest.mark.parametrize("usage, expected", [
    (dict(ended="not_started", error="could not read x's model list (HTTP 429 for https://x)"),
     ApiRateLimitError),
    (dict(ended="failed", error="no openai stream event for 300s"), ApiResponseStalledError),
    # A placeholder key in a local trial: the provider refused the turn's
    # first request.
    (dict(ended="failed", error="Error code: 401 - {'error': {'code': 'invalid_api_key'}}"),
     AgentAuthenticationError),
])
def test_a_failed_run_takes_the_type_its_summary_names(agent, usage, expected):
    environment = FakeEnvironment([("--print=", (1, "")), ("cat ", _usage(**usage))])

    with pytest.raises(NonZeroAgentExitCodeError) as raised:
        _run(agent, environment)

    assert type(raised.value) is expected


def test_a_finished_run_copies_its_state_out_and_removes_the_credential(agent):
    environment = FakeEnvironment([("cat ", _usage(ended="completed"))])

    _run(agent, environment)

    assert environment.steps() == ["login", "run", "usage", "finish"]
    finish, user = environment.calls[-1]
    assert user is None  # the agent user, whose home holds the credential
    assert finish == commands.finish_command(logs_dir=agent.environment_logs_dir)
    assert '"$HOME"/.openprogram "$HOME"/.openprogram-*' in finish


def test_a_failed_run_still_removes_the_credential(agent):
    environment = FakeEnvironment([
        ("--print=", (1, "")), ("cat ", _usage(ended="failed", error="x")),
    ])

    with pytest.raises(NonZeroAgentExitCodeError):
        _run(agent, environment)

    assert environment.steps()[-1] == "finish"


def test_a_failed_login_still_removes_the_credential(agent):
    environment = FakeEnvironment([("providers login", (1, "disk full"))])

    with pytest.raises(NonZeroAgentExitCodeError):
        _run(agent, environment)

    assert environment.steps() == ["login", "finish"]


def test_the_agent_timeout_stops_the_run_then_copies_and_removes(agent):
    environment = FakeEnvironment([("--print=", asyncio.CancelledError())])

    with pytest.raises(asyncio.CancelledError):
        _run(agent, environment)

    assert environment.steps() == ["login", "run", "stop", "finish"]


def test_a_failing_cleanup_does_not_hide_the_runs_own_error(agent):
    environment = FakeEnvironment([
        ("--print=", (1, "")), ("cat ", _usage(ended="failed", error="x")),
        (commands.EVIDENCE_DIR_NAME, RuntimeError("container gone")),
    ])

    with pytest.raises(NonZeroAgentExitCodeError):
        _run(agent, environment)

"""The memory writer's process environment, as the SDK consumes it.

``ClaudeAgentOptions.env`` is a mapping the SDK spreads over ``os.environ``
to build the CLI's environment:

    process_env = {**inherited_env, ..., **options.env, ...}

``None`` is not a mapping, so passing it raises a ``TypeError`` before the
CLI is ever spawned — the whole inherit-the-user's-login path died there,
and nothing in the run reached far enough to report why. "Add nothing to
what I inherited" is spelled ``{}``.

The fake query below performs the same spread the SDK does, so the contract
is exercised rather than merely asserted.
"""
from __future__ import annotations

import pytest

from claude_agent_sdk import ResultMessage


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="s1", result="done", usage={},
    )


class _Recorder:
    """Stands in for ``claude_agent_sdk.query``, keeping the options."""

    def __init__(self):
        self.options = None

    async def __call__(self, *, prompt, options):
        self.options = options
        # Exactly what the SDK's subprocess transport does with this field.
        assert {**options.env} == dict(options.env)
        yield _result()


@pytest.fixture
def recorder():
    return _Recorder()


def _run(config, recorder, cwd):
    from openprogram.memory.scriptorium.agent_runtime import ClaudeCodeAgent
    agent = ClaudeCodeAgent(config, query_fn=recorder)
    return agent.run(prompt="hello", system_prompt="be brief", cwd=cwd)


def test_inherited_login_adds_nothing_to_the_environment(tmp_path, recorder):
    from openprogram.memory.scriptorium.agent_runtime import ClaudeCodeConfig

    result = _run(ClaudeCodeConfig.inherited(), recorder, tmp_path)

    assert result.text == "done"
    assert recorder.options.env == {}


def test_nested_claude_disables_builtin_file_and_command_tools(tmp_path, recorder):
    from openprogram.memory.scriptorium.agent_runtime import ClaudeCodeConfig

    _run(ClaudeCodeConfig.inherited(), recorder, tmp_path)

    assert recorder.options.tools == []
    assert recorder.options.allowed_tools == []
    assert {"Read", "Write", "Edit", "Grep", "Glob", "Bash"} <= set(
        recorder.options.disallowed_tools
    )


def test_a_provisioned_credential_still_overrides_the_environment(
    tmp_path, recorder
):
    from openprogram.memory.scriptorium.agent_runtime import ClaudeCodeConfig

    _run(
        ClaudeCodeConfig(
            base_url="https://example.invalid",
            api_key="sk-test",
            model="claude-test",
        ),
        recorder,
        tmp_path,
    )

    env = recorder.options.env
    assert env["ANTHROPIC_BASE_URL"] == "https://example.invalid"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    # A key set here must not be undercut by an inherited OAuth token, and
    # the CLI must not read the user's own config directory.
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CONFIG_DIR"]

from __future__ import annotations

import json
import errno
import os
import re
import subprocess
import sys
import textwrap

if sys.platform != "win32":
    import pty
    import tty

import pytest

from openprogram.host._platform.terminal import InteractivePty
from openprogram.interfaces.cli import application as cli
from openprogram.models.providers.structured_output import (
    StructuredOutputUnsupportedError,
    StructuredOutputValidationError,
)


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_cli_main_preflights_schema_before_chat_runtime(tmp_path, monkeypatch):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    seen = {}

    def run(*, oneshot, resume, tui, response_format, no_alt_screen=False, screen_reader=False):
        seen.update(prompt=oneshot, response_format=response_format)

    monkeypatch.setattr(cli, "_cmd_cli_chat", run)
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "answer", "--json-schema", str(path)])
    cli.main()

    assert seen["prompt"] == "answer"
    assert seen["response_format"].schema == SCHEMA


def test_cli_rejects_prompt_and_schema_both_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "-", "--json-schema", "-"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "stdin" in capsys.readouterr().err.lower()


def test_cli_missing_schema_file_is_usage_error(monkeypatch, capsys, tmp_path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["openprogram", "--print", "answer", "--json-schema", str(missing)],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert str(missing) not in capsys.readouterr().err


def test_cli_json_schema_requires_one_shot_print(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openprogram", "--json-schema", "schema.json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_invalid_envelope_does_not_echo_schema_text(tmp_path, monkeypatch, capsys):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({"type": "secret-schema-type"}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["openprogram", "--print", "answer", "--json-schema", str(path)],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "secret" not in capsys.readouterr().err


def test_cli_deep_stdin_schema_is_bounded_usage_error():
    deep_schema = "[" * 10_000 + "0" + "]" * 10_000
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openprogram.interfaces.cli",
            "--print",
            "answer",
            "--json-schema",
            "-",
        ],
        input=deep_schema,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "invalid_schema: Structured output request is invalid\n"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (StructuredOutputUnsupportedError("not supported", code="unsupported"), 3),
        (StructuredOutputValidationError("failed", code="validation_failed"), 4),
    ],
)
def test_cli_maps_typed_errors_without_candidate_text(
    tmp_path, monkeypatch, capsys, error, exit_code
):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(SCHEMA), encoding="utf-8")

    def run(**_kwargs):
        raise error

    monkeypatch.setattr(cli, "_cmd_cli_chat", run)
    monkeypatch.setattr(sys, "argv", ["openprogram", "--print", "answer", "--json-schema", str(path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == exit_code
    stderr = capsys.readouterr().err
    assert error.code in stderr
    assert "secret candidate" not in stderr


def _resumable(monkeypatch):
    """``--resume`` names a conversation that exists (``print_session``)."""
    from openprogram.interfaces.cli import print_session

    monkeypatch.setattr(print_session, "_exists", lambda session_id: True)


def test_one_shot_structured_result_is_json_only(monkeypatch, capsys):
    from openprogram.interfaces.cli import chat as cli_chat
    from openprogram.interfaces.cli import print_turn, worker_turn
    from openprogram.runtime.agent_registry import builtin_agents

    class Agent:
        id = "main"

    def finished(session_id, text, *, agent_id, model=None, response_format=None, run, **_kw):
        run.ended = print_turn.ENDED_COMPLETED
        run.structured_output = {"answer": 3}
        return run

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: None)
    monkeypatch.setattr(builtin_agents, "ensure_default_agent", lambda: Agent())
    monkeypatch.setattr(print_turn, "chosen_model", lambda agent_id, model: "test/m")
    monkeypatch.setattr(print_turn, "run_in_process", finished)
    _resumable(monkeypatch)
    cli_chat.run_cli_chat(oneshot="answer", resume="local_x", tui=False, response_format=SCHEMA)
    assert capsys.readouterr().out == '{"answer":3}\n'


def test_one_shot_structured_failure_raises_its_code(monkeypatch):
    from types import SimpleNamespace

    from openprogram.interfaces.cli import chat as cli_chat
    from openprogram.interfaces.cli import print_turn, worker_turn
    from openprogram.models.providers.structured_output import StructuredOutputSchemaError
    from openprogram.runtime.agent_registry import builtin_agents

    def failed(session_id, text, *, agent_id, model=None, response_format=None, run, **_kw):
        run.ended = print_turn.ENDED_FAILED
        run.error = "schema rejected"
        run.result = SimpleNamespace(structured_error_code="invalid_schema",
                                     structured_output_issues=[])
        return run

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: None)
    monkeypatch.setattr(builtin_agents, "ensure_default_agent", lambda: SimpleNamespace(id="main"))
    monkeypatch.setattr(print_turn, "chosen_model", lambda agent_id, model: "test/m")
    monkeypatch.setattr(print_turn, "run_in_process", failed)
    _resumable(monkeypatch)
    with pytest.raises(StructuredOutputSchemaError):
        cli_chat.run_cli_chat(oneshot="answer", resume="local_x", tui=False, response_format=SCHEMA)


def _run_one_shot_in_pty(*, response_format, reply):
    script = textwrap.dedent(
        f"""
        import time

        from openprogram.interfaces.cli import chat as cli_chat
        from openprogram.interfaces.cli import print_turn, worker_turn
        from openprogram.runtime.agent_registry import builtin_agents

        class Agent:
            id = "main"

        reply = {reply!r}

        def delayed_run(session_id, text, *, agent_id, model=None, response_format=None, run, **_kw):
            time.sleep(0.5)
            run.ended = print_turn.ENDED_COMPLETED
            if isinstance(reply, str):
                run.reply = reply
            else:
                run.structured_output = reply
            return run

        from openprogram.interfaces.cli import print_session
        print_session._exists = lambda session_id: True
        worker_turn.resolve_worker = lambda: None
        builtin_agents.ensure_default_agent = lambda: Agent()
        print_turn.chosen_model = lambda agent_id, model: "test/m"
        print_turn.run_in_process = delayed_run
        cli_chat.run_cli_chat(
            oneshot="answer",
            resume="local_pty",
            tui=False,
            response_format={response_format!r},
        )
        """
    )
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env.pop("NO_COLOR", None)
    if sys.platform == "win32":
        terminal = InteractivePty([sys.executable, "-c", script], env=env)
        chunks = []
        try:
            while terminal.alive:
                chunk = terminal.read_nonblocking(timeout=0.1)
                if chunk:
                    chunks.append(chunk)
            returncode = terminal.wait(timeout=10)
            while True:
                chunk = terminal.read_nonblocking(timeout=0.05)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            terminal.close()
        output = "".join(chunks).replace("\r\n", "\n")
        # ConPTY's host negotiation writes terminal-control CSI sequences
        # before the child output. A real terminal consumes them; strip them
        # here so the assertion compares the user-visible text.
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        return output, "", returncode

    master_fd, slave_fd = pty.openpty()
    tty.setraw(slave_fd)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env=env,
        )
        os.close(slave_fd)
        slave_fd = -1
        chunks = []
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
        process.wait(timeout=10)
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        os.close(master_fd)
        if slave_fd >= 0:
            os.close(slave_fd)

    return (
        b"".join(chunks).decode("utf-8"),
        stderr.decode("utf-8", errors="replace"),
        process.returncode,
    )


def test_one_shot_structured_result_is_json_only_on_tty():
    stdout, stderr, returncode = _run_one_shot_in_pty(
        response_format={"type": "object"},
        reply={"answer": 3},
    )
    assert returncode == 0, stderr
    assert stdout == '{"answer":3}\n'
    assert json.loads(stdout) == {"answer": 3}


def test_one_shot_text_is_only_the_reply_on_tty():
    stdout, stderr, returncode = _run_one_shot_in_pty(
        response_format=None,
        reply="plain answer",
    )
    assert returncode == 0, stderr
    assert stdout == "plain answer\n"

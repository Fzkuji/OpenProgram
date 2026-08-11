from __future__ import annotations

import json
import errno
import os
import pty
import subprocess
import sys
import textwrap
import tty

import pytest

from openprogram import cli
from openprogram.providers.structured_output import (
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

    def run(*, oneshot, resume, tui, response_format):
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


def test_one_shot_structured_result_is_json_only(monkeypatch, capsys):
    from openprogram import cli_chat

    class Agent:
        id = "main"

    monkeypatch.setattr(cli_chat, "_get_chat_runtime", lambda: ("test", object()))
    monkeypatch.setattr("openprogram.agent.management.manager.get_default", lambda: Agent())
    monkeypatch.setattr(
        cli_chat,
        "_run_turn_with_history",
        lambda *args, **kwargs: {"answer": 3},
    )
    cli_chat.run_cli_chat(oneshot="answer", tui=False, response_format=SCHEMA)
    assert capsys.readouterr().out == '{"answer":3}\n'


@pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")
def test_one_shot_structured_result_is_json_only_on_tty():
    script = textwrap.dedent(
        """
        import time

        from openprogram import cli_chat
        from openprogram.agent.management import manager

        class Agent:
            id = "main"

        def delayed_runtime():
            time.sleep(0.5)
            return "test", object()

        cli_chat._get_chat_runtime = delayed_runtime
        cli_chat._run_turn_with_history = lambda *args, **kwargs: {"answer": 3}
        manager.get_default = lambda: Agent()
        cli_chat.run_cli_chat(
            oneshot="answer",
            tui=False,
            response_format={"type": "object"},
        )
        """
    )
    master_fd, slave_fd = pty.openpty()
    tty.setraw(slave_fd)
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env.pop("NO_COLOR", None)
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

    stdout = b"".join(chunks).decode("utf-8")
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout == '{"answer":3}\n'
    assert json.loads(stdout) == {"answer": 3}

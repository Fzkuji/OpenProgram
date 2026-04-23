"""Tests for the CliRunner one-shot path (phase 1b)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from openprogram.providers._shared.cli_backend import (
    CliBackendConfig,
    CliBackendPlugin,
    CliRunner,
    Done,
    Error,
    SessionInfo,
    TextDelta,
    ToolCall,
    Usage,
)


def _write_fake_cli(
    tmpdir: Path,
    *,
    lines: list[dict],
    exit_code: int = 0,
    stderr: str = "",
    echo_argv: bool = False,
) -> Path:
    """Write an executable shell script that prints ``lines`` as JSONL.

    The script ignores its inputs — we just want to assert the runner
    parses whatever the CLI emits, not that the CLI does anything real.
    """
    script = tmpdir / "fake_cli"
    payload_path = tmpdir / "payload.jsonl"
    with payload_path.open("w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")
    argv_dump = ""
    if echo_argv:
        argv_dump = 'printf "ARGV=%s\\n" "$*" 1>&2\n'
    stderr_block = ""
    if stderr:
        stderr_block = f'printf "%s" {json.dumps(stderr)} 1>&2\n'
    script.write_text(
        "#!/bin/sh\n"
        + argv_dump
        + f'cat {json.dumps(str(payload_path))}\n'
        + stderr_block
        + f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _make_plugin(cmd: str, **config_overrides) -> CliBackendPlugin:
    cfg = CliBackendConfig(
        command=cmd,
        output="jsonl",
        jsonl_dialect="claude-stream-json",
        **config_overrides,
    )
    return CliBackendPlugin(id="fake-cli", config=cfg)


async def _collect(runner: CliRunner, prompt: str, **kw) -> list:
    events = []
    async for ev in runner.run(prompt, model_id=kw.pop("model_id", "claude-sonnet-4-6"), **kw):
        events.append(ev)
    return events


def test_runner_parses_full_turn(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, lines=[
        {"type": "system", "session_id": "sess-1", "model": "claude-sonnet-4-6"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}},
        ]}},
        {"type": "result", "result": "ok",
         "usage": {"input_tokens": 100, "output_tokens": 50,
                   "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10},
         "modelUsage": {"claude-sonnet-4-6": {"contextWindow": 200000}},
         "duration_ms": 1200, "num_turns": 1},
    ])
    runner = CliRunner(
        plugin=_make_plugin(str(cli)),
        workspace_dir=str(tmp_path),
    )
    events = asyncio.run(_collect(runner, "hi"))

    types = [type(e).__name__ for e in events]
    assert types == ["SessionInfo", "TextDelta", "ToolCall", "Usage", "Done"]

    sess = events[0]
    assert isinstance(sess, SessionInfo)
    assert sess.session_id == "sess-1"
    assert sess.model_id == "claude-sonnet-4-6"

    text = events[1]
    assert isinstance(text, TextDelta) and text.text == "Hello"

    call = events[2]
    assert isinstance(call, ToolCall) and call.name == "bash"
    assert call.input == {"cmd": "ls"}

    usage = events[3]
    assert isinstance(usage, Usage)
    # 100 + 20 + 10 = 130 (total input, including cache reads + creates)
    assert usage.input_tokens == 130
    assert usage.cache_read == 20
    assert usage.cache_create == 10
    assert usage.context_window == 200000

    done = events[4]
    assert isinstance(done, Done)
    assert done.duration_ms >= 0


def test_runner_ignores_unknown_messages(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, lines=[
        {"type": "something_random", "data": 42},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
    ])
    runner = CliRunner(plugin=_make_plugin(str(cli)), workspace_dir=str(tmp_path))
    events = asyncio.run(_collect(runner, "hi"))
    # SessionInfo not emitted because no "system" message; unknown msg skipped.
    assert [type(e).__name__ for e in events] == ["TextDelta", "Done"]


def test_runner_emits_error_on_nonzero_exit(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, lines=[], exit_code=7, stderr="boom")
    runner = CliRunner(plugin=_make_plugin(str(cli)), workspace_dir=str(tmp_path))
    events = asyncio.run(_collect(runner, "hi"))
    assert len(events) == 1 and isinstance(events[0], Error)
    assert events[0].kind == "ExitCode(7)"
    assert "boom" in events[0].message


def test_runner_emits_error_when_cli_missing(tmp_path: Path) -> None:
    runner = CliRunner(
        plugin=_make_plugin("/nonexistent/no_such_cli_xyzzy"),
        workspace_dir=str(tmp_path),
    )
    events = asyncio.run(_collect(runner, "hi"))
    assert len(events) == 1 and isinstance(events[0], Error)
    assert events[0].recoverable is False
    assert events[0].kind == "FileNotFoundError"


def test_argv_builder_model_and_session_and_system(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, lines=[
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}},
    ], echo_argv=True)

    plugin = _make_plugin(
        str(cli),
        args=("--permission-mode", "bypassPermissions"),
        model_arg="--model",
        session_arg="--session-id",
        session_mode="always",
        system_prompt_arg="--append-system-prompt",
        input="arg",
    )
    runner = CliRunner(plugin=plugin, workspace_dir=str(tmp_path))

    # Peek at argv by patching _build_argv indirectly: build once and inspect.
    argv = runner._build_argv(
        prompt="hello",
        model_id="claude-sonnet-4-6",
        system_prompt="be helpful",
        image_paths=(),
        resume=False,
    )
    assert argv[0] == str(cli)
    assert "--model" in argv and "claude-sonnet-4-6" in argv
    assert "--session-id" in argv
    assert "--append-system-prompt" in argv and "be helpful" in argv
    assert argv[-1] == "hello"


def test_stdin_mode_feeds_prompt(tmp_path: Path) -> None:
    # Fake CLI that reads stdin and echoes it wrapped in a text block.
    script = tmp_path / "echo_stdin"
    script.write_text(
        "#!/bin/sh\n"
        "INPUT=$(cat)\n"
        'printf \'{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}\n\' "$INPUT"\n'
    )
    script.chmod(0o755)
    plugin = _make_plugin(str(script), input="stdin")
    runner = CliRunner(plugin=plugin, workspace_dir=str(tmp_path))
    events = asyncio.run(_collect(runner, "piped-prompt"))
    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["piped-prompt"]

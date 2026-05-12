"""ClaudeCodeRuntime → CliRunner integration, using a fake ``claude`` CLI.

These tests don't need a real Claude Code install. They replace the
binary path with a shell script that speaks enough stream-json to make
the runtime's full event flow exercisable end-to-end — content
filtering, envelope wrap, streaming callback, usage capture, compact
trigger, and close/teardown.
"""

from __future__ import annotations

import json
import stat
import warnings
from pathlib import Path

import pytest


def _write_fake_claude(tmp_path: Path) -> Path:
    """Fake ``claude`` binary.

    Reads stream-json lines from stdin. For each user message, echoes
    the prompt text back as an ``assistant`` text block, then emits a
    ``result`` with token usage. Recognizes ``/compact`` and emits a
    ``compact_boundary`` event before the result.
    """
    script = tmp_path / "fake_claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        "    content = msg.get('message', {}).get('content', [])\n"
        "    text = ''\n"
        "    for b in content:\n"
        "        if isinstance(b, dict) and b.get('type') == 'text':\n"
        "            text = b.get('text', '')\n"
        "            break\n"
        "    # First turn emits a system event so SessionInfo lands.\n"
        "    print(json.dumps({'type':'system','session_id':'fake-sess',"
        "'model':'claude-sonnet-4-6'}), flush=True)\n"
        "    if text == '/compact':\n"
        "        print(json.dumps({'type':'compact_boundary',"
        "'compact_metadata':{'post_tokens':123}}), flush=True)\n"
        "    print(json.dumps({'type':'assistant','message':{'content':["
        "{'type':'text','text':'reply:'+text}]}}), flush=True)\n"
        "    print(json.dumps({'type':'result','result':'reply:'+text,"
        "'usage':{'input_tokens':10,'output_tokens':5,"
        "'cache_read_input_tokens':2,'cache_creation_input_tokens':1},"
        "'modelUsage':{'claude-sonnet-4-6':{'contextWindow':200000}}}), "
        "flush=True)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from openprogram.providers.anthropic import ClaudeCodeRuntime
    monkeypatch.chdir(tmp_path)
    cli = _write_fake_claude(tmp_path)
    rt = ClaudeCodeRuntime(cli_path=str(cli), timeout=10, max_turns_per_process=100)
    yield rt
    rt.close()


def test_round_trip_text(runtime) -> None:
    reply = runtime._call([{"type": "text", "text": "hello"}], model="claude-sonnet-4-6")
    assert reply == "reply:hello"
    # Usage normalized: raw(10) + cache_read(2) + cache_create(1) = 13.
    assert runtime.last_usage == {
        "input_tokens": 13, "output_tokens": 5,
        "cache_read": 2, "cache_create": 1,
    }
    assert runtime._context_window_tokens == 200000
    assert runtime._resolved_model_id == "claude-sonnet-4-6"


def test_streaming_callback_fires(runtime) -> None:
    events: list[dict] = []
    runtime.on_stream = events.append
    runtime._call([{"type": "text", "text": "stream me"}], model="claude-sonnet-4-6")
    types = [e["type"] for e in events]
    assert "text" in types
    text_ev = next(e for e in events if e["type"] == "text")
    assert text_ev["text"].startswith("reply:stream me")


def test_unsupported_blocks_warn_and_skip(runtime) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reply = runtime._call(
            [
                {"type": "text", "text": "main"},
                {"type": "audio", "path": "x.wav"},
                {"type": "video", "path": "x.mp4"},
                {"type": "file", "path": "x.pdf"},
            ],
            model="claude-sonnet-4-6",
        )
    categories = {type(w.message).__name__ for w in caught}
    assert "UserWarning" in categories
    messages = [str(w.message) for w in caught]
    assert any("audio" in m for m in messages)
    assert any("video" in m for m in messages)
    assert any("file" in m for m in messages)
    assert reply == "reply:main"


def test_compact_triggers_boundary(runtime) -> None:
    # Warm up: let the first turn set context_window_tokens.
    runtime._call([{"type": "text", "text": "warmup"}], model="claude-sonnet-4-6")
    assert runtime._context_window_tokens == 200000
    # Manual compact — sends /compact; fake CLI replies with compact_boundary.
    ran = runtime.compact()
    assert ran is True
    assert runtime._last_context_tokens == 123


def test_response_format_appended_to_prompt(runtime, tmp_path) -> None:
    # With response_format set, the runtime appends a JSON-only instruction
    # to the prompt. Fake CLI echoes the whole prompt back, so we can see it.
    reply = runtime._call(
        [{"type": "text", "text": "give me"}],
        model="claude-sonnet-4-6",
        response_format={"type": "object"},
    )
    assert "Respond with ONLY valid JSON" in reply
    assert reply.startswith("reply:give me")


def test_close_kills_live_proc(runtime) -> None:
    runtime._call([{"type": "text", "text": "hi"}], model="claude-sonnet-4-6")
    proc = runtime._runner._live_proc
    assert proc is not None and proc.returncode is None
    runtime.close()
    # Manually re-poll since close() waits for teardown.
    assert runtime._runner._live_proc is None
    assert proc.returncode is not None

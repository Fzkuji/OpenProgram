"""A turn's log holds its reply and little else.

One 19 s turn with four tool calls once wrote a 445 KB log for a 63 KB reply:
the resolved tool catalog in full (132 KB, every turn) and the whole context
breakdown (26 KB) again after each provider request. Over a long conversation
that was most of the log and of every cold open after the last checkpoint.
The turn here has the measured shape: 81 tools with real-sized descriptions
and schemas, six provider requests that each report their usage.
"""
from __future__ import annotations

import json
from collections import Counter

from openprogram.models.providers.types import Usage
from openprogram.state.store.session import session_log
from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall
from tests.support.transcript.live_server import SESSION_ID, live_chat

TOOLS = 81
REQUESTS = 6
#: Mean bytes of a tool's description and of its schema in the measured log.
DESCRIPTION_BYTES = 615
SCHEMA_BYTES = 545


def _catalog(chat) -> None:
    """Tools that weigh what the measured catalog weighed."""
    for index in range(TOOLS - 2):
        tool = chat.tools.tool(f"tool_{index}")
        tool.description = f"Tool {index}. " + "Describes one thing the agent can do. " * (DESCRIPTION_BYTES // 38)
        tool.parameters = {"type": "object", "properties": {
            f"argument_{n}": {"type": "string", "description": "What this argument selects. " * 3}
            for n in range(SCHEMA_BYTES // 110)
        }}
        chat.tools.extra.append(tool)


def _report_usage(chat, monkeypatch) -> None:
    """Each scripted response reports the prompt it was sent, as a provider does."""
    scripted = chat.model.stream_simple

    async def with_usage(model, context, options=None):
        async for event in scripted(model, context, options):
            message = getattr(event, "message", None)
            if message is not None and getattr(event, "type", "") == "done":
                message.usage = Usage(input=40_000 + 900 * chat.model.calls, output=120,
                                      request_input_estimate=38_000 + 900 * chat.model.calls)
            yield event

    monkeypatch.setattr(chat.model, "stream_simple", with_usage)


def _lines(chat) -> list[tuple[dict, int]]:
    path = chat.sessions._session_dir(SESSION_ID) / session_log.MAIN_LOG
    return [(json.loads(line), len(line)) for line in path.read_bytes().splitlines(keepends=True)]


def _weights(lines) -> tuple[Counter, Counter]:
    """Log bytes by record type, and ``meta.patch`` bytes by the key set."""
    by_type: Counter = Counter()
    by_key: Counter = Counter()
    for record, size in lines:
        by_type[record["type"]] += size
        if record["type"] == "meta.patch":
            for key, value in (record["data"].get("set") or {}).items():
                by_key[key] += len(json.dumps(value))
    return by_type, by_key


def _run_turn(chat, turn: int) -> None:
    for request in range(REQUESTS - 1):
        chat.model.add(ScriptedText(f"step {request}. "),
                       ScriptedToolCall("lookup", {"query": f"q{request}"}, f"call-{turn}-{request}"))
    chat.model.add(ScriptedText("the final answer"))
    chat.send(f"question {turn}")
    chat.wait_finished()


def test_a_turn_adds_little_to_the_log_beyond_its_reply(tmp_path, monkeypatch, record_property):
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.server_runtime import context_stats

    with live_chat(tmp_path) as chat:
        # The real refresh and breakdown, which live_chat stubs for its recordings.
        monkeypatch.setattr(server, "refresh_context_stats", context_stats.refresh_context_stats)
        _catalog(chat)
        _report_usage(chat, monkeypatch)
        _run_turn(chat, 0)
        first = _lines(chat)
        _run_turn(chat, 1)
        chat.wait_published()
        lines = _lines(chat)
        with server._sessions_lock:
            shown = dict(server._sessions[SESSION_ID].get("_last_context_stats") or {})
        reopened = chat.whole_read()[-1]["data"]

    turn = lines[len(first):]
    by_type, by_key = _weights(turn)
    reply = sum(size for record, size in turn if record["type"].startswith("node."))
    journal = sum(size for record, size in turn
                  if record["type"] in ("turn.decision", "turn.tool_started", "turn.tool_result"))
    beyond = sum(by_type.values()) - reply - journal
    record_property("log_bytes_by_type", dict(by_type))
    record_property("meta_bytes_by_key", dict(by_key))
    print("\nturn bytes by type:", dict(by_type.most_common()))
    print("meta.patch bytes by key:", dict(by_key.most_common(6)))
    print(f"reply {reply}, journal {journal}, everything else {beyond}")

    # What the ring showed live is what the log holds for the next open.
    assert shown.get("basis") == "measured" and shown.get("total_used")
    assert isinstance(shown.get("breakdown"), dict)
    saved = chat_meta(lines)["_last_context_stats"]
    assert {key: saved.get(key) for key in ("total_used", "basis", "estimated", "calibration")} == {
        key: shown.get(key) for key in ("total_used", "basis", "estimated", "calibration")}
    assert reopened["context_stats"]["total_used"] == shown["total_used"]

    # The catalog is named, not copied; a request adds a measurement, not a breakdown.
    assert by_type["turn.snapshot"] < 4_000 + 220 * TOOLS
    assert by_key["_last_context_stats"] < 1_500 * REQUESTS
    assert beyond < 40_000


def chat_meta(lines) -> dict:
    return session_log.fold_records(record for record, _size in lines).meta

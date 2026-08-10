"""Composed coverage for automatic Scriptorium writing.

The model response is scripted at the API-provider boundary.  Everything
above it is production code: SessionDB, default model resolution, the
OpenProgram Agent tool loop, managed memory tools, staged installation, node
markers, and the idle-session watcher.
"""

from __future__ import annotations

import atexit
import json
import re
from pathlib import Path

import pytest

from tests.providers._registry_fixture import install_registry
from tests.providers.scripted_provider import (
    ScriptedProvider,
    ScriptedText,
    ScriptedToolCall,
)


PROVIDER_ID = "memory-e2e"
MODEL_ID = "writer-model"
API_ID = "scripted-memory-api"
SESSION_ID = "idle-memory-e2e"
SOURCE_ID = f"openprogram/{SESSION_ID}/u1"


def _close_store(store) -> None:
    store._flush_index()
    atexit.unregister(store._flush_index)


def _configure_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    available_model: bool = True,
):
    from openprogram import paths
    from openprogram.agent import authority
    from openprogram.agent.management import manager
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import set_provider

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    set_provider(None)
    if available_model:
        install_registry(monkeypatch, {
            PROVIDER_ID: {
                "models": [{
                    "id": MODEL_ID,
                    "name": MODEL_ID,
                    "api": API_ID,
                    "base_url": "http://scripted.invalid",
                }],
            },
        })
    else:
        install_registry(monkeypatch, {})
    manager.create(
        "main",
        provider=PROVIDER_ID,
        model_id=MODEL_ID,
        make_default=True,
    )
    db = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


def _append_paired_turn(db) -> float:
    from openprogram.agent import authority

    paired = authority.paired_channel_authority(
        "telegram", "main", "telegram-user-42", "B",
    )
    db.append_message(SESSION_ID, {
        "id": "u1",
        "role": "user",
        "content": "I prefer exact answers.",
        **paired,
    })
    return db.list_sessions(limit=10)[0]["updated_at"]


def _prompt_text(call) -> str:
    return "\n".join(
        block.text
        for message in call.context.messages
        for block in getattr(message, "content", [])
        if hasattr(block, "text")
    )


def test_idle_writer_composes_real_runtime_and_installs_topic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Removing any production handoff leaves no installed, marked result."""
    from openprogram.memory import get_provider, session_watcher, store
    from openprogram.memory.scriptorium.provider import ScriptoriumMemoryProvider
    from openprogram.providers import api_registry

    db = _configure_runtime(tmp_path, monkeypatch)
    scripted = ScriptedProvider()
    scripted.add_response(ScriptedToolCall(
        "Write",
        {
            "file_path": "topics/core.md",
            "content": (
                "# Preferences\n\n"
                "B prefers exact answers.[^e1]\n\n"
                f"[^e1]: Time: `undated`; Sources: {SOURCE_ID}\n"
            ),
        },
        "write-topic",
    ))
    scripted.add_response(ScriptedText("written"))
    scripted.add_response(ScriptedText("already organized"))
    monkeypatch.setitem(api_registry._registry, API_ID, scripted)

    try:
        updated_at = _append_paired_turn(db)
        provider = get_provider()
        assert type(provider) is ScriptoriumMemoryProvider

        assert session_watcher._scan(idle_minutes=0) == 1

        memory = store.root()
        topic = (memory / "topics/core.md").read_text(encoding="utf-8")
        assert "B prefers exact answers." in topic
        assert SOURCE_ID in topic
        assert "[^e1]" not in topic
        block = re.search(r"(?m) \^([0-9a-f]{8})$", topic)
        assert block is not None
        assert re.search(r"\[\^e-[0-9a-f]{10}\]", topic)

        source = (
            memory / f"sources/openprogram/_v2/{SESSION_ID}.md"
        ).read_text(encoding="utf-8")
        assert source.startswith("<!-- openprogram-source-archive:v2 -->\n\n")
        assert f"<!-- source-id:{SOURCE_ID} -->" in source
        assert "<!-- speaker-id:telegram%2Duser%2D42 -->" in source
        assert "] B (telegram-user-42): I prefer exact answers." in source

        recent = [
            json.loads(line)
            for line in (memory / "recent_events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assert recent == [{
            "memory_id": block.group(1),
            "when": None,
            "whens": [],
            "content": "B prefers exact answers.",
            "refs": [SOURCE_ID],
            "source_refs": [SOURCE_ID],
            "topic_path": "topics/core.md",
            "headings": ["Preferences"],
            "created_order": 0,
        }]
        relations = json.loads(
            (memory / "relations.json").read_text(encoding="utf-8")
        )
        assert relations == {
            "backlinks": {}, "outbound": {block.group(1): []},
        }
        assert "B prefers exact answers." in (
            memory / "core.md"
        ).read_text(encoding="utf-8")

        branch = db.get_branch(SESSION_ID)
        assert branch[0]["memory_written_scriptorium"] == store.workspace_id()
        assert session_watcher._load_processed() == {
            SESSION_ID: updated_at,
        }

        first_call = scripted.calls[0]
        assert (
            first_call.model.provider,
            first_call.model.id,
            first_call.model.api,
        ) == (PROVIDER_ID, MODEL_ID, API_ID)
        assert {tool.name for tool in first_call.context.tools} >= {
            "Read", "Write", "Edit", "Grep", "Glob", "shell",
        }
        prompt = _prompt_text(first_call)
        assert f'"ref":"{SOURCE_ID}"' in prompt
        assert '"speaker":"B (telegram-user-42)"' in prompt
    finally:
        from openprogram.memory import set_provider

        set_provider(None)
        _close_store(db)


class _RawCredentialFailure:
    def __init__(self) -> None:
        self.call_count = 0

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        self.call_count += 1
        raise ValueError("No API key configured for provider 'memory-e2e'")
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.parametrize(
    ("failure", "reason_fragment"),
    [
        ("model-resolution", "is not available"),
        ("lazy-credential", "No API key configured"),
    ],
)
def test_permanent_writer_startup_failure_is_not_offered_next_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason_fragment: str,
):
    """Raw config/auth failures are durable non-retry verdicts."""
    from openprogram.memory import get_provider, session_watcher, store
    from openprogram.memory.scriptorium.provider import ScriptoriumMemoryProvider
    from openprogram.providers import api_registry

    db = _configure_runtime(
        tmp_path,
        monkeypatch,
        available_model=failure != "model-resolution",
    )
    raw_failure = _RawCredentialFailure()
    if failure == "lazy-credential":
        monkeypatch.setitem(api_registry._registry, API_ID, raw_failure)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "openprogram.events.emit_safe",
        lambda name, actor, payload=None, meta=None: events.append(
            (name, payload or {})
        ),
    )

    try:
        updated_at = _append_paired_turn(db)
        assert type(get_provider()) is ScriptoriumMemoryProvider

        assert session_watcher._scan(idle_minutes=0) == 0
        processed = session_watcher._load_processed()
        assert processed == {SESSION_ID: updated_at}
        ended = [
            payload for name, payload in events
            if name == "memory.ingest_ended"
        ]
        assert len(ended) == 1
        assert ended[0]["ok"] is False
        assert ended[0]["retryable"] is False
        assert reason_fragment in ended[0]["reason"]

        source_path = (
            store.root() / f"sources/openprogram/_v2/{SESSION_ID}.md"
        )
        archived_once = source_path.read_bytes()
        assert db.get_branch(SESSION_ID)[0].get(
            "memory_written_scriptorium"
        ) is None

        assert session_watcher._scan(idle_minutes=0) == 0
        assert session_watcher._load_processed() == processed
        assert source_path.read_bytes() == archived_once
        assert len([
            name for name, _payload in events
            if name == "memory.ingest_ended"
        ]) == 1
        assert raw_failure.call_count == (
            1 if failure == "lazy-credential" else 0
        )
    finally:
        from openprogram.memory import set_provider

        set_provider(None)
        _close_store(db)

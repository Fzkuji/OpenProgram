"""Memory web routes — path containment, transactional saves, stage cleanup.

The topic editor writes memory from the browser, so these routes are the one
place a hand edit reaches the workspace without going through the structured
transaction. Three things are checked here: a request cannot name a file
outside the directory it addresses, a rejected edit leaves the committed file
byte-for-byte alone, and neither outcome leaves a staged copy of memory behind
in the temp directory.

The fixtures build a minimal but *valid* workspace — one topic paragraph
carrying a block ID and a footnote that resolves to an archived source —
because anything less is refused by the parser before the routes are reached.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SOURCE = '# Conversation 1\n\n<a id="d1-1"></a>\n\nuser: remember this\n'
NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
# A second topic whose paragraph links into NOTE's block.
LINKING = (
    "# Other\n"
    "\n"
    "See [the note](note.md#^abc12345).[^e-2f4c7a2b91] ^def45678\n"
    "\n"
    "[^e-2f4c7a2b91]: Time: `2026-01-02`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)


def _stage_dirs() -> set[str]:
    """The workspace staging trees currently sitting in the temp directory."""
    return set(glob.glob(
        os.path.join(tempfile.gettempdir(), "scriptorium-topics-*")
    ))


@pytest.fixture
def memory(tmp_path, monkeypatch):
    """A memory workspace holding one valid topic. Returns its root."""
    import openprogram.paths as paths

    test_temp = tmp_path / "temp"
    test_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(test_temp))
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)

    from openprogram.memory import store
    root = store.ensure()
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "D1.md").write_text(SOURCE, encoding="utf-8")
    (root / "topics" / "note.md").write_text(NOTE, encoding="utf-8")
    return root


@pytest.fixture
def client(memory):
    from openprogram.webui.routes import memory as routes
    app = FastAPI()
    routes.register(app)
    return TestClient(app)


# ---- path containment -------------------------------------------------


def test_within_rejects_paths_that_leave_the_root(tmp_path):
    from openprogram.webui.routes.memory import _within

    root = tmp_path / "topics"
    root.mkdir()
    (tmp_path / "topics-private").mkdir()

    assert _within(root, "note.md") == (root / "note.md").resolve()
    assert _within(root, "people/alice.md") == (root / "people/alice.md").resolve()
    # A sibling whose name merely starts with the root's name is outside it.
    assert _within(root, "../topics-private/secret.md") is None
    assert _within(root, "sub/../../topics-private/secret.md") is None
    assert _within(root, "..") is None
    assert _within(root, "/etc/passwd") is None


@pytest.mark.parametrize("path", [
    # ``..`` percent-encoded, which is how it survives the HTTP client and
    # the router to reach the route as one path parameter.
    "..%2Ftopics-private%2Fsecret.md",
    "sub%2F..%2F..%2Ftopics-private%2Fsecret.md",
    "..%2F..%2Fescape.md",
])
def test_put_refuses_to_write_outside_topics(client, memory, path):
    r = client.put(f"/api/memory/topics/{path}", json={"content": "# Pwned\n"})
    assert r.status_code == 403, r.text
    assert not (memory / "topics-private").exists()
    assert not (memory / "escape.md").exists()
    assert not (memory.parent / "escape.md").exists()


def test_delete_refuses_to_reach_outside_topics(client, memory):
    private = memory / "topics-private"
    private.mkdir()
    (private / "secret.md").write_text("# Secret\n", encoding="utf-8")

    r = client.delete("/api/memory/topics/..%2Ftopics-private%2Fsecret.md")
    assert r.status_code == 403, r.text
    assert (private / "secret.md").is_file()


def test_timeline_refuses_a_date_that_is_really_a_path(client):
    assert client.get("/api/memory/timeline/%2E%2E").status_code == 403
    assert client.get("/api/memory/timeline/not-a-date").status_code == 403
    assert client.get("/api/memory/timeline/2026-01-02").status_code == 200


def test_status_route_returns_the_inspect_status_contract(
    client, monkeypatch,
):
    from openprogram.memory.retrieval import inspect

    expected = {
        "workspace": "/memory",
        "revision": "abc",
        "writer": {
            "last_success_at": None,
            "last_failure": {
                "at": "2026-08-10T12:00:00+00:00",
                "reason_code": "MODEL_PROVIDER_UNAVAILABLE",
                "retryable": True,
            },
            "pending_turns": 3,
        },
    }
    seen = {}

    def _status(_root, *, include_path=False):
        seen["include_path"] = include_path
        return expected

    monkeypatch.setattr(inspect, "status", _status)

    response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json() == expected
    # The web UI is the owner's own surface, so it asks for the path the
    # model-facing tool must not be given.
    assert seen["include_path"] is True


def test_memory_refs_expose_stable_block_identity(client):
    response = client.get("/api/memory/refs?q=worth")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["memory_id"] == "abc12345"
    assert rows[0]["topic_path"] == "note.md"
    assert rows[0]["workspace_id"].startswith("w-")


def test_ensure_initializes_git_and_snapshots_existing_memory(
    tmp_path, monkeypatch,
):
    state = tmp_path / "state"
    root = state / "memory"
    topic = root / "topics/note.md"
    topic.parent.mkdir(parents=True)
    topic.write_text("# Existing\n", encoding="utf-8")
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    from openprogram.memory import store

    assert store.ensure() == root
    assert (root / ".git").is_dir()
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=root, text=True
    ) == ""
    saved = subprocess.check_output(
        ["git", "show", "HEAD:topics/note.md"], cwd=root, text=True
    )
    assert saved == "# Existing\n"


def test_ensure_migrates_marker_workspace_missing_the_current_layout(
    tmp_path, monkeypatch,
):
    state = tmp_path / "state"
    root = state / "memory"
    legacy = root / "wiki/Old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old memory\n", encoding="utf-8")
    marker = root / ".git/openprogram-memory-ready"
    marker.parent.mkdir()
    marker.write_text("ready\n", encoding="utf-8")
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)

    from openprogram.memory import store

    assert store.ensure() == root
    assert (root / "topics").is_dir()
    assert (root / "sources").is_dir()
    assert not (root / "wiki").exists()
    assert (state / "memory-superseded/wiki/Old.md").read_text(
        encoding="utf-8",
    ) == "old memory\n"


def test_structured_delete_is_recorded_in_git_history(tmp_path):
    from openprogram.memory.management import MemoryWorkspace

    root = tmp_path / "memory"
    (root / "sources").mkdir(parents=True)
    (root / "topics").mkdir()
    (root / "sources/D1.md").write_text(SOURCE, encoding="utf-8")
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    workspace = MemoryWorkspace(root)
    try:
        result = workspace.update(
            base_revision=workspace.revision(),
            changes=[{"path": "topics/note.md", "action": "delete"}],
            commit_message="Remove obsolete memory",
            git_commit="on",
        )
    finally:
        workspace.close()

    assert result.git_committed is True
    assert result.git_commit
    assert not (root / "topics/note.md").exists()
    previous = subprocess.check_output(
        ["git", "show", "HEAD^:topics/note.md"], cwd=root, text=True
    )
    assert "A fact worth keeping" in previous


def test_structured_changes_api_uses_the_workspace_transaction(client, memory):
    from openprogram.memory.management.transaction import workspace_revision

    response = client.post("/api/memory/changes", json={
        "base_revision": workspace_revision(memory),
        "changes": [{
            "path": "topics/new.md",
            "action": "write",
            "content": (
                "# New\n\n"
                "A new fact.[^e1]\n\n"
                "[^e1]: Time: `2026-08-15`; Sources: new-source-api\n"
            ),
        }],
        "sources": [{
            "label": "new-source-api",
            "role": "user",
            "content": "A new fact.",
            "observed_at": "2026-08-15",
        }],
        "commit_message": "Add API memory",
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source_ids"]["new-source-api"].startswith("claude-code/")
    written = (memory / "topics/new.md").read_text(encoding="utf-8")
    assert "new-source-api" not in written
    assert payload["source_ids"]["new-source-api"] in written


def test_record_changes_api_creates_a_memory_and_derived_views(client, memory):
    from openprogram.memory.management.transaction import workspace_revision
    from openprogram.memory.markdown import parse_topic_tree

    response = client.post("/api/memory/changes", json={
        "base_revision": workspace_revision(memory),
        "memory_changes": [{
            "op": "create_record",
            "content": "Created through the record API.",
            "time": "2026-08-15",
            "source_refs": ["new-source-record"],
            "destination": {
                "topic_path": "topics/from-api.md",
                "headings": ["API"],
                "position": "end",
            },
        }],
        "sources": [{
            "label": "new-source-record",
            "role": "user",
            "content": "Created through the record API.",
            "observed_at": "2026-08-15",
        }],
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["block_ids"]
    created = [
        unit for unit in parse_topic_tree(memory / "topics")
        if unit.topic_path == "from-api.md"
    ]
    assert len(created) == 1
    assert created[0].source_refs == (
        payload["source_ids"]["new-source-record"],
    )
    assert (memory / "timeline/2026/08/15.md").is_file()
    assert (memory / "recent_events.jsonl").is_file()
    assert (memory / "relations.json").is_file()


def test_topic_edit_uses_configured_recent_view_limit(
    client, memory, monkeypatch,
):
    from openprogram.memory.management.config import MemoryConfig

    (memory / "topics/other.md").write_text(LINKING, encoding="utf-8")
    monkeypatch.setattr(
        "openprogram.memory.management.config.load_memory_config",
        lambda: MemoryConfig(recent_limit=1),
    )

    response = client.put(
        "/api/memory/topics/note.md",
        json={"content": NOTE.replace("worth keeping", "worth retaining")},
    )

    assert response.status_code == 200, response.text
    rows = [
        json.loads(line)
        for line in (memory / "recent_events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1


def test_memory_update_schema_constructs_a_google_function_declaration():
    from google.genai import types as gtypes

    from openprogram.programs.functions.memory.memory import UPDATE_SPEC
    from openprogram.providers._schema import normalize

    parameters = normalize(UPDATE_SPEC["parameters"], "gemini_openapi")
    declaration = gtypes.FunctionDeclaration(
        name="memory_update",
        description="Update memory",
        parameters=parameters,
    )

    assert declaration.name == "memory_update"
    item = UPDATE_SPEC["parameters"]["properties"]["memory_changes"]["items"]
    assert item["properties"]["op"]["enum"] == [
        "create_record",
        "update_record",
        "delete_record",
        "move_records",
    ]
    assert item["properties"]["destination"]["properties"]["position"][
        "enum"
    ] == ["start", "end", "before", "after"]


def test_structured_changes_api_reports_stale_revision(client, memory):
    response = client.post("/api/memory/changes", json={
        "base_revision": "stale",
        "changes": [{"path": "topics/note.md", "action": "delete"}],
    })

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_UPDATE"
    assert (memory / "topics/note.md").is_file()


def test_memory_changes_api_accepts_the_compatible_unified_diff(client, memory):
    from openprogram.memory.management.transaction import workspace_revision

    original = (memory / "topics/note.md").read_text(encoding="utf-8")
    updated = original.replace("A fact", "A corrected fact")
    response = client.post("/api/memory/changes", json={
        "base_revision": workspace_revision(memory),
        "patch": (
            "--- a/topics/note.md\n"
            "+++ b/topics/note.md\n"
            "@@ -1,5 +1,5 @@\n"
            + "".join(
                ("-" if old != new else " ") + old
                + (("+" + new) if old != new else "")
                for old, new in zip(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                )
            )
        ),
    })

    assert response.status_code == 200, response.text
    assert "A corrected fact" in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")


def test_structured_changes_api_requires_base_revision(client, memory):
    response = client.post("/api/memory/changes", json={
        "changes": [{"path": "topics/note.md", "action": "delete"}],
    })

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert (memory / "topics/note.md").is_file()


def test_structured_changes_api_rejects_non_string_commit_message(
    client, memory,
):
    from openprogram.memory.management.transaction import workspace_revision

    response = client.post("/api/memory/changes", json={
        "base_revision": workspace_revision(memory),
        "changes": [{"path": "topics/note.md", "action": "delete"}],
        "commit_message": 1,
    })

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert (memory / "topics/note.md").is_file()


# ---- a save either lands whole or not at all --------------------------


def test_put_dropping_a_block_id_is_refused_and_changes_nothing(client, memory):
    r = client.put("/api/memory/topics/note.md", json={"content": "# Note\n"})
    assert r.status_code == 400, r.text
    assert "abc12345" in r.json()["error"]
    assert (memory / "topics/note.md").read_text(encoding="utf-8") == NOTE


def test_put_keeping_the_block_id_lands(client, memory):
    edited = NOTE.replace("worth keeping", "worth remembering")
    r = client.put("/api/memory/topics/note.md", json={"content": edited})
    assert r.status_code == 200, r.text
    assert "worth remembering" in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert subprocess.check_output(
        ["git", "log", "-1", "--format=%s"], cwd=memory, text=True
    ).strip() == "memory: edit topics/note.md"


CORE = (
    "# Core\n"
    "\n"
    "Always on.[^e-3f4c7a2b92] ^bcd23456\n"
    "\n"
    "[^e-3f4c7a2b92]: Time: `2026-01-03`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)


def test_core_save_lands_on_the_master_and_is_rendered(client, memory):
    """The editor edits ``topics/core.md``; the root file is the render.

    Editing the render instead would hand the browser a copy cut to the
    token budget and save that truncated text back as the whole thing.
    """
    r = client.put("/api/memory/core", json={"content": CORE})
    assert r.status_code == 200, r.text
    assert "Always on." in (
        memory / "topics" / "core.md"
    ).read_text(encoding="utf-8")
    assert "Always on." in (memory / "core.md").read_text(encoding="utf-8")

    read_back = client.get("/api/memory/core")
    payload = read_back.json()
    assert payload["content"] == (
        memory / "topics" / "core.md"
    ).read_text(encoding="utf-8")
    assert payload["size"] == (memory / "topics/core.md").stat().st_size
    assert payload["mtime"] == (memory / "topics/core.md").stat().st_mtime
    assert payload["rendered_content"] == (
        memory / "core.md"
    ).read_text(encoding="utf-8")
    assert payload["rendered_size"] == (memory / "core.md").stat().st_size
    assert payload["rendered_mtime"] == (memory / "core.md").stat().st_mtime
    assert 0 < payload["rendered_tokens"] <= payload["budget_tokens"]
    assert payload["budget_tokens"] == 2_000


def test_core_route_distinguishes_rendered_from_disabled_injection(
    client, memory, monkeypatch,
):
    from openprogram.memory.management.config import MemoryConfig

    assert client.put("/api/memory/core", json={"content": CORE}).status_code == 200
    monkeypatch.setattr(
        "openprogram.memory.management.config.load_memory_config",
        lambda: MemoryConfig(core_inject=False),
    )

    payload = client.get("/api/memory/core").json()

    assert payload["rendered_content"]
    assert payload["rendered_tokens"] > 0
    assert payload["injection_enabled"] is False
    assert payload["injected_content"] == ""
    assert payload["injected_tokens"] == 0


def test_core_route_counts_literal_tiktoken_special_text(client, memory):
    content = CORE.replace("Always on.", "Keep literal <|endoftext|> text.")
    (memory / "topics/core.md").write_text(content, encoding="utf-8")
    (memory / "core.md").write_text(content, encoding="utf-8")

    response = client.get("/api/memory/core")

    assert response.status_code == 200, response.text
    assert response.json()["rendered_tokens"] > 0


def test_empty_core_keeps_legacy_and_effective_fields(client):
    payload = client.get("/api/memory/core").json()

    assert payload["content"] == ""
    assert payload["size"] == 0
    assert payload["mtime"] == 0
    assert payload["rendered_content"] == ""
    assert payload["injected_content"] == ""


def test_core_get_waits_for_a_complete_install_snapshot(
    client, memory, monkeypatch,
):
    from openprogram.memory.management import block_views
    from openprogram.memory.management.transaction import staged_edit

    assert client.put("/api/memory/core", json={"content": CORE}).status_code == 200
    updated = (memory / "topics/core.md").read_text(
        encoding="utf-8",
    ).replace("Always on.", "Always current.")
    topics_moved = threading.Event()
    release_install = threading.Event()
    original_replace = block_views.os.replace

    def paused_replace(source, destination):
        result = original_replace(source, destination)
        if Path(source) == memory / "topics":
            topics_moved.set()
            assert release_install.wait(timeout=5)
        return result

    monkeypatch.setattr(block_views.os, "replace", paused_replace)
    responses = {}

    def write_core(stage):
        (stage / "topics/core.md").write_text(updated, encoding="utf-8")

    writer = threading.Thread(target=lambda: responses.setdefault(
        "put", staged_edit(
            memory,
            write_core,
            commit_message="memory: edit topics/core.md",
        ),
    ))
    writer.start()
    if not topics_moved.wait(timeout=5):
        writer.join(timeout=1)
        pytest.fail(f"install never moved topics: {responses!r}")

    reader = threading.Thread(target=lambda: responses.setdefault(
        "get", client.get("/api/memory/core"),
    ))
    reader.start()
    time.sleep(0.1)
    assert reader.is_alive(), "GET returned during the incomplete install window"

    release_install.set()
    writer.join(timeout=5)
    reader.join(timeout=5)
    assert not writer.is_alive()
    assert not reader.is_alive()
    assert responses["put"] == (True, "")
    assert responses["get"].status_code == 200
    payload = responses["get"].json()
    assert "Always current." in payload["content"]
    assert "Always current." in payload["rendered_content"]


def test_delete_is_refused_while_another_topic_links_into_it(client, memory):
    (memory / "topics/other.md").write_text(LINKING, encoding="utf-8")

    r = client.delete("/api/memory/topics/note.md")
    assert r.status_code == 400, r.text
    assert "abc12345" in r.json()["error"]
    assert (memory / "topics/note.md").is_file()


def test_delete_removes_a_topic_nothing_points_at(client, memory):
    r = client.delete("/api/memory/topics/note.md")
    assert r.status_code == 200, r.text
    assert not (memory / "topics/note.md").exists()


def test_save_gives_up_while_the_workspace_lock_is_held(
    client, memory, monkeypatch
):
    from openprogram.memory.management.transaction import (
        workspace_write_lock,
    )
    from openprogram.webui.routes import memory as routes

    monkeypatch.setattr(routes, "WRITE_LOCK_TIMEOUT_S", 0.2)
    edited = NOTE.replace("worth keeping", "worth remembering")

    with workspace_write_lock(memory):
        r = client.put("/api/memory/topics/note.md", json={"content": edited})
    assert r.status_code == 400, r.text
    assert "lock" in r.json()["error"]
    assert (memory / "topics/note.md").read_text(encoding="utf-8") == NOTE

    # Released again, the same edit goes through.
    r = client.put("/api/memory/topics/note.md", json={"content": edited})
    assert r.status_code == 200, r.text


# ---- staging leaves nothing behind ------------------------------------


def test_stage_directories_are_cleaned_up_on_both_paths(client):
    before = _stage_dirs()

    rejected = client.put("/api/memory/topics/note.md", json={"content": "# Note\n"})
    assert rejected.status_code == 400, rejected.text
    accepted = client.put(
        "/api/memory/topics/note.md",
        json={"content": NOTE.replace("worth keeping", "worth remembering")},
    )
    assert accepted.status_code == 200, accepted.text

    assert _stage_dirs() == before


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/memory/status", None),
        ("GET", "/api/memory/topics", None),
        ("GET", "/api/memory/topics/example.md", None),
        ("PUT", "/api/memory/topics/example.md", {"content": "# Example\n"}),
        ("DELETE", "/api/memory/topics/example.md", None),
        (
            "POST",
            "/api/memory/changes",
            {"base_revision": "x", "changes": []},
        ),
        ("GET", "/api/memory/timeline", None),
        ("GET", "/api/memory/timeline/2026-01-01", None),
        ("GET", "/api/memory/recent", None),
        ("GET", "/api/memory/core", None),
        ("PUT", "/api/memory/core", {"content": "# Core\n"}),
    ],
)
def test_backend_none_rejects_every_web_memory_route(
    monkeypatch, tmp_path, method, path, body
):
    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"memory": {"backend": "none"}},
    )

    from openprogram.webui.routes import memory as routes

    app = FastAPI()
    routes.register(app)
    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "MEMORY_DISABLED",
            "message": "memory is disabled by memory.backend=none",
        }
    }
    assert not (tmp_path / "state" / "memory").exists()

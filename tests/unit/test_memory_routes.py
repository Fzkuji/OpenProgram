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
import os
import tempfile

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
    from openprogram.memory.scriptorium.retrieval import inspect

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
    monkeypatch.setattr(inspect, "status", lambda _root: expected)

    response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json() == expected


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
    assert read_back.json()["content"] == (
        memory / "topics" / "core.md"
    ).read_text(encoding="utf-8")


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
    from openprogram.memory.scriptorium.management.transaction import (
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

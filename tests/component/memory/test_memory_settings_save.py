"""Saving a Topic from Settings: what a person may cite, and what they keep.

Every turn this build writes carries the memory policy it ran under. A
person editing memory in Settings runs no execution, yet is the owner
editing the shared store: a turn that wrote to that store is theirs to
cite, while a conversation that ran with memory off, or one an Agent keeps
for its own space, stays out. A person may also write a paragraph that
cites nothing, and a Topic they leave with headings only stays a file.
"""
from __future__ import annotations

import tempfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

AGENT_CREATED = 1_777_000_000.0
NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
)


def _stamp(*, agent="main", mode="read_write", write_space="legacy_global",
           eligible=True):
    """The ``memory_policy`` an Agent's turn is stored with."""
    return {
        "version": 1, "agent_id": agent, "mode": mode,
        "read_spaces": [write_space], "write_space": write_space,
        "required": False, "epoch": 1, "created_at": AGENT_CREATED,
        "eligible": eligible,
    }


@pytest.fixture
def memory(tmp_path, monkeypatch, chats):
    import openprogram.state.paths as paths
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.permissions import authority

    test_temp = tmp_path / "temp"
    test_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(test_temp))
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()
    # The main Agent as a fresh install saves it (shared memory, epoch 1),
    # and a keeper Agent that writes only its own space.
    def space(name):
        return {"mode": "read_write", "read_spaces": [name], "write_space": name}

    agents = {
        "main": SimpleNamespace(memory=space("legacy_global"),
                                memory_policy_epoch=1, created_at=AGENT_CREATED),
        "keeper": SimpleNamespace(memory=space("self"),
                                  memory_policy_epoch=1, created_at=AGENT_CREATED),
    }
    monkeypatch.setattr(manager, "get", agents.get)
    owner = authority.local_owner_authority()
    chats.say("s1", "d1-1", "remember this")
    chats.say("s2", "m1", "I applied to Norizon", memory_policy=_stamp(), **owner)
    chats.say("off", "m1", "private", memory_policy=_stamp(
        mode="off", eligible=False), **owner)
    chats.say("own", "m1", "agent's own", memory_policy=_stamp(
        agent="keeper", write_space="self"), **owner)

    from openprogram.state.memory import store

    root = store.ensure()
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    return root


@pytest.fixture
def client(memory):
    from openprogram.interfaces.server.routes.settings import memory as routes

    app = FastAPI()
    routes.register(app)
    return TestClient(app)


def _cited(source: str) -> str:
    return NOTE + (
        "\nApplied to Norizon.[^e1]\n"
        f"\n[^e1]: Time: `2026-11-06`; Sources: [said]({source})\n"
    )


def test_a_settings_save_cites_the_owners_stamped_conversation(client, memory):
    response = client.put("/api/memory/topics/note.md", json={
        "content": _cited("/s/s2#m1"), "base_content": NOTE, "autosave": True,
    })

    assert response.status_code == 200, response.text
    assert "[said](/s/s2#m1)" in (memory / "topics/note.md").read_text()


@pytest.mark.parametrize("session, reason", [
    ("off", "memory off or read-only"),
    ("own", "an Agent's own memory space"),
])
def test_a_settings_save_still_refuses_a_conversation_kept_from_memory(
    client, memory, session, reason,
):
    response = client.put("/api/memory/topics/note.md", json={
        "content": _cited(f"/s/{session}#m1"), "base_content": NOTE,
        "autosave": True,
    })

    assert response.status_code == 400
    error = response.json()["error"]
    assert "not a message memory may cite" in error
    assert reason in error
    assert "cite a message a person or the assistant wrote" in error
    assert (memory / "topics/note.md").read_text() == NOTE


def test_an_outside_file_saved_in_settings_becomes_memorys_own(
    client, memory,
):
    hand = "# Norizon\n\n## Status\n\n- Applied; no reply yet.\n"
    (memory / "topics/career").mkdir()
    (memory / "topics/career/norizon.md").write_text(hand, encoding="utf-8")
    fixed = (
        "# Norizon\n\n## Status\n\nApplied; no reply yet.[^e1]\n"
        "\n[^e1]: Time: `2026-11-06`; Sources: [said](/s/s2#m1)\n"
    )

    response = client.put("/api/memory/topics/career/norizon.md", json={
        "content": fixed, "base_content": hand, "autosave": True,
    })

    assert response.status_code == 200, response.text
    assert "warning" not in response.json()
    opened = client.get("/api/memory/topics/career/norizon.md").json()
    assert "warning" not in opened
    assert opened["origin"] == "runtime"
    assert [row["origin"] for row in opened["paragraphs"]] == ["cited"]


def test_a_paragraph_that_cites_nothing_is_saved(client, memory):
    content = NOTE + "\nI prefer green tea in the morning.\n"

    response = client.put("/api/memory/topics/note.md", json={
        "content": content, "base_content": NOTE, "autosave": True,
    })

    assert response.status_code == 200, response.text
    saved = (memory / "topics/note.md").read_text()
    assert "I prefer green tea in the morning. ^" in saved
    opened = client.get("/api/memory/topics/note.md").json()
    assert [row["origin"] for row in opened["paragraphs"]] == ["cited", "person"]


def test_the_record_api_cites_the_owners_stamped_conversation(client, memory):
    response = client.post("/api/memory/changes", json={"memory_changes": [{
        "op": "create_record", "content": "Applied to Norizon.",
        "time": "2026-11-06",
        "sources": [{"source": "/s/s2#m1", "label": "said"}],
        "destination": {
            "topic_path": "topics/jobs.md", "headings": ["Jobs"],
            "position": "end",
        },
    }]})

    assert response.status_code == 200, response.text
    assert "/s/s2#m1" in (memory / "topics/jobs.md").read_text()


def test_outside_a_persons_edit_a_stamped_turn_needs_its_own_execution(memory):
    from openprogram.runtime.context import session_db
    from openprogram.state.memory import chat_refs

    message = session_db.default_db().get_messages("s2")[0]
    # A background pass with no scope writes legacy turns only; the stamped
    # ones are written by their own execution's pass.
    assert not chat_refs.citable(message)


def test_a_topic_saved_with_its_title_only_is_kept(client, memory):
    title = "# Note\n"

    response = client.put("/api/memory/topics/note.md", json={
        "content": title, "base_content": NOTE, "autosave": True,
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert "removed" not in body and "warning" not in body
    assert body["content"] == title
    assert (memory / "topics/note.md").read_text() == title

    # Typing on from the title: the save after it is not a conflict.
    response = client.put("/api/memory/topics/note.md", json={
        "content": NOTE, "base_content": title, "autosave": True,
    })
    assert response.status_code == 200, response.text
    assert "A fact worth keeping." in (memory / "topics/note.md").read_text()


def test_a_new_title_only_topic_is_kept(client, memory):
    response = client.put("/api/memory/topics/draft.md", json={
        "content": "# Draft\n", "autosave": True,
    })

    assert response.status_code == 200, response.text
    assert "removed" not in response.json()
    assert (memory / "topics/draft.md").read_text() == "# Draft\n"


def test_a_missing_topic_still_conflicts_with_a_base_that_had_paragraphs(
    client, memory,
):
    (memory / "topics/note.md").unlink()

    response = client.put("/api/memory/topics/note.md", json={
        "content": NOTE, "base_content": NOTE, "autosave": True,
    })

    assert response.status_code == 409


OUTSIDE_CITED = (
    "# Norizon\n\n## Status\n\nApplied; no reply yet.[^e1]\n"
    "\n[^e1]: Time: `2026-11-06`; Sources: [said]({source})\n"
)


def test_an_outside_file_opens_marked_as_written_outside(client, memory):
    (memory / "topics/career").mkdir()
    (memory / "topics/career/norizon.md").write_text(
        "# Norizon\n\n- Applied; no reply yet.\n", encoding="utf-8",
    )

    opened = client.get("/api/memory/topics/career/norizon.md").json()

    assert "warning" not in opened
    assert opened["origin"] == "outside"
    assert [row["origin"] for row in opened["paragraphs"]] == ["outside"]


def test_an_outside_file_saved_with_a_refused_citation_is_refused(
    client, memory,
):
    hand = "# Norizon\n\n## Status\n\n- Applied; no reply yet.\n"
    (memory / "topics/career").mkdir()
    path = memory / "topics/career/norizon.md"
    path.write_text(hand, encoding="utf-8")

    response = client.put("/api/memory/topics/career/norizon.md", json={
        "content": OUTSIDE_CITED.format(source="/s/own#m1"),
        "base_content": hand, "autosave": True,
    })

    # A citation a person adds is checked like any other.
    assert response.status_code == 400
    assert "an Agent's own memory space" in response.json()["error"]
    assert path.read_text(encoding="utf-8") == hand

    response = client.put("/api/memory/topics/career/norizon.md", json={
        "content": OUTSIDE_CITED.format(source="/s/s2#m1"),
        "base_content": hand, "autosave": True,
    })
    assert response.status_code == 200, response.text
    assert "warning" not in response.json()

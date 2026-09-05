"""Every ``MemoryWorkspace`` closes its staging tree, on both outcomes.

Building one copies the whole workspace into a fresh temp directory. Nothing
removes that copy when the object is dropped, so a caller that opens a
workspace per write and never closes it leaves one staged copy of memory
behind per call — invisible until the temp directory is full.

The callers checked here are the two that open a workspace outside the web
routes and the CLI: the ``memory_update`` tool and the background runtime.
"""
from __future__ import annotations

import glob
import os
import tempfile

import pytest

SOURCE = '# Conversation 1\n\n<a id="d1-1"></a>\n\nuser: remember this\n'
NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
)
PATCH = (
    "--- a/topics/note.md\n"
    "+++ b/topics/note.md\n"
    "@@ -3,1 +3,1 @@\n"
    "-A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "+A fact worth remembering.[^e-1f4c7a2b90] ^abc12345\n"
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
    stage_root = tmp_path / "temp"
    stage_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(stage_root))
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)

    from openprogram.memory import store
    root = store.ensure()
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "D1.md").write_text(SOURCE, encoding="utf-8")
    (root / "topics" / "note.md").write_text(NOTE, encoding="utf-8")
    return root


# ---- the memory_update tool -------------------------------------------


def test_memory_update_cleans_up_on_both_paths(memory, monkeypatch):
    import json

    from openprogram.programs.tools.knowledge.memory import memory as memory_tools

    monkeypatch.setattr(memory_tools, "authority_from_message", lambda *_: {
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "principal_id": "owner/install/0123456789abcdef",
        "authority_tier": "owner",
        "interaction": "interactive",
    })
    memory_status = memory_tools.memory_status
    memory_update = memory_tools.memory_update
    before = _stage_dirs()

    rejected = memory_update(base_revision="not-the-revision", patch=PATCH)
    assert json.loads(rejected)["error"]["code"] == "CONCURRENT_UPDATE"
    assert _stage_dirs() == before

    revision = json.loads(memory_status())["revision"]
    accepted = json.loads(memory_update(base_revision=revision, patch=PATCH))
    assert accepted["ok"] is True, accepted
    assert "worth remembering" in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert _stage_dirs() == before


def test_memory_update_accepts_structured_changes(memory, monkeypatch):
    import json

    from openprogram.programs.tools.knowledge.memory import memory as memory_tools

    monkeypatch.setattr(memory_tools, "authority_from_message", lambda *_: {
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "principal_id": "owner/install/0123456789abcdef",
        "authority_tier": "owner",
        "interaction": "interactive",
    })
    revision = json.loads(memory_tools.memory_status())["revision"]
    accepted = json.loads(memory_tools.memory_update(
        base_revision=revision,
        changes=[{
            "path": "topics/note.md",
            "action": "write",
            "content": NOTE.replace("worth keeping", "worth remembering"),
        }],
    ))

    assert accepted["ok"] is True, accepted
    assert "worth remembering" in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert _stage_dirs() == set()


def test_memory_update_accepts_record_changes(memory, monkeypatch):
    import json

    from openprogram.programs.tools.knowledge.memory import memory as memory_tools

    monkeypatch.setattr(memory_tools, "authority_from_message", lambda *_: {
        "speaker_kind": "owner",
        "speaker_id": "owner/local",
        "speaker_display": "Owner",
        "principal_id": "owner/install/0123456789abcdef",
        "authority_tier": "owner",
        "interaction": "interactive",
    })
    assert "memory_changes" in (
        memory_tools.UPDATE_SPEC["parameters"]["properties"]
    )
    revision = json.loads(memory_tools.memory_status())["revision"]
    accepted = json.loads(memory_tools.memory_update(
        base_revision=revision,
        memory_changes=[{
            "op": "update_record",
            "memory_id": "abc12345",
            "content": "Updated as one record.",
            "time": "2026-02-01",
            "source_refs": ["D1:1"],
        }],
    ))

    assert accepted["ok"] is True, accepted
    assert "Updated as one record." in (
        memory / "topics/note.md"
    ).read_text(encoding="utf-8")
    assert _stage_dirs() == set()


# ---- the background runtime -------------------------------------------


def _record(content: str = "remember this"):
    from openprogram.memory.runtime.state import SourceRecord
    return SourceRecord(
        provider="claude-code", thread_id="t1", message_id="m1",
        ordinal=1, role="user", content=content,
    )


def _runtime(memory):
    from openprogram.memory.runtime.online import OnlineMemoryRuntime
    return OnlineMemoryRuntime(memory, token_counter=len)


def test_process_cleans_up_after_a_batch_it_wrote(memory):
    before = _stage_dirs()

    assert _runtime(memory).process(
        [_record()], lambda workspace, batch: ["topics/note.md"], force=True
    ) is True
    assert _stage_dirs() == before


def test_process_cleans_up_after_a_writer_that_raised(memory):
    before = _stage_dirs()

    def writer(workspace, batch):
        raise RuntimeError("the writer gave up")

    with pytest.raises(RuntimeError):
        _runtime(memory).process([_record()], writer, force=True)
    assert _stage_dirs() == before


# -- the state file's own temporary ----------------------------------------


def test_saving_state_leaves_no_temporary_behind(tmp_path):
    """One shared ``runtime.json.tmp`` is how two writers overwrite each
    other's half-written bytes. A private name per write is the property
    this function can hold without help from the caller's lock, and a
    sibling project's stray ``runtime 2.json`` files, mode 600, are what
    the leftovers look like when it does not."""
    from openprogram.memory.runtime.state import (
        RuntimeState,
        RuntimeStateStore,
    )

    store = RuntimeStateStore(tmp_path)
    names = set()
    for tokens in (1, 2):
        state = RuntimeState(local_tokens=tokens)

        # Capture the name this write picked, then let it finish.
        import tempfile as _tempfile
        real = _tempfile.mkstemp

        def spy(*args, **kwargs):
            handle, path = real(*args, **kwargs)
            names.add(os.path.basename(path))
            return handle, path

        _tempfile.mkstemp = spy
        try:
            store.save(state)
        finally:
            _tempfile.mkstemp = real

    assert len(names) == 2, "each write picks a name of its own"
    assert all(str(os.getpid()) in name for name in names)
    assert list(store.path.parent.glob("*.tmp")) == []
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o644
    assert store.load().local_tokens == 2

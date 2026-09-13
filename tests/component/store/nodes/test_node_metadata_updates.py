"""Existing node updates preserve independently committed metadata."""
from contextlib import contextmanager

import pytest

from openprogram.store.session.session_store import SessionStore


@pytest.fixture
def stores(tmp_path):
    opened = []

    def new():
        store = SessionStore(tmp_path / "sessions")
        opened.append(store)
        return store
    first, second = new(), new()
    first.create_session("updates", "main")
    first.append_message("updates", {"id": "node", "role": "assistant", "content": "original"})
    first._open("updates")
    second._open("updates")
    try:
        yield first, second, new
    finally:
        for store in reversed(opened):
            store.close()


def node(store):
    return next(item for item in store.get_nodes("updates") if item.id == "node")


def apply(store, method):
    if method == "update":
        store.update_node("updates", "node", output="edited", metadata={"first": 1})
    else:
        store.merge_node_metadata_batch("updates", {"node": {"first": 1}})


@pytest.mark.parametrize("method", ["update", "batch"])
def test_disjoint_committed_patch_survives_write_boundary(stores, monkeypatch, method):
    first, second, new = stores
    original_lock = first._head_file_lock

    @contextmanager
    def interleave(git):
        second.update_node("updates", "node", predecessor="ROOT", caller="ROOT", metadata={"second": 2})
        with original_lock(git):
            yield
    monkeypatch.setattr(first, "_head_file_lock", interleave)
    apply(first, method)
    index = first._sessions["updates"][1]
    assert index.nodes_by_id["node"].metadata["second"] == 2
    assert "node" in index.children_by_predecessor["ROOT"]
    assert "node" in index.children_by_caller["ROOT"]
    for store in (first, new()):
        current = node(store)
        assert current.metadata["first"] == 1
        assert current.metadata["second"] == 2
        assert current.output == ("edited" if method == "update" else "original")


@pytest.mark.parametrize("method", ["update", "batch"])
def test_failed_write_does_not_publish_unpersisted_cache(stores, monkeypatch, method):
    first, _, new = stores
    before = node(first).to_dict()

    def fail(*_args):
        raise OSError("history unavailable")
    with monkeypatch.context() as patch:
        patch.setattr("openprogram.store.session.session_store.shared.atomic_write_text", fail)
        with pytest.raises(OSError, match="history unavailable"):
            apply(first, method)
    assert node(first).to_dict() == before
    assert node(new()).to_dict() == before


def test_finalization_summary_preserves_other_writer(stores, monkeypatch):
    from openprogram.agent.dispatcher.finalize import persist_turn_file_summary

    first, second, new = stores
    original_open = first._open

    def interleave(*args, **kwargs):
        pair = original_open(*args, **kwargs)
        second.merge_node_metadata("updates", "node", {"second": 2})
        return pair
    monkeypatch.setattr(first, "_open", interleave)
    monkeypatch.setattr("openprogram.store.default_store", lambda: first)
    monkeypatch.setattr("openprogram.store.document_history.DocumentHistory.register_model_turn", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "openprogram.store.snapshot.checkpoint.CheckpointStore.list_file_history",
        lambda *_: [{"path": "/tmp/example", "operation": "modify", "stats": {"added": 1, "removed": 0}}],
    )
    summary = persist_turn_file_summary("updates", "node")
    current = node(new())
    assert current.metadata["turn_files"] == summary
    assert current.metadata["second"] == 2


@pytest.mark.parametrize("method", ["update", "batch"])
def test_update_revalidates_relocated_history_and_cache(stores, monkeypatch, method):
    first, second, new = stores
    original_lock = first._head_file_lock
    source = first._session_dir("updates")
    destination = first.root_path / "projects" / "relocated" / "updates"

    @contextmanager
    def relocate(git):
        destination.parent.mkdir(parents=True)
        source.rename(destination)
        second._record_location("updates", destination)
        second.merge_node_metadata("updates", "node", {"second": 2})
        with original_lock(git):
            yield
    monkeypatch.setattr(first, "_head_file_lock", relocate)
    apply(first, method)
    git, index = first._sessions["updates"]
    assert git.path == destination
    assert index.nodes_by_id["node"].metadata["first"] == 1
    assert index.nodes_by_id["node"].metadata["second"] == 2
    assert node(new()).metadata == index.nodes_by_id["node"].metadata
    assert not source.exists()


@pytest.mark.parametrize("fields", [{"id": "other"}, {"seq": 100}, {"role": "code"}])
def test_update_cannot_repoint_history_identity(stores, fields):
    first, _, new = stores
    before = node(first).to_dict()
    with pytest.raises(ValueError, match="history filename"):
        first.update_node("updates", "node", **fields)
    assert node(first).to_dict() == before
    assert node(new()).to_dict() == before


@pytest.mark.parametrize("edge", ["predecessor", "caller"])
def test_invalid_edge_never_reaches_history_or_cache(stores, edge):
    first, _, new = stores
    before = node(first).to_dict()
    with pytest.raises(TypeError):
        first.update_node("updates", "node", **{edge: ["invalid"]})
    assert node(first).to_dict() == before
    assert node(new()).to_dict() == before

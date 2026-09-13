"""HEAD CAS owns its target and publishes one durable metadata result."""
import copy
from contextlib import ExitStack, closing, contextmanager

import pytest

from openprogram.store import SessionStore


@pytest.fixture
def stores(tmp_path):
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(tmp_path / "sessions")))
        store = new()
        store.create_session("s", "main")
        store.append_message("s", {"id": "root", "role": "user", "content": "root"})
        store.append_message("s", {"id": "child", "role": "assistant", "predecessor": "root"})
        yield store, new


def test_cas_clear_removes_legacy_head(stores):
    store, new = stores
    store.update_session("s", last_node_id="child")
    assert store.compare_and_set_head("s", "child", None)
    for reader in (store, new()):
        assert reader.get_session("s")["head_id"] is None


def test_cas_compares_legacy_head_as_exposed_by_reads(stores):
    store, new = stores
    git, _ = store._open("s")
    meta = git.read_meta()
    meta.update(head_id=None, last_node_id="child")
    git.write_meta(meta)
    assert store.get_session("s")["head_id"] == "child"
    assert store.compare_and_set_head("s", "child", "root")
    assert new().get_session("s")["head_id"] == "root"


@pytest.mark.parametrize("field", [
    "head_id", "last_node_id", "head_version", "writer_epoch", "branch_refs", "active_branch_id",
])
def test_supplemental_metadata_cannot_replace_cas_controls(stores, field):
    store, new = stores
    before = copy.deepcopy(store.get_session("s"))
    with pytest.raises(ValueError, match="control fields"):
        store.compare_and_set_head("s", "child", None, meta_update={field: "override"})
    assert store.get_session("s") == before
    assert new().get_session("s") == before


def test_cas_checks_summary_after_lock_refresh(stores, monkeypatch):
    store, new = stores
    other = new()
    original_lock = store._head_file_lock
    @contextmanager
    def interleave(git):
        other.update_node("s", "root", metadata={"covers_ids": ["archived"]})
        with original_lock(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(store, "_head_file_lock", interleave)
        with pytest.raises(ValueError, match="compaction summary"):
            store.compare_and_set_head("s", "child", "root")
    assert new().get_session("s")["head_id"] == "child"


def test_cas_mismatch_does_not_change_metadata(stores):
    store, new = stores
    before = copy.deepcopy(store.get_session("s"))
    assert not store.compare_and_set_head(
        "s", "wrong", "root", branch_update={"target_branch_id": "new"},
        meta_update={"head_id": "override"},
    )
    assert new().get_session("s") == before


def test_branch_forward_and_rollback_keep_reference_state(stores):
    store, new = stores
    store.update_session("s", head_version=4, writer_epoch=6, branch_refs={
        "source": {"branch_id": "source", "head_id": "child", "keep": "value"},
    })
    assert store.compare_and_set_head("s", "child", "root", branch_update={
        "source_branch_id": "source", "target_branch_id": "target", "active_branch_id": "target",
    })
    meta = new().get_session("s")
    assert (meta["head_version"], meta["writer_epoch"], meta["active_branch_id"]) == (5, 7, "target")
    assert meta["branch_refs"]["source"]["keep"] == "value"
    assert meta["branch_refs"]["target"]["head_id"] == "root"
    assert store.compare_and_set_head("s", "root", "child", branch_update={
        "source_branch_id": "source", "target_branch_id": "target", "active_branch_id": "source",
        "preserve_target": True, "target_status": "aborted",
    })
    meta = new().get_session("s")
    assert (meta["head_version"], meta["writer_epoch"], meta["active_branch_id"]) == (6, 8, "source")
    assert meta["branch_refs"]["target"]["head_id"] == "root"
    assert meta["branch_refs"]["target"]["status"] == "aborted"


def test_cas_publishes_detached_serialized_metadata(stores):
    store, new = stores
    extra = {"workspace_alignment": {"paths": ("one", "two"), "value": 1}}
    assert store.compare_and_set_head("s", "child", "root", meta_update=extra)
    extra["workspace_alignment"]["value"] = 2
    for reader in (store, new()):
        assert reader.get_session("s")["workspace_alignment"] == {"paths": ["one", "two"], "value": 1}


@pytest.mark.parametrize("identifier", ["../invalid", "", []])
def test_invalid_cas_session_keeps_mutation_validation(tmp_path, identifier):
    with closing(SessionStore(tmp_path / "sessions")) as store:
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        with pytest.raises(ValueError):
            store.compare_and_set_head(identifier, None, "root")
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_missing_cas_session_is_not_created(tmp_path):
    with closing(SessionStore(tmp_path / "sessions")) as store:
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        assert not store.compare_and_set_head("missing", None, "root")
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before

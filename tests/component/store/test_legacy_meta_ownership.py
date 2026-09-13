"""Remaining metadata mutations retain current durable fields and HEAD rules."""
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
        store.create_session("s", "main", title="original")
        store.append_message("s", {"id": "root", "role": "user", "content": "root"})
        store.append_message("s", {"id": "child", "role": "assistant", "predecessor": "root"})
        store.set_branch_name("s", "root", "original name")
        yield store, new


def mutate(store, method):
    if method == "mark_merged":
        store.mark_merged("s", [" root ", "root"])
    else:
        getattr(store, method)("s", "root")


@pytest.mark.parametrize("method", ["set_head", "delete_branch_name", "mark_merged"])
def test_mutation_preserves_intervening_fields(stores, monkeypatch, method):
    store, new = stores
    other = new()
    original_lock = store._head_file_lock
    @contextmanager
    def interleave(git):
        other.update_session("s", title="concurrent", pinned=True)
        other.set_branch_name("s", "child", "concurrent branch")
        other.mark_merged("s", ["other"])
        with original_lock(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(store, "_head_file_lock", interleave)
        mutate(store, method)
    for reader in (store, new()):
        meta = reader.get_session("s")
        assert meta["title"] == "concurrent"
        assert meta["pinned"] is True
        assert meta["branches"]["child"]["name"] == "concurrent branch"
        if method == "set_head":
            assert meta["head_id"] == "root"
        elif method == "delete_branch_name":
            assert "root" not in meta["branches"]
        else:
            assert meta["merged_heads"] == ["other", "root"]


@pytest.mark.parametrize("method", ["set_head", "delete_branch_name", "mark_merged"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_write_failure_does_not_publish_pending_metadata(stores, monkeypatch, method, error):
    store, new = stores
    expected = copy.deepcopy(store.get_session("s"))
    git, _ = store._open("s")
    def fail(*_args):
        raise error("write unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(git, "write_meta", fail)
        with pytest.raises(error, match="write unavailable"):
            mutate(store, method)
    assert store.get_session("s") == expected
    assert new().get_session("s") == expected


def test_head_rejects_summary_written_before_lock(stores, monkeypatch):
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
            store.set_head("s", "root")
    assert new().get_session("s")["head_id"] == "child"


def test_clearing_head_removes_legacy_alias(stores):
    store, new = stores
    store.update_session("s", last_node_id="child")
    store.set_head("s", None)
    assert store.get_session("s")["head_id"] is None
    assert new().get_session("s")["head_id"] is None


@pytest.mark.parametrize("method", ["delete_branch_name", "mark_merged"])
@pytest.mark.parametrize("identifier", ["missing", "../invalid", "", []])
def test_missing_or_invalid_mutations_do_not_create_storage(tmp_path, method, identifier):
    with closing(SessionStore(tmp_path / "sessions")) as store:
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        argument = ["root"] if method == "mark_merged" else "root"
        getattr(store, method)(identifier, argument)
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_idempotent_mutations_do_not_write(stores, monkeypatch):
    store, _ = stores
    store.mark_merged("s", ["first", "root"])
    git, _ = store._open("s")
    def unexpected(*_args):
        raise AssertionError("no metadata change should be written")
    monkeypatch.setattr(git, "write_meta", unexpected)
    store.mark_merged("s", [" root ", "", "first"])
    store.delete_branch_name("s", "missing")
    assert store.get_session("s")["merged_heads"] == ["first", "root"]


@pytest.mark.parametrize("head", [None, "existing-reference"])
def test_set_head_retains_missing_session_initialization(tmp_path, head):
    root = tmp_path / "sessions"
    with closing(SessionStore(root)) as store:
        store.set_head("missing", head)
        assert store.get_session("missing")["head_id"] == head
    with closing(SessionStore(root)) as fresh:
        assert fresh.get_session("missing")["head_id"] == head

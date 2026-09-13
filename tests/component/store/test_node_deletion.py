"""Node deletion selects literal identities and recovers durable failures."""
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
import os

import pytest

from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter, SessionStore


@pytest.fixture
def stores(tmp_path):
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(tmp_path / "sessions")))
        store = new()
        store.create_session("s", "main", title="original")
        yield store, new


def populate(store, identifier="target"):
    writer = SessionNodeWriter(store, "s")
    writer.append(Call(id="root", role="user"))
    writer.append(Call(id="retained", role="llm", predecessor="root"))
    writer.append(Call(id=identifier, role="llm", predecessor="root"))
    store.set_branch_name("s", identifier, "remove this")
    store.set_branch_name("s", "retained", "keep this")


def remove(store, method, identifier="target"):
    return getattr(store, method)("s", identifier)


def assert_deleted(reader, identifier="target"):
    assert {n.id for n in reader.get_nodes("s")} == {"root", "retained"}
    meta = reader.get_session("s")
    assert meta["head_id"] == "root"
    assert identifier not in meta.get("branches", {})
    assert meta["branches"]["retained"]["name"] == "keep this"


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
@pytest.mark.parametrize("identifier", [
    pytest.param(value, marks=pytest.mark.skipif(
        os.name == "nt" and value in {"*", "?"},
        reason="Windows filenames cannot contain star or question mark",
    )) for value in ["*", "?", "[retained]"]
])
def test_literal_deletion_retains_other_nodes(stores, method, identifier):
    store, new = stores
    populate(store, identifier)
    assert remove(store, method, identifier)
    assert_deleted(store, identifier)
    assert_deleted(new(), identifier)


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
def test_deletion_preserves_intervening_metadata(stores, monkeypatch, method):
    store, new = stores
    populate(store)
    other = new()
    original_lock = store._head_file_lock
    @contextmanager
    def interleave(git):
        other.update_session("s", title="concurrent", pinned=True)
        with original_lock(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(store, "_head_file_lock", interleave)
        assert remove(store, method)
    assert new().get_session("s")["title"] == "concurrent"
    assert new().get_session("s")["pinned"] is True


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
@pytest.mark.parametrize("stage", ["unlink", "metadata", "cancel"])
def test_deletion_failure_is_recovered_before_public_read(stores, monkeypatch, method, stage):
    store, new = stores
    populate(store)
    git, _ = store._open("s")
    error = KeyboardInterrupt if stage == "cancel" else OSError
    with monkeypatch.context() as patch:
        if stage == "unlink":
            original_unlink = Path.unlink
            def fail_unlink(path, *args, **kwargs):
                if path.parent.name == "history" and path.name.endswith("-target.json"):
                    raise OSError("injected deletion error")
                return original_unlink(path, *args, **kwargs)
            patch.setattr(Path, "unlink", fail_unlink)
        else:
            def fail_meta(*_args):
                raise error("injected deletion error")
            patch.setattr(git, "write_meta", fail_meta)
        with pytest.raises(error, match="injected deletion error"):
            remove(store, method)
    assert_deleted(new())
    assert_deleted(store)


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
def test_missing_deletion_does_not_create_session(tmp_path, method):
    with closing(SessionStore(tmp_path / "sessions")) as store:
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        assert not getattr(store, method)("missing", "absent")
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
def test_intent_write_failure_keeps_existing_records(stores, monkeypatch, method):
    store, new = stores
    populate(store)
    from openprogram.store.session.session_store import shared
    original_write = shared.atomic_write_text
    def fail_intent(path, text):
        if path.name == "openprogram-delete-nodes.json":
            raise OSError("intent unavailable")
        return original_write(path, text)
    with monkeypatch.context() as patch:
        patch.setattr(shared, "atomic_write_text", fail_intent)
        with pytest.raises(OSError, match="intent unavailable"):
            remove(store, method)
    for reader in (store, new()):
        assert {n.id for n in reader.get_nodes("s")} == {"root", "retained", "target"}
        assert reader.get_session("s")["head_id"] == "target"


def test_tail_selection_includes_child_appended_before_lock(stores, monkeypatch):
    store, new = stores
    populate(store)
    other = new()
    original_lock = store._head_file_lock
    @contextmanager
    def interleave(git):
        SessionNodeWriter(other, "s").append(Call(id="child", role="tool", caller="target"))
        with original_lock(git):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(store, "_head_file_lock", interleave)
        assert store.delete_branch_tail("s", "target") == 2
    assert_deleted(store)
    assert_deleted(new())

"""Disk writes never certify an index that has not finished publication."""
from contextlib import ExitStack, closing

import pytest

from openprogram.store import SessionStore
from openprogram.store.session.session_store import shared


@pytest.fixture
def stores(tmp_path):
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(tmp_path / "sessions")))
        store = new()
        store.create_session("s", "main", title="original")
        store.append_message("s", {"id": "root", "role": "user", "content": "original"})
        yield store, new


@pytest.mark.parametrize("operation", ["metadata", "head", "node"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_post_write_failure_reloads_durable_state(stores, monkeypatch, operation, error):
    store, new = stores
    git, _ = store._open("s")
    if operation == "node":
        # A fingerprint is only a change hint; equal observations must not
        # certify a write that the owning operation failed to publish.
        observed = git.stat_fingerprint()
        monkeypatch.setattr(git, "stat_fingerprint", lambda: observed)
    with monkeypatch.context() as patch:
        if operation == "node":
            original = shared.atomic_write_text
            def fail_after_write(path, text):
                result = original(path, text)
                if path.parent.name == "history":
                    raise error("after durable write")
                return result
            patch.setattr(shared, "atomic_write_text", fail_after_write)
        else:
            original = git.write_meta
            def fail_after_write(meta):
                original(meta)
                raise error("after durable write")
            patch.setattr(git, "write_meta", fail_after_write)
        with pytest.raises(error, match="after durable write"):
            if operation == "metadata":
                store.update_session("s", title="durable")
            elif operation == "head":
                store.compare_and_set_head("s", "root", None)
            else:
                store.update_node("s", "root", output="durable")
    for reader in (store, new()):
        if operation == "metadata":
            assert reader.get_session("s")["title"] == "durable"
        elif operation == "head":
            assert reader.get_session("s")["head_id"] is None
        else:
            assert reader.get_nodes("s")[0].output == "durable"


@pytest.mark.parametrize("operation", ["metadata", "history"])
def test_direct_git_write_invalidates_cached_store(stores, operation):
    store, new = stores
    git, _ = store._open("s")
    if operation == "metadata":
        meta = git.read_meta()
        meta["title"] = "direct write"
        git.write_meta(meta)
        for reader in (store, new()):
            assert reader.get_session("s")["title"] == "direct write"
    else:
        git.write_history(1, "llm", "child", {
            "id": "child", "seq": 1, "role": "llm", "predecessor": "root",
        })
        for reader in (store, new()):
            assert [node.id for node in reader.get_nodes("s")] == ["root", "child"]

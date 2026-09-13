"""Turn commits preserve durable metadata written by concurrent session owners."""
import json
import subprocess
from contextlib import ExitStack, closing, contextmanager

import pytest

from openprogram.store import SessionStore


@pytest.fixture
def stores(tmp_path):
    with ExitStack() as stack:
        def new():
            return stack.enter_context(closing(SessionStore(tmp_path / "sessions")))
        first, second = new(), new()
        first.create_session("commit", "main", title="original")
        first.append_message("commit", {"id": "root", "role": "user", "content": "root"})
        yield first, second, new


def committed_meta(store, sha):
    result = subprocess.run(
        ["git", "-C", str(store._session_dir("commit")), "show", f"{sha}:meta.json"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("field", ["title", "head_id"])
def test_turn_commit_preserves_intervening_metadata(stores, monkeypatch, field):
    first, second, new = stores
    original_lock = first._head_file_lock
    @contextmanager
    def interleave(git):
        if field == "title":
            second.update_session("commit", title="committed title")
        else:
            second.append_message("commit", {
                "id": "child", "role": "assistant", "predecessor": "root",
            })
        with original_lock(git):
            yield
    monkeypatch.setattr(first, "_head_file_lock", interleave)
    sha = first.commit_turn("commit", "finished turn")
    assert sha
    expected = "committed title" if field == "title" else "child"
    assert committed_meta(first, sha)[field] == expected
    assert new().get_session("commit")[field] == expected


def test_turn_commit_recovers_pending_append(stores, monkeypatch):
    first, _, new = stores
    git, _ = first._open("commit")
    def fail(*_args):
        raise OSError("metadata unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(git, "write_meta", fail)
        with pytest.raises(OSError, match="metadata unavailable"):
            first.append_message("commit", {"id": "child", "role": "assistant", "predecessor": "root"})
    sha = first.commit_turn("commit", "recovered turn")
    assert committed_meta(first, sha)["head_id"] == "child"
    assert new().get_session("commit")["head_id"] == "child"
    assert first.commit_turn("commit", "no change") is None


def test_failed_commit_preserves_data_for_retry(stores, monkeypatch):
    first, _, new = stores
    first.update_session("commit", title="durable title")
    git, _ = first._open("commit")
    def fail(*_args):
        raise OSError("Git unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(git, "commit_all", fail)
        with pytest.raises(OSError, match="Git unavailable"):
            first.commit_turn("commit", "failed commit")
    assert new().get_session("commit")["title"] == "durable title"
    sha = first.commit_turn("commit", "retry")
    assert committed_meta(first, sha)["title"] == "durable title"


def test_missing_session_commit_does_not_create_storage(stores):
    first, _, _ = stores
    before = sorted(str(p.relative_to(first.root_path)) for p in first.root_path.rglob("*"))
    assert first.commit_turn("missing", "nothing") is None
    assert sorted(str(p.relative_to(first.root_path)) for p in first.root_path.rglob("*")) == before

import json
import threading
from pathlib import Path

from openprogram.store.session.session_store import SessionStore


def test_concurrent_mark_merged_preserves_both_heads(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    store.create_session("s1", "main")
    pair = store._open("s1")
    assert pair is not None
    git, _idx = pair
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    combined_write_finished = threading.Event()
    original = git.write_meta

    def delayed_write(meta):
        if meta.get("merged_heads") == ["head-a"]:
            first_write_started.set()
            assert release_first_write.wait(timeout=2)
        result = original(meta)
        if set(meta.get("merged_heads") or []) == {"head-a", "head-b"}:
            combined_write_finished.set()
        return result

    monkeypatch.setattr(git, "write_meta", delayed_write)
    first = threading.Thread(target=store.mark_merged, args=("s1", ["head-a"]))
    second = threading.Thread(target=store.mark_merged, args=("s1", ["head-b"]))
    first.start()
    assert first_write_started.wait(timeout=2)
    second.start()
    combined_write_finished.wait(timeout=0.2)
    release_first_write.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert not first.is_alive()
    assert not second.is_alive()

    assert store.merged_heads("s1") == {"head-a", "head-b"}
    on_disk = json.loads((root / "s1" / "meta.json").read_text(encoding="utf-8"))
    assert set(on_disk["merged_heads"]) == {"head-a", "head-b"}


def test_mark_merged_preserves_order_and_is_idempotent(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main")

    store.mark_merged("s1", [" a ", "", "a", "b"])
    store.mark_merged("s1", ["b", "c"])

    pair = store._open("s1")
    assert pair is not None
    assert pair[1].meta["merged_heads"] == ["a", "b", "c"]


def test_cached_open_waits_for_local_meta_publish(tmp_path: Path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main")
    pair = store._open("s1")
    assert pair is not None
    git, idx = pair
    published = threading.Event()
    release_sync = threading.Event()
    original_mark_synced = git.mark_synced

    def delayed_mark_synced():
        published.set()
        assert release_sync.wait(timeout=2)
        original_mark_synced()

    monkeypatch.setattr(git, "mark_synced", delayed_mark_synced)
    with idx._lock:
        idx.meta["merged_heads"] = ["head-a"]
    writer = threading.Thread(target=store._persist_meta, args=(git, idx))
    writer.start()
    assert published.wait(timeout=2)
    with idx._lock:
        idx.meta["merged_heads"].append("head-b")
    reader = threading.Thread(target=store._open, args=("s1",))
    reader.start()
    release_sync.set()
    writer.join(timeout=3)
    reader.join(timeout=3)
    assert not writer.is_alive()
    assert not reader.is_alive()

    store._persist_meta(git, idx)
    assert store.merged_heads("s1") == {"head-a", "head-b"}
    reloaded = SessionStore(tmp_path / "sessions")
    assert reloaded.merged_heads("s1") == {"head-a", "head-b"}

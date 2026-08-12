import json
import threading
from pathlib import Path

from openprogram.store.session.memory_index import SessionMemoryIndex
from openprogram.store.session.session_store import SessionStore


def test_concurrent_mark_merged_preserves_both_heads(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    store.create_session("s1", "main")
    barrier = threading.Barrier(2)
    original = SessionMemoryIndex.set_meta

    def synchronized_set_meta(self, **fields):
        if "merged_heads" in fields:
            barrier.wait(timeout=2)
        return original(self, **fields)

    monkeypatch.setattr(SessionMemoryIndex, "set_meta", synchronized_set_meta)
    threads = [
        threading.Thread(target=store.mark_merged, args=("s1", [head]))
        for head in ("head-a", "head-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

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

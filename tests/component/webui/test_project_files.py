"""Project files panel backend — ws_actions.files + GET /files/raw.

Covers the wire contract:

* ``project_file_tree`` — dirs-first case-insensitive listing, dotfiles in
* ``project_file_read`` — text content, ``binary`` / ``too_large`` flags,
  whitespace filenames round-trip un-stripped
* ``project_file_write`` — utf-8 write + round-trip, ``expected_mtime``
  conflict gate (no clobber), 5 MB cap, parent dir must exist
* ``_resolve`` guard — ``..``, absolute paths (even inside the root) and
  symlink escapes rejected, unknown project rejected
* ``GET /files/raw`` — nosniff everywhere; images and PDFs keep the real
  content-type inline (PDFs additionally skip the CSP sandbox so the
  browser's built-in viewer works), everything else is octet-stream +
  attachment + sandbox; 403 on escape, 404 on missing file / unknown
  project
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import resource
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.store.project import project_store
from openprogram.webui.ws_actions import files as ws_files


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake project "p1" rooted at tmp_path/proj with a small tree."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "Alpha_dir").mkdir()
    (root / "src" / "x.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "zeta.txt").write_text("zzz", encoding="utf-8")
    (root / "apple.txt").write_text("aaa", encoding="utf-8")
    (root / ".hidden").write_text("dot", encoding="utf-8")

    # Something outside the project to escape to.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    os.symlink(outside / "secret.txt", root / "sneaky_link")

    def fake_get_project(project_id: str):
        if project_id == "p1":
            return types.SimpleNamespace(id="p1", path=str(root))
        return None

    monkeypatch.setattr(project_store, "get_project", fake_get_project)
    return root


def _run(handler, cmd: dict) -> dict:
    ws = _FakeWS()
    asyncio.run(handler(ws, cmd))
    assert len(ws.sent) == 1
    return ws.sent[0]


# ---- project_file_tree ----------------------------------------------------

def test_tree_root_dirs_first_case_insensitive(project_root):
    frame = _run(ws_files.handle_project_file_tree,
                 {"project_id": "p1", "path": ""})
    assert frame["type"] == "project_file_tree_result"
    data = frame["data"]
    assert data["project_id"] == "p1" and data["path"] == ""
    assert "error" not in data
    names = [(e["name"], e["type"]) for e in data["entries"]]
    assert names == [
        ("Alpha_dir", "dir"),
        ("src", "dir"),
        (".hidden", "file"),
        ("apple.txt", "file"),
        ("sneaky_link", "file"),
        ("zeta.txt", "file"),
    ]
    by_name = {e["name"]: e for e in data["entries"]}
    assert by_name["zeta.txt"]["size"] == 3
    assert by_name["zeta.txt"]["mtime"] > 0


def test_tree_subdirectory(project_root):
    data = _run(ws_files.handle_project_file_tree,
                {"project_id": "p1", "path": "src"})["data"]
    assert [e["name"] for e in data["entries"]] == ["x.py"]


def test_tree_unknown_project(project_root):
    data = _run(ws_files.handle_project_file_tree,
                {"project_id": "nope", "path": ""})["data"]
    assert data["entries"] == []
    assert "unknown project" in data["error"]


def test_tree_pages_use_opaque_stable_cursor(project_root):
    for index in range(105):
        (project_root / f"item-{index:03d}.txt").write_text("x", encoding="utf-8")

    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 100,
    })["data"]
    assert len(first["entries"]) == 100
    assert first["snapshot_id"]
    assert isinstance(first["next_cursor"], str)
    assert not first["next_cursor"].isdigit()

    second = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": first["next_cursor"],
        "page_size": 100,
    })["data"]
    assert second["snapshot_id"] == first["snapshot_id"]
    assert [e["name"] for e in second["entries"]] == [
        "item-096.txt", "item-097.txt", "item-098.txt", "item-099.txt",
        "item-100.txt", "item-101.txt", "item-102.txt",
        "item-103.txt", "item-104.txt", "sneaky_link", "zeta.txt",
    ]
    assert second["next_cursor"] is None


def test_tree_cursor_rejects_directory_version_change(project_root):
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    (project_root / "changed.txt").write_text("changed", encoding="utf-8")
    stale = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": first["next_cursor"],
        "page_size": 1,
    })["data"]
    assert stale["error_code"] == "STALE_SNAPSHOT"
    assert stale["status"] == "stale"
    assert stale["entries"] == []


@pytest.mark.parametrize("code", ["STALE_SNAPSHOT", "STALE_CURSOR", "CURSOR"])
def test_query_stale_errors_use_canonical_status(code):
    result = ws_files._query_error("p1", "src", code=code)
    assert result["status"] == "stale"
    assert result["error_code"] == code


def test_tree_cursor_is_bound_to_project_and_path(project_root, monkeypatch):
    other = project_root.parent / "other"
    other.mkdir()
    (other / "same.txt").write_text("other", encoding="utf-8")
    original = project_store.get_project

    def get_project(project_id):
        if project_id == "p2":
            return types.SimpleNamespace(id="p2", path=str(other), name="Other")
        return original(project_id)

    monkeypatch.setattr(project_store, "get_project", get_project)
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    stale = _run(ws_files.handle_project_file_tree, {
        "project_id": "p2", "path": "", "cursor": first["next_cursor"],
        "page_size": 1,
    })["data"]
    assert stale["error_code"] == "STALE_SNAPSHOT"
    assert stale["entries"] == []


def test_project_search_finds_unexpanded_nested_paths_and_ignores_build_dirs(project_root):
    deep = project_root / "unexpanded" / "nested"
    deep.mkdir(parents=True)
    (deep / "needle.py").write_text("x", encoding="utf-8")
    ignored = project_root / "node_modules" / "needle.js"
    ignored.parent.mkdir()
    ignored.write_text("x", encoding="utf-8")

    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
        "page_size": 100,
    })["data"]
    assert data["error_code"] is None
    assert [row["path"] for row in data["results"]] == [
        "unexpanded/nested/needle.py",
    ]
    assert data["snapshot_id"]


def test_project_search_cursor_is_stable_and_invalidated(project_root):
    search_dir = project_root / "search"
    search_dir.mkdir()
    for index in range(3):
        (search_dir / f"needle-{index}.txt").write_text("x", encoding="utf-8")
    first = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
        "page_size": 1,
    })["data"]
    assert first["next_cursor"]
    (search_dir / "needle-new.txt").write_text("x", encoding="utf-8")
    stale = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
        "cursor": first["next_cursor"], "page_size": 1,
    })["data"]
    assert stale["error_code"] == "STALE_SNAPSHOT"
    assert stale["results"] == []


def test_project_queries_reject_arbitrary_root(project_root):
    tree = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "root": "/etc",
    })["data"]
    search = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "passwd", "root": "/etc",
    })["data"]
    assert tree["error_code"] == search["error_code"] == "INVALID_REQUEST"
    assert tree["entries"] == [] and search["results"] == []


def test_project_search_does_not_follow_symlink(project_root):
    external = project_root.parent / "external-search"
    external.mkdir()
    (external / "needle.txt").write_text("secret", encoding="utf-8")
    (project_root / "linked-dir").symlink_to(external, target_is_directory=True)
    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
    })["data"]
    assert data["results"] == []


@pytest.mark.parametrize("field,value", [
    ("query", {"needle": True}),
    ("mode", {"contains": True}),
    ("type", ["file"]),
    ("sort", ["rank_path"]),
    ("page_size", "100"),
    ("page_size", 0),
    ("page_size", 101),
])
def test_project_search_rejects_malformed_query_fields(project_root, field, value):
    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle", field: value,
    })["data"]
    assert data["error_code"] == "INVALID_REQUEST"
    assert data["results"] == []


def test_project_search_rejects_empty_query_explicitly(project_root):
    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "",
    })["data"]
    assert data["error_code"] == "INVALID_REQUEST"
    assert data["results"] == []


def test_project_query_reports_permission_without_partial_snapshot(project_root, monkeypatch):
    original_scandir = ws_files.os.scandir

    def denied(path):
        if isinstance(path, int):
            raise PermissionError(errno.EACCES, "permission denied")
        return original_scandir(path)

    monkeypatch.setattr(ws_files.os, "scandir", denied)
    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
    })["data"]
    assert data["error_code"] == "PERMISSION"
    assert data["results"] == []
    assert data["snapshot_id"] is None


def test_project_query_rejects_symlink_path_even_when_inside_project(project_root):
    target = project_root / "real-dir"
    target.mkdir()
    (target / "needle.txt").write_text("x", encoding="utf-8")
    (project_root / "inside-link").symlink_to(target, target_is_directory=True)
    for handler, field in ((ws_files.handle_project_file_tree, "entries"),
                           (ws_files.handle_project_file_search, "results")):
        command = {"project_id": "p1", "path": "inside-link"}
        if field == "results":
            command["query"] = "needle"
        data = _run(handler, command)["data"]
        assert data["error_code"] == "INVALID_REQUEST"
        assert data[field] == []


def test_project_search_does_not_follow_directory_replaced_by_symlink(project_root, monkeypatch):
    nested = project_root / "nested"
    nested.mkdir()
    external = project_root.parent / "raced-external"
    external.mkdir()
    (external / "needle.txt").write_text("secret", encoding="utf-8")
    original_open = ws_files.os.open

    def racing_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and path == "nested":
            nested.rmdir()
            nested.symlink_to(external, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ws_files.os, "open", racing_open)
    data = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "", "query": "needle",
    })["data"]
    assert data["error_code"] in {"IO_ERROR", "PERMISSION"}
    assert data["results"] == []
    assert data["snapshot_id"] is None


def test_project_queries_reject_ignored_path(project_root):
    ignored = project_root / "node_modules"
    ignored.mkdir()
    (ignored / "needle.js").write_text("x", encoding="utf-8")
    tree = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "node_modules",
    })["data"]
    search = _run(ws_files.handle_project_file_search, {
        "project_id": "p1", "path": "node_modules", "query": "needle",
    })["data"]
    assert tree["error_code"] == search["error_code"] == "INVALID_REQUEST"
    assert tree["entries"] == [] and search["results"] == []


def test_repeated_cursor_requests_reuse_bounded_token(project_root):
    for index in range(105):
        (project_root / f"bounded-{index:03d}.txt").write_text("x", encoding="utf-8")
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    cursor = first["next_cursor"]
    expected_next = None
    for _ in range(1000):
        data = _run(ws_files.handle_project_file_tree, {
            "project_id": "p1", "path": "", "page_size": 1, "cursor": cursor,
        })["data"]
        assert data["error_code"] is None
        expected_next = expected_next or data["next_cursor"]
        assert data["next_cursor"] == expected_next


def test_snapshot_eviction_removes_all_cursors_and_returns_stale(project_root, monkeypatch):
    with ws_files._QUERY_LOCK:
        ws_files._QUERY_SNAPSHOTS.clear()
        ws_files._QUERY_CURSORS.clear()
        ws_files._QUERY_CURSOR_TOKENS.clear()
    monkeypatch.setattr(ws_files, "_QUERY_MAX_SNAPSHOTS", 1)
    for index in range(4):
        (project_root / f"evict-{index}.txt").write_text("x", encoding="utf-8")
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    cursor_one = first["next_cursor"]
    second = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
        "cursor": cursor_one,
    })["data"]
    cursor_two = second["next_cursor"]
    _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "src", "page_size": 1,
    })
    assert cursor_one not in ws_files._QUERY_CURSORS
    assert cursor_two not in ws_files._QUERY_CURSORS
    stale = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": cursor_one,
    })["data"]
    assert stale["error_code"] == "STALE_SNAPSHOT"


def test_expired_snapshot_removes_cursor_and_returns_stale(project_root, monkeypatch):
    with ws_files._QUERY_LOCK:
        ws_files._QUERY_SNAPSHOTS.clear()
        ws_files._QUERY_CURSORS.clear()
        ws_files._QUERY_CURSOR_TOKENS.clear()
    monkeypatch.setattr(ws_files, "_QUERY_SNAPSHOT_TTL", 0.0)
    (project_root / "ttl.txt").write_text("x", encoding="utf-8")
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    cursor = first["next_cursor"]
    stale = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": cursor,
    })["data"]
    assert stale["error_code"] == "STALE_SNAPSHOT"
    assert cursor not in ws_files._QUERY_CURSORS


def test_tree_limit_stops_after_maximum_entry_probe(project_root):
    for index in range(ws_files._QUERY_MAX_SNAPSHOT_ITEMS + 1):
        (project_root / f"large-{index:05d}.txt").write_text("x", encoding="utf-8")
    data = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 100,
    })["data"]
    assert data["error_code"] == "LIMIT_EXCEEDED"
    assert data["entries"] == []
    assert data["snapshot_id"] is None


def test_cursor_basis_enforces_snapshot_item_limit(project_root, monkeypatch):
    for index in range(3):
        (project_root / f"cursor-limit-{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ws_files, "_QUERY_MAX_SNAPSHOT_ITEMS", 10)
    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    assert first["next_cursor"]
    monkeypatch.setattr(ws_files, "_QUERY_MAX_SNAPSHOT_ITEMS", 2)
    second = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": first["next_cursor"],
        "page_size": 1,
    })["data"]
    assert second["error_code"] == "LIMIT_EXCEEDED"


def test_search_closes_sibling_directory_fds_under_low_nofile_limit(project_root):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard < 256 or soft <= 256:
        pytest.skip("host file-descriptor limit cannot be lowered to 256")
    for index in range(300):
        (project_root / f"sibling-{index:03d}").mkdir()
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
    try:
        data = _run(ws_files.handle_project_file_search, {
            "project_id": "p1", "path": "", "query": "needle",
        })["data"]
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert data["error_code"] is None


def test_global_snapshot_quota_bounds_items_bytes_and_cursors(project_root, monkeypatch):
    with ws_files._QUERY_LOCK:
        ws_files._QUERY_SNAPSHOTS.clear()
        ws_files._QUERY_CURSORS.clear()
        ws_files._QUERY_CURSOR_TOKENS.clear()
    monkeypatch.setattr(ws_files, "_QUERY_MAX_TOTAL_ITEMS", 30)
    monkeypatch.setattr(ws_files, "_QUERY_MAX_TOTAL_BYTES", 10_000)
    monkeypatch.setattr(ws_files, "_QUERY_MAX_CURSORS", 2)
    for index in range(4):
        (project_root / f"quota-{index}.txt").write_text("x", encoding="utf-8")
    for _ in range(5):
        _run(ws_files.handle_project_file_tree, {
            "project_id": "p1", "path": "", "page_size": 1,
        })
        items, bytes_used, cursors = ws_files._snapshot_usage()
        assert items <= 30
        assert bytes_used <= 10_000
        assert cursors <= 2


def test_query_page_barrier_does_not_return_cursor_for_evicted_snapshot(project_root, monkeypatch):
    from openprogram.webui.ws_actions import files_query

    first = _run(ws_files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
    })["data"]
    snapshot = ws_files._QUERY_SNAPSHOTS[first["snapshot_id"]]
    original_new_cursor = files_query._new_cursor

    def evict_before_return(snapshot_id, offset):
        files_query._evict_snapshot(snapshot_id)
        return original_new_cursor(snapshot_id, offset)

    monkeypatch.setattr(files_query, "_new_cursor", evict_before_return)
    page = ws_files._query_page(snapshot, 0, 1, "entries")
    assert page["error_code"] == "STALE_SNAPSHOT"


def test_query_path_nul_is_invalid_request(project_root):
    for handler, field in ((ws_files.handle_project_file_tree, "entries"),
                           (ws_files.handle_project_file_search, "results")):
        command = {"project_id": "p1", "path": "bad\x00path"}
        if field == "results":
            command["query"] = "needle"
        data = _run(handler, command)["data"]
        assert data["error_code"] == "INVALID_REQUEST"
        assert data[field] == []


def test_zero_match_search_counts_full_candidate_basis_and_evicts_old_snapshot(
    project_root, monkeypatch,
):
    with ws_files._QUERY_LOCK:
        ws_files._QUERY_SNAPSHOTS.clear()
        ws_files._QUERY_CURSORS.clear()
        ws_files._QUERY_CURSOR_TOKENS.clear()
    monkeypatch.setattr(ws_files, "_QUERY_MAX_TOTAL_ITEMS", 15_000)
    monkeypatch.setattr(ws_files, "_QUERY_MAX_TOTAL_BYTES", 8 * 1024 * 1024)
    for index in range(ws_files._QUERY_MAX_SNAPSHOT_ITEMS - 6):
        (project_root / f"candidate-{index:05d}.txt").write_text(
            "x", encoding="utf-8",
        )
    for query in ("zero-match-a", "zero-match-b"):
        data = _run(ws_files.handle_project_file_search, {
            "project_id": "p1", "path": "", "query": query,
        })["data"]
        assert data["error_code"] is None
        assert data["results"] == []
        items, bytes_used, _cursors = ws_files._snapshot_usage()
        assert items > 0
        assert items <= 15_000
        assert bytes_used <= 8 * 1024 * 1024


def test_tree_path_traversal_rejected(project_root):
    for bad in ("../outside", "src/../../outside", "/etc"):
        data = _run(ws_files.handle_project_file_tree,
                    {"project_id": "p1", "path": bad})["data"]
        assert data["error"] == "path escapes project root", bad
        assert data["entries"] == []


# ---- project_file_read ----------------------------------------------------

def test_read_text_file(project_root):
    frame = _run(ws_files.handle_project_file_read,
                 {"project_id": "p1", "path": "src/x.py"})
    assert frame["type"] == "project_file_read_result"
    data = frame["data"]
    assert data["content"] == "print('hi')\n"
    assert data["size"] == len("print('hi')\n")
    assert data["mtime"] > 0
    assert len(data["revision"]) == 64
    assert "binary" not in data and "too_large" not in data


def test_read_binary_flag(project_root):
    (project_root / "blob.bin").write_bytes(b"\x00\x01\x02rest")
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": "blob.bin"})["data"]
    assert data["binary"] is True
    assert "content" not in data
    assert data["size"] == 7


def test_read_too_large_flag(project_root):
    (project_root / "big.txt").write_bytes(b"a" * 1_000_001)
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": "big.txt"})["data"]
    assert data["too_large"] is True
    assert "content" not in data
    assert data["size"] == 1_000_001


def test_read_growth_is_bounded_after_initial_stat(project_root, monkeypatch):
    target = project_root / "growing.txt"
    target.write_bytes(b"a" * 8_192)
    original_open = open

    class GrowingReader:
        def __init__(self, stream):
            self.stream = stream
            self.did_grow = False

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            data = self.stream.read(size)
            if not self.did_grow:
                self.did_grow = True
                target.write_bytes(b"a" * (ws_files._READ_MAX_BYTES + 1))
            return data

    def growing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return GrowingReader(stream) if os.fspath(path) == os.fspath(target) else stream

    monkeypatch.setattr(ws_files, "open", growing_open, raising=False)
    data = _run(ws_files.handle_project_file_read, {
        "project_id": "p1", "path": "growing.txt",
    })["data"]
    assert data["too_large"] is True
    assert "content" not in data


def test_read_unknown_project(project_root):
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "nope", "path": "apple.txt"})["data"]
    assert "unknown project" in data["error"]
    assert "content" not in data


def test_read_symlink_escape_rejected(project_root):
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": "sneaky_link"})["data"]
    assert data["error"] == "path escapes project root"
    assert "content" not in data


def test_read_traversal_and_absolute_rejected(project_root):
    for bad in ("../outside/secret.txt", str(project_root.parent / "outside" / "secret.txt")):
        data = _run(ws_files.handle_project_file_read,
                    {"project_id": "p1", "path": bad})["data"]
        assert data["error"] == "path escapes project root", bad


def test_read_in_root_absolute_path_rejected(project_root):
    # Absolute paths are rejected up front, even when they resolve inside
    # the project root.
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": str(project_root / "apple.txt")})["data"]
    assert data["error"] == "path escapes project root"
    assert "content" not in data


def test_read_whitespace_filename_roundtrip(project_root):
    (project_root / " padded ").write_text("pad", encoding="utf-8")
    data = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": " padded "})["data"]
    assert data["path"] == " padded "  # echoed un-stripped
    assert data["content"] == "pad"


# ---- project_file_write ----------------------------------------------------

def _write(cmd: dict) -> dict:
    frame = _run(ws_files.handle_project_file_write, cmd)
    assert frame["type"] == "project_file_write_result"
    return frame["data"]


def test_write_roundtrip(project_root):
    read = _run(ws_files.handle_project_file_read,
                {"project_id": "p1", "path": "apple.txt"})["data"]
    data = _write({"project_id": "p1", "path": "apple.txt",
                   "content": "fresh\n", "expected_mtime": read["mtime"]})
    assert data["ok"] is True
    assert data["mtime"] > 0
    assert len(data["revision"]) == 64
    assert "conflict" not in data and "error" not in data
    again = _run(ws_files.handle_project_file_read,
                 {"project_id": "p1", "path": "apple.txt"})["data"]
    assert again["content"] == "fresh\n"
    assert again["mtime"] == data["mtime"]


def test_write_rejects_content_drift_when_mtime_is_restored(project_root):
    read = _run(ws_files.handle_project_file_read, {
        "project_id": "p1", "path": "apple.txt",
    })["data"]
    target = project_root / "apple.txt"
    target.write_text("external\n", encoding="utf-8")
    os.utime(target, (read["mtime"], read["mtime"]))
    result = _run(ws_files.handle_project_file_write, {
        "project_id": "p1", "path": "apple.txt", "content": "local\n",
        "expected_mtime": read["mtime"],
        "baseline_revision": read["revision"],
    })["data"]
    assert result["conflict"] is True
    assert target.read_text(encoding="utf-8") == "external\n"


def test_write_revision_covers_the_full_editable_read_limit(project_root):
    target = project_root / "large-text.txt"
    target.write_bytes(b"x" * (ws_files._IDENTITY_DIGEST_MAX_BYTES + 1))
    read = _run(ws_files.handle_project_file_read, {
        "project_id": "p1", "path": "large-text.txt",
    })["data"]
    assert read["size"] < ws_files._READ_MAX_BYTES
    assert len(read["revision"]) == 64
    result = _write({
        "project_id": "p1", "path": "large-text.txt", "content": "y" * read["size"],
        "expected_mtime": read["mtime"], "baseline_revision": read["revision"],
    })
    assert result["ok"] is True


def test_write_conflict_does_not_write(project_root):
    stale = os.stat(project_root / "apple.txt").st_mtime - 100
    data = _write({"project_id": "p1", "path": "apple.txt",
                   "content": "clobber", "expected_mtime": stale})
    assert data["conflict"] is True
    assert "ok" not in data
    assert (project_root / "apple.txt").read_text() == "aaa"  # untouched


def test_write_no_expected_mtime_creates_new_file(project_root):
    data = _write({"project_id": "p1", "path": "src/new.txt",
                   "content": "born"})
    assert data["ok"] is True
    assert (project_root / "src" / "new.txt").read_text() == "born"


def test_write_traversal_rejected(project_root):
    for bad in ("../outside/evil.txt", "/etc/evil", "sneaky_link"):
        data = _write({"project_id": "p1", "path": bad, "content": "x"})
        assert data["error"] == "path escapes project root", bad
    assert (project_root.parent / "outside" / "secret.txt").read_text() == "secret"


def test_write_oversize_rejected(project_root):
    data = _write({"project_id": "p1", "path": "apple.txt",
                   "content": "a" * 5_000_001})
    assert data["error"] == "content exceeds 5 MB"
    assert (project_root / "apple.txt").read_text() == "aaa"  # untouched


def test_write_parent_dir_must_exist(project_root):
    data = _write({"project_id": "p1", "path": "no_such_dir/new.txt",
                   "content": "x"})
    assert "parent directory" in data["error"]
    assert not (project_root / "no_such_dir").exists()  # no mkdir


def test_write_non_string_content_rejected(project_root):
    data = _write({"project_id": "p1", "path": "apple.txt",
                   "content": None})
    assert data["error"] == "content must be a string"


# ---- project_file_create ----------------------------------------------------

def test_create_file_and_dir(project_root):
    frame = _run(ws_files.handle_project_file_create,
                 {"project_id": "p1", "path": "src/born.txt", "kind": "file"})
    assert frame["type"] == "project_file_create_result"
    assert frame["data"]["ok"] is True
    assert (project_root / "src" / "born.txt").read_bytes() == b""  # empty

    data = _run(ws_files.handle_project_file_create,
                {"project_id": "p1", "path": "src/newdir", "kind": "dir"})["data"]
    assert data["ok"] is True
    assert (project_root / "src" / "newdir").is_dir()


def test_create_exists_rejected(project_root):
    for path, kind in (("apple.txt", "file"), ("src", "dir")):
        data = _run(ws_files.handle_project_file_create,
                    {"project_id": "p1", "path": path, "kind": kind})["data"]
        assert data["error"] == f"already exists: {path!r}"
    assert (project_root / "apple.txt").read_text() == "aaa"  # untouched


def test_create_parent_missing(project_root):
    data = _run(ws_files.handle_project_file_create,
                {"project_id": "p1", "path": "ghost/child.txt", "kind": "file"})["data"]
    assert "parent directory" in data["error"]
    assert not (project_root / "ghost").exists()  # no implicit mkdir


def test_create_escape_rejected(project_root):
    for bad in ("../outside/evil.txt", "/etc/evil"):
        data = _run(ws_files.handle_project_file_create,
                    {"project_id": "p1", "path": bad, "kind": "file"})["data"]
        assert data["error"] == "path escapes project root", bad
    assert not (project_root.parent / "outside" / "evil.txt").exists()


def test_create_bad_kind_rejected(project_root):
    data = _run(ws_files.handle_project_file_create,
                {"project_id": "p1", "path": "x.txt", "kind": "symlink"})["data"]
    assert data["error"] == "kind must be 'file' or 'dir'"


# ---- project_file_rename ----------------------------------------------------

def test_rename_in_place(project_root):
    frame = _run(ws_files.handle_project_file_rename,
                 {"project_id": "p1", "path": "apple.txt",
                  "new_path": "banana.txt"})
    assert frame["type"] == "project_file_rename_result"
    data = frame["data"]
    assert data["ok"] is True
    assert data["path"] == "apple.txt" and data["new_path"] == "banana.txt"
    assert not (project_root / "apple.txt").exists()
    assert (project_root / "banana.txt").read_text() == "aaa"


def test_rename_moves_across_subdirs(project_root):
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "src/x.py",
                 "new_path": "Alpha_dir/x_moved.py"})["data"]
    assert data["ok"] is True
    assert not (project_root / "src" / "x.py").exists()
    assert (project_root / "Alpha_dir" / "x_moved.py").read_text() == "print('hi')\n"


def test_rename_source_missing(project_root):
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "nope.txt",
                 "new_path": "other.txt"})["data"]
    assert data["error"] == "source does not exist: 'nope.txt'"


def test_rename_destination_exists(project_root):
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "apple.txt",
                 "new_path": "zeta.txt"})["data"]
    assert data["error"] == "destination already exists: 'zeta.txt'"
    assert (project_root / "apple.txt").read_text() == "aaa"  # untouched
    assert (project_root / "zeta.txt").read_text() == "zzz"


def test_rename_case_only(project_root, tmp_path):
    # Probe: two case-variant names hitting the same entry means the
    # filesystem is case-insensitive (macOS default). On a
    # case-sensitive fs the plain rename path already covers this.
    probe = tmp_path / "CaseProbe.txt"
    probe.write_text("x", encoding="utf-8")
    insensitive = (tmp_path / "caseprobe.txt").exists()
    probe.unlink()
    if not insensitive:
        pytest.skip("case-sensitive filesystem: case-only rename is a plain rename")
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "apple.txt",
                 "new_path": "Apple.txt"})["data"]
    assert data["ok"] is True
    names = os.listdir(project_root)
    assert "Apple.txt" in names and "apple.txt" not in names
    assert not any(".casetmp." in n for n in names)  # no temp residue
    assert (project_root / "Apple.txt").read_text() == "aaa"


def test_rename_escape_either_side(project_root):
    # Escaping source.
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "../outside/secret.txt",
                 "new_path": "stolen.txt"})["data"]
    assert data["error"] == "path escapes project root"
    # Escaping destination.
    data = _run(ws_files.handle_project_file_rename,
                {"project_id": "p1", "path": "apple.txt",
                 "new_path": "../outside/leaked.txt"})["data"]
    assert data["error"] == "path escapes project root"
    assert (project_root / "apple.txt").read_text() == "aaa"
    assert (project_root.parent / "outside" / "secret.txt").read_text() == "secret"


# ---- project_file_copy ------------------------------------------------------

def test_copy_file(project_root):
    frame = _run(ws_files.handle_project_file_copy,
                 {"project_id": "p1", "path": "apple.txt",
                  "new_path": "src/apple_copy.txt"})
    assert frame["type"] == "project_file_copy_result"
    assert frame["data"]["ok"] is True
    assert (project_root / "apple.txt").read_text() == "aaa"  # source kept
    assert (project_root / "src" / "apple_copy.txt").read_text() == "aaa"


def test_copy_dir_recursive(project_root):
    data = _run(ws_files.handle_project_file_copy,
                {"project_id": "p1", "path": "src",
                 "new_path": "src_copy"})["data"]
    assert data["ok"] is True
    assert (project_root / "src_copy" / "x.py").read_text() == "print('hi')\n"
    assert (project_root / "src" / "x.py").exists()  # source kept


def test_copy_destination_exists(project_root):
    data = _run(ws_files.handle_project_file_copy,
                {"project_id": "p1", "path": "apple.txt",
                 "new_path": "zeta.txt"})["data"]
    assert data["error"] == "destination already exists: 'zeta.txt'"
    assert (project_root / "zeta.txt").read_text() == "zzz"  # untouched


def test_copy_source_missing(project_root):
    data = _run(ws_files.handle_project_file_copy,
                {"project_id": "p1", "path": "nope.txt",
                 "new_path": "copy.txt"})["data"]
    assert data["error"] == "source does not exist: 'nope.txt'"


def test_copy_escape_either_side(project_root):
    data = _run(ws_files.handle_project_file_copy,
                {"project_id": "p1", "path": "../outside/secret.txt",
                 "new_path": "stolen.txt"})["data"]
    assert data["error"] == "path escapes project root"
    assert not (project_root / "stolen.txt").exists()
    data = _run(ws_files.handle_project_file_copy,
                {"project_id": "p1", "path": "apple.txt",
                 "new_path": "../outside/leaked.txt"})["data"]
    assert data["error"] == "path escapes project root"
    assert not (project_root.parent / "outside" / "leaked.txt").exists()


# ---- project_file_delete ----------------------------------------------------

def test_delete_file(project_root):
    frame = _run(ws_files.handle_project_file_delete,
                 {"project_id": "p1", "path": "apple.txt"})
    assert frame["type"] == "project_file_delete_result"
    assert frame["data"]["ok"] is True
    assert not (project_root / "apple.txt").exists()


def test_delete_dir_recursive(project_root):
    data = _run(ws_files.handle_project_file_delete,
                {"project_id": "p1", "path": "src"})["data"]
    assert data["ok"] is True
    assert not (project_root / "src").exists()


def test_delete_project_root_refused(project_root):
    # "" and "." and "src/.." all resolve to the root itself.
    for path in ("", ".", "src/.."):
        data = _run(ws_files.handle_project_file_delete,
                    {"project_id": "p1", "path": path})["data"]
        assert data["error"] == "refusing to delete project root", path
    assert project_root.is_dir()
    assert (project_root / "apple.txt").read_text() == "aaa"


def test_delete_missing(project_root):
    data = _run(ws_files.handle_project_file_delete,
                {"project_id": "p1", "path": "nope.txt"})["data"]
    assert data["error"] == "does not exist: 'nope.txt'"


def test_delete_escape_rejected(project_root):
    data = _run(ws_files.handle_project_file_delete,
                {"project_id": "p1", "path": "../outside/secret.txt"})["data"]
    assert data["error"] == "path escapes project root"
    assert (project_root.parent / "outside" / "secret.txt").read_text() == "secret"


# ---- project_file_reveal ----------------------------------------------------

@pytest.fixture
def popen_spy(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws_files.subprocess, "Popen",
                        lambda argv: calls.append(argv))
    return calls


def test_reveal_per_platform(project_root, popen_spy, monkeypatch):
    target = os.path.realpath(str(project_root / "apple.txt"))
    for platform, argv in (
        ("darwin", ["open", "-R", target]),
        ("win32", ["explorer", "/select," + target]),
        ("linux", ["xdg-open", os.path.dirname(target)]),
    ):
        popen_spy.clear()
        monkeypatch.setattr(ws_files.sys, "platform", platform)
        frame = _run(ws_files.handle_project_file_reveal,
                     {"project_id": "p1", "path": "apple.txt"})
        assert frame["type"] == "project_file_reveal_result"
        assert frame["data"]["ok"] is True
        assert popen_spy == [argv], platform


def test_reveal_dir_on_linux_opens_dir_itself(project_root, popen_spy, monkeypatch):
    monkeypatch.setattr(ws_files.sys, "platform", "linux")
    data = _run(ws_files.handle_project_file_reveal,
                {"project_id": "p1", "path": "src"})["data"]
    assert data["ok"] is True
    assert popen_spy == [["xdg-open", os.path.realpath(str(project_root / "src"))]]


def test_reveal_escape_rejected(project_root, popen_spy):
    data = _run(ws_files.handle_project_file_reveal,
                {"project_id": "p1", "path": "../outside/secret.txt"})["data"]
    assert data["error"] == "path escapes project root"
    assert popen_spy == []  # nothing launched


def test_reveal_missing(project_root, popen_spy):
    data = _run(ws_files.handle_project_file_reveal,
                {"project_id": "p1", "path": "nope.txt"})["data"]
    assert data["error"] == "does not exist: 'nope.txt'"
    assert popen_spy == []


def test_reveal_launch_failure_reported_not_raised(project_root, monkeypatch):
    def boom(argv):
        raise OSError("no file manager")
    monkeypatch.setattr(ws_files.subprocess, "Popen", boom)
    data = _run(ws_files.handle_project_file_reveal,
                {"project_id": "p1", "path": "apple.txt"})["data"]
    assert "no file manager" in data["error"]
    assert "ok" not in data


# ---- GET /files/raw --------------------------------------------------------

@pytest.fixture
def client(project_root):
    from openprogram.webui.server import create_app
    # 不进 lifespan（不用 with）——只测路由，避免启动钩子副作用。
    app = create_app()
    return TestClient(
        app,
        base_url="http://127.0.0.1:18100",
        headers={"Authorization": f"Bearer {app.state.owner_auth.token}"},
    )


def test_raw_image_inline_with_hardening_headers(client, project_root):
    (project_root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    r = client.get("/files/raw", params={"project_id": "p1", "path": "pic.png"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG\r\n\x1a\nfakepng"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert "content-disposition" not in r.headers  # inline, <img> needs it


def test_raw_non_image_is_octet_stream_attachment(client, project_root):
    (project_root / "page.html").write_text("<script>alert(1)</script>",
                                            encoding="utf-8")
    r = client.get("/files/raw", params={"project_id": "p1", "path": "page.html"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == 'attachment; filename="page.html"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.content == b"<script>alert(1)</script>"


def test_raw_pdf_inline_no_sandbox(client, project_root):
    (project_root / "doc.pdf").write_bytes(b"%PDF-1.4 fake body")
    r = client.get("/files/raw", params={"project_id": "p1", "path": "doc.pdf"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    # Inline (no attachment) and no CSP sandbox — a sandboxed response
    # blocks the browser's built-in PDF viewer.
    assert "content-disposition" not in r.headers
    assert "content-security-policy" not in r.headers


def test_raw_escape_403(client, project_root):
    r = client.get("/files/raw",
                   params={"project_id": "p1", "path": "../outside/secret.txt"})
    assert r.status_code == 403
    r = client.get("/files/raw", params={"project_id": "p1", "path": "sneaky_link"})
    assert r.status_code == 403


def test_raw_missing_404(client, project_root):
    r = client.get("/files/raw", params={"project_id": "p1", "path": "nope.txt"})
    assert r.status_code == 404


def test_raw_unknown_project_404(client, project_root):
    r = client.get("/files/raw", params={"project_id": "ghost", "path": "apple.txt"})
    assert r.status_code == 404

"""Project files panel backend — ws_actions.files + GET /files/raw.

Covers the wire contract:

* ``project_file_tree`` — dirs-first case-insensitive listing, dotfiles in
* ``project_file_read`` — text content, ``binary`` / ``too_large`` flags,
  whitespace filenames round-trip un-stripped
* ``_resolve`` guard — ``..``, absolute paths (even inside the root) and
  symlink escapes rejected, unknown project rejected
* ``GET /files/raw`` — nosniff + CSP sandbox always; images keep the real
  content-type, everything else is octet-stream + attachment; 403 on
  escape, 404 on missing file / unknown project
"""
from __future__ import annotations

import asyncio
import json
import os
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.store import project_store
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


# ---- GET /files/raw --------------------------------------------------------

@pytest.fixture
def client(project_root):
    from openprogram.webui.server import create_app
    # 不进 lifespan（不用 with）——只测路由，避免启动钩子副作用。
    return TestClient(create_app())


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

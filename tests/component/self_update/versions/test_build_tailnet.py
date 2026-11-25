"""Every sealed version carries the embedded Tailscale sidecar in its bin/, so a command-line install can open from anywhere."""
import os
from pathlib import Path
import sys

import pytest

from openprogram.operations.self_update.versions import build as builder

NAME = "openprogram-tailnet" + (".exe" if os.name == "nt" else "")


@pytest.fixture
def worktree(tmp_path):
    root = tmp_path / "worktree"
    script = root / "scripts" / "release" / "build-tailnet.py"
    script.parent.mkdir(parents=True)
    # Stands in for the Go build: writes this machine's binary where the real one does.
    script.write_text(
        "from pathlib import Path\n"
        "out = Path(__file__).resolve().parents[2] / 'apps' / 'tailnet' / 'dist' / 'here-arch' / "
        f"{NAME!r}\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_bytes(b'new')\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def go_on_path(tmp_path):
    bin_dir = tmp_path / "gobin"
    bin_dir.mkdir()
    go = bin_dir / ("go.exe" if os.name == "nt" else "go")
    go.write_text("#!/bin/sh\n", encoding="utf-8")
    go.chmod(0o755)
    return {"PATH": str(bin_dir)}


def _previous(tmp_path):
    version = tmp_path / "previous"
    (version / "bin").mkdir(parents=True)
    (version / "bin" / NAME).write_bytes(b"old")
    return version


def test_a_fresh_version_builds_the_sidecar_with_go(tmp_path, worktree, go_on_path):
    staging = tmp_path / "staging"
    builder._stage_tailnet(worktree, staging, None, None, go_on_path)
    assert (staging / "bin" / NAME).read_bytes() == b"new"


def test_an_unchanged_sidecar_is_kept_from_the_parent(tmp_path, worktree, go_on_path):
    staging = tmp_path / "staging"
    builder._stage_tailnet(worktree, staging, _previous(tmp_path), ["openprogram/x.py"], go_on_path)
    assert (staging / "bin" / NAME).read_bytes() == b"old"


def test_a_changed_sidecar_is_rebuilt(tmp_path, worktree, go_on_path):
    staging = tmp_path / "staging"
    builder._stage_tailnet(worktree, staging, _previous(tmp_path), ["apps/tailnet/main.go"], go_on_path)
    assert (staging / "bin" / NAME).read_bytes() == b"new"


def test_without_go_the_parents_copy_carries_over(tmp_path, worktree):
    staging = tmp_path / "staging"
    builder._stage_tailnet(worktree, staging, _previous(tmp_path), ["apps/tailnet/main.go"], {"PATH": ""})
    assert (staging / "bin" / NAME).read_bytes() == b"old"


def test_without_go_or_a_parent_the_version_simply_has_none(tmp_path, worktree):
    staging = tmp_path / "staging"
    builder._stage_tailnet(worktree, staging, None, None, {"PATH": ""})
    assert not (staging / "bin").exists()


def test_the_worker_finds_the_sidecar_in_its_versions_bin(tmp_path, monkeypatch):
    from openprogram.host.security import remote_access

    version = tmp_path / "version"
    (version / "venv").mkdir(parents=True)
    (version / "bin").mkdir()
    sidecar = version / "bin" / NAME
    sidecar.write_bytes(b"x")
    monkeypatch.delenv("OPENPROGRAM_TAILNET_BIN", raising=False)
    monkeypatch.setattr(remote_access, "_runtime_roots", lambda: [])
    monkeypatch.setattr(sys, "prefix", str(version / "venv"))
    assert remote_access.binary_path() == sidecar

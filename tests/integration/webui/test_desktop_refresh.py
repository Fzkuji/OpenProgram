"""Exercise the Desktop archive packer both installed Apps use."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASAR = ROOT / "node_modules/@electron/asar/bin/asar.js"


def _repository(tmp_path: Path) -> Path:
    """A checkout holding only the packer, its helper and a small Desktop shell."""
    if os.name == "nt" or not shutil.which("node") or not ASAR.is_file():
        pytest.skip("Requires POSIX, Node and npm ci --ignore-scripts")
    repo = tmp_path / "repo"
    release = repo / "scripts/release"
    release.mkdir(parents=True)
    for name in ("pack-desktop-archive.sh", "restore-asar-permissions.py"):
        shutil.copy2(ROOT / "scripts/release" / name, release / name)
    (repo / "node_modules").symlink_to(ROOT / "node_modules")
    desktop = repo / "apps/desktop"
    (desktop / "main").mkdir(parents=True)
    (desktop / "main.js").write_text("entry", encoding="utf-8")
    (desktop / "main/actions.js").write_text("nested module", encoding="utf-8")
    (desktop / "package.json").write_text(
        json.dumps({"build": {"files": ["main.js", "main/**"]}}), encoding="utf-8"
    )
    return repo


def _pack(repo: Path, installed: Path, output: Path) -> None:
    subprocess.run(
        ["bash", str(repo / "scripts/release/pack-desktop-archive.sh"),
         str(installed), str(output.parent / "stage"), str(output)],
        env={**os.environ, "OPENPROGRAM_LOCAL_PYTHON": sys.executable},
        check=True, capture_output=True, text=True,
    )


def _archive(source: Path, archive: Path) -> None:
    subprocess.run(["node", str(ASAR), "pack", str(source), str(archive),
                    "--unpack-dir", "node_modules/node-pty"], check=True)


def test_refresh_copies_declared_nested_desktop_modules(tmp_path):
    repo = _repository(tmp_path)
    source = tmp_path / "source"
    (source / "main").mkdir(parents=True)
    (source / "main/removed.js").write_text("old", encoding="utf-8")
    (source / "stale-top-level.js").write_text("old", encoding="utf-8")
    installed = tmp_path / "installed.asar"
    _archive(source, installed)
    output = tmp_path / "out/app.asar"
    output.parent.mkdir()
    _pack(repo, installed, output)
    extracted = tmp_path / "extracted"
    subprocess.run(["node", str(ASAR), "extract", str(output), str(extracted)], check=True)
    assert (extracted / "main/actions.js").read_text(encoding="utf-8") == "nested module"
    assert (extracted / "main.js").read_text(encoding="utf-8") == "entry"
    assert not (extracted / "main/removed.js").exists()
    assert not (extracted / "stale-top-level.js").exists()


def test_refresh_preserves_and_repairs_unpacked_permissions(tmp_path):
    repo = _repository(tmp_path)
    source = tmp_path / "source"
    helper = source / "node_modules/node-pty/prebuilds/darwin-arm64/spawn-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nprintf helper-ok")
    helper.chmod(0o755)
    data = helper.with_name("data.json")
    data.write_text("{}")
    data.chmod(0o640)
    installed = tmp_path / "installed.asar"
    _archive(source, installed)
    installed_helper = Path(str(installed) + ".unpacked") / helper.relative_to(source)
    for damaged in (False, True):
        installed_helper.chmod(0o644 if damaged else 0o755)
        output = tmp_path / f"out-{damaged}/app.asar"
        output.parent.mkdir()
        _pack(repo, installed, output)
        unpacked = Path(str(output) + ".unpacked")
        result = subprocess.run([str(unpacked / helper.relative_to(source))],
                                capture_output=True, text=True, check=True)
        assert result.stdout == "helper-ok"
        assert (unpacked / data.relative_to(source)).stat().st_mode & 0o777 == 0o640


def test_refresh_gives_the_installed_package_json_the_source_version(tmp_path):
    # app.getVersion() reads the asar's package.json and the packaged worker
    # refuses a runtime of another version: a refresh to 1.0.0 that kept the
    # installed 0.9.8 there opened no window.
    repo = _repository(tmp_path)
    package = repo / "apps/desktop/package.json"
    package.write_text(json.dumps({**json.loads(package.read_text()), "version": "1.0.0"}), encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({"name": "openprogram-desktop", "version": "0.9.8", "main": "main.js"}), encoding="utf-8")
    installed = tmp_path / "installed.asar"
    _archive(source, installed)
    output = tmp_path / "out/app.asar"
    output.parent.mkdir()
    _pack(repo, installed, output)
    extracted = tmp_path / "extracted"
    subprocess.run(["node", str(ASAR), "extract", str(output), str(extracted)], check=True)
    staged = json.loads((extracted / "package.json").read_text(encoding="utf-8"))
    assert staged == {"name": "openprogram-desktop", "version": "1.0.0", "main": "main.js"}

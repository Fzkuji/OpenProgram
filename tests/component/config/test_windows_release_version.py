"""Installed Windows shell/runtime version agreement uses real Python metadata."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

from openprogram import _compat


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows release preflight")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    app = tmp_path / "O'Brien 应用"
    resources = app / "resources"
    runtime = resources / "runtime"
    python_root = runtime / "py"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(python_root)],
                   check=True, capture_output=True, timeout=30)
    (app / "OpenProgram.exe").write_bytes(b"fixture shell")
    (resources / "app.asar").write_bytes(b"fixture archive")
    manifest = runtime / "runtime-manifest.json"
    manifest.write_text(json.dumps({"python": "py/Scripts/python.exe", "openprogram": "0.8.1"}))
    metadata = python_root / "Lib" / "site-packages" / "openprogram-0.8.1.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Metadata-Version: 2.1\nName: openprogram\nVersion: 0.8.1\n")
    monkeypatch.setattr(_compat, "_windows_powershell", lambda *a, **kw: "0.8.1.0\n")
    verify = runpy.run_path(str(ROOT / "scripts/release/verify-release-version.py"))["_installed_app_version"]
    return app, manifest, metadata, verify


@pytest.mark.parametrize("executable_path", [False, True])
def test_installed_windows_version_reads_its_own_metadata(installed, executable_path):
    app, _, _, verify = installed
    assert verify(app / "OpenProgram.exe" if executable_path else app) == "0.8.1"


@pytest.mark.parametrize("change", ["manifest", "metadata", "missing", "escape", "revision", "no-version"])
def test_installed_windows_version_rejects_disagreement(installed, monkeypatch, change):
    app, manifest, metadata, verify = installed
    payload = json.loads(manifest.read_text())
    if change == "manifest":
        payload["openprogram"] = "0.8.2"
    elif change == "metadata":
        metadata.write_text("Metadata-Version: 2.1\nName: openprogram\nVersion: 0.8.2\n")
    elif change == "missing":
        payload["python"] = "py/missing.exe"
    elif change == "escape":
        payload["python"] = "../../outside.exe"
    else:
        monkeypatch.setattr(_compat, "_windows_powershell", lambda *a, **kw: "0.8.1.1" if change == "revision" else "")
    manifest.write_text(json.dumps(payload))
    with pytest.raises(SystemExit):
        verify(app)


def test_windows_bundle_reads_real_pe_metadata_without_launching(tmp_path):
    app = tmp_path / "O'Brien 应用"
    (app / "resources").mkdir(parents=True)
    (app / "resources" / "app.asar").write_bytes(b"fixture archive")
    # cmd has native PE version resources. Copying it to an Electron-shaped
    # fixture tests path quoting and the actual read-only Windows API.
    shutil.copy2(shutil.which("cmd.exe"), app / "OpenProgram.exe")
    resources, version = _compat.desktop_bundle_metadata(app)
    assert resources == app / "resources"
    assert version and all(part.isdecimal() for part in version.split("."))

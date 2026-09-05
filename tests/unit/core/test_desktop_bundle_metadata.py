"""Bundle layout detection keeps version preflight portable and read-only."""
import plistlib

import pytest

from openprogram import _compat


def test_macos_bundle_metadata_does_not_require_a_macos_host(tmp_path, monkeypatch):
    contents = tmp_path / "OpenProgram.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.8.1"}))
    monkeypatch.setattr(_compat, "_windows_powershell", lambda *a, **kw: pytest.fail("unexpected Windows probe"))
    assert _compat.desktop_bundle_metadata(contents.parent) == (contents / "Resources", "0.8.1")


@pytest.mark.parametrize("native,expected", [("0.8.1.0", "0.8.1"), ("0.8.1.2", "0.8.1.2"), ("0.8.1", "0.8.1")])
def test_windows_bundle_version_keeps_nonzero_revision(tmp_path, monkeypatch, native, expected):
    (tmp_path / "OpenProgram.exe").write_bytes(b"fixture")
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "app.asar").write_bytes(b"fixture")
    monkeypatch.setattr(_compat, "_windows_powershell", lambda *a, **kw: native)
    assert _compat.desktop_bundle_metadata(tmp_path) == (resources, expected)

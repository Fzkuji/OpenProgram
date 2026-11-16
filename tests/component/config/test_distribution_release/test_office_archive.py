"""Every release carries the pinned Office archive, or it does not start building.

The App downloads Office from its own version's release
(``office_editor/install.py``). The archive cannot be rebuilt in CI, so the
Release workflow carries the newest release's copy forward, or the copy the
owner put on the tag's draft, and stops before any build when neither exists.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import urllib.error

import pytest

from ._support import ROOT

SCRIPT = ROOT / "scripts/release/office-archive.py"


@pytest.fixture
def office() -> dict:
    return runpy.run_path(str(SCRIPT))


def _pinned(data: bytes) -> dict[str, object]:
    return {
        "OFFICE_RELEASES": "https://github.com/Owner/Repo/releases",
        "OFFICE_ARCHIVE_NAME": "OfficeAssets-test.zip",
        "OFFICE_ARCHIVE_SHA256": hashlib.sha256(data).hexdigest(),
        "OFFICE_DOWNLOAD_BYTES": len(data),
    }


def test_the_script_reads_the_pins_the_app_verifies(office) -> None:
    from openprogram.programs.applications.office_editor import install

    assert office["pins"]() == {
        "OFFICE_RELEASES": install.OFFICE_RELEASES,
        "OFFICE_ARCHIVE_NAME": install.OFFICE_ARCHIVE_NAME,
        "OFFICE_ARCHIVE_SHA256": install.OFFICE_ARCHIVE_SHA256,
        "OFFICE_DOWNLOAD_BYTES": install.OFFICE_DOWNLOAD_BYTES,
    }


def test_fetch_carries_forward_only_the_pinned_bytes(office, tmp_path: Path) -> None:
    data = b"office archive"
    pinned = _pinned(data)
    asked = []

    def opener(request, timeout):
        asked.append(request.full_url)
        return io.BytesIO(data)

    target = tmp_path / "out" / "OfficeAssets-test.zip"
    assert office["fetch"](target, pinned, opener) == 0
    assert target.read_bytes() == data
    assert asked == ["https://github.com/Owner/Repo/releases/latest/download/OfficeAssets-test.zip"]

    with pytest.raises(SystemExit, match="not the pinned archive"):
        office["fetch"](target, pinned, lambda request, timeout: io.BytesIO(b"other bytes!!!"))
    assert not target.exists()


def test_fetch_says_when_the_newest_release_has_none(office, tmp_path: Path) -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    assert office["fetch"](tmp_path / "x.zip", _pinned(b"x"), opener) == office["NOT_PUBLISHED"]


def test_verify_names_the_file_and_the_seed_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", str(tmp_path / "absent.zip"), "--tag", "v1.3.0"],
        capture_output=True, text=True,
    )
    from openprogram.programs.applications.office_editor import install

    assert result.returncode == 1
    assert result.stdout.startswith("::error::v1.3.0 cannot be released without the Office archive")
    for fact in (install.OFFICE_ARCHIVE_NAME, str(install.OFFICE_DOWNLOAD_BYTES), install.OFFICE_ARCHIVE_SHA256,
                 "python scripts/release/office-archive.py seed v1.3.0"):
        assert fact in result.stdout


def test_seed_puts_a_verified_file_on_the_tags_draft_only(office, tmp_path: Path) -> None:
    data = b"office archive"
    pinned = _pinned(data)
    archive = tmp_path / "OfficeAssets-test.zip"
    archive.write_bytes(data)
    commands = []

    def run(command, **options):
        commands.append(command)
        if command[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(command, 1, "", "release not found")
        return subprocess.CompletedProcess(command, 0, "", "")

    assert office["seed"]("v1.3.0", archive, pinned, run) == 0
    assert commands[-1][:6] == ["gh", "release", "create", "v1.3.0", str(archive), "--draft"]

    def public(command, **options):
        return subprocess.CompletedProcess(command, 0, "false\n", "")

    with pytest.raises(SystemExit, match="already public"):
        office["seed"]("v1.2.0", archive, pinned, public)
    archive.write_bytes(b"something else")
    with pytest.raises(SystemExit, match="not the pinned Office archive"):
        office["seed"]("v1.3.0", archive, pinned, run)


def test_the_workflow_finds_the_archive_before_building_and_checks_the_public_release() -> None:
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    # Every build waits for the archive: platforms needs it and everything needs platforms.
    assert jobs["platforms"]["needs"] == "office"
    assert "office" in jobs["publish"]["needs"]
    steps = jobs["office"]["steps"]
    fetch = next(step for step in steps if step.get("name") == "Fetch the Office archive")["run"]
    assert fetch.index("office-archive.py fetch") < fetch.index("gh release download") < fetch.index(
        "office-archive.py verify")
    upload = next(step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == "office-assets" and upload["with"]["path"] == "office-assets/*.zip"
    verify = next(step for step in jobs["publish"]["steps"]
                  if step.get("name") == "Verify public latest Release entry")["run"]
    assert '[ "$office_count" = 1 ]' in verify


def test_released_asks_the_newest_release_then_the_tags_draft(office) -> None:
    pinned = _pinned(b"x")
    asked = []

    def gh(answers):
        def run(command, **options):
            asked.append(command)
            code, names = answers[len(asked) - 1]
            return subprocess.CompletedProcess(command, code, names, "")
        return run

    assert office["released"]("v1.3.0", pinned, gh([(0, "OfficeAssets-test.zip\nother.zip\n")]))
    assert asked[0][:3] == ["gh", "release", "view"] and asked[0][3] == "--json"
    asked.clear()
    assert office["released"]("v1.3.0", pinned, gh([(0, "other.zip\n"), (0, "OfficeAssets-test.zip\n")]))
    assert asked[1][:4] == ["gh", "release", "view", "v1.3.0"]
    asked.clear()
    assert not office["released"]("v1.3.0", pinned, gh([(0, "other.zip\n"), (1, "")]))


def test_pack_zips_a_prepared_pack_the_app_installs_and_pins_it(
    office, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    import openprogram.programs.applications.office_editor.install as installer
    from openprogram.programs.applications.office_editor.assets import install_office_pack
    from tests.component.config.office_editor.test_office_packaging import make_office_pack

    prepared = tmp_path / "prepared"
    install_office_pack(make_office_pack(tmp_path / "pack"), prepared)  # what prepare.py publishes
    archive = office["pack"](prepared, tmp_path / "out")
    assert archive.name.startswith("OfficeAssets-d15d12b-dc31dd9d-") and archive.suffix == ".zip"
    again = office["pack"](prepared, tmp_path / "again")
    assert again.name == archive.name and again.read_bytes() == archive.read_bytes(), "the same pack, the same bytes"

    install = tmp_path / "install.py"
    safe_http = tmp_path / "safe_http.py"
    shutil.copy2(office["INSTALL"], install)
    shutil.copy2(office["SAFE_HTTP"], safe_http)
    written = office["write_pins"](archive, install, safe_http)
    data = archive.read_bytes()
    assert office["pins"](install) == {**office["pins"](), **written}
    assert written == {"OFFICE_ARCHIVE_NAME": archive.name, "OFFICE_ARCHIVE_SHA256": hashlib.sha256(data).hexdigest(),
                       "OFFICE_DOWNLOAD_BYTES": len(data)}
    assert f"max_decoded_body_bytes={len(data)}, deadline_covers_body=False" in safe_http.read_text(encoding="utf-8")

    # The App takes these bytes under the new pins.
    cache = tmp_path / "cache" / "office"
    monkeypatch.setattr(installer, "prepared_office_cache", lambda: cache)
    monkeypatch.setattr(installer, "OFFICE_DOWNLOAD_BYTES", len(data))
    monkeypatch.setattr(installer, "OFFICE_ARCHIVE_SHA256", written["OFFICE_ARCHIVE_SHA256"])
    monkeypatch.setattr(installer.safe_http, "safe_client", lambda *a, **k: httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data))))
    assert installer.download_office_pack().available


def test_the_download_limit_is_the_pinned_size() -> None:
    from openprogram.host.security import safe_http
    from openprogram.programs.applications.office_editor import install

    assert safe_http.CONSUMER_REGISTRY["office.assets"].max_decoded_body_bytes == install.OFFICE_DOWNLOAD_BYTES


def test_check_says_whether_a_local_copy_is_the_pinned_archive(tmp_path: Path) -> None:
    from openprogram.programs.applications.office_editor import install

    copy = tmp_path / install.OFFICE_ARCHIVE_NAME
    copy.write_bytes(b"not it")
    result = subprocess.run([sys.executable, str(SCRIPT), "check", str(copy)], capture_output=True, text=True)
    assert result.returncode == 1
    assert f"is not the pinned {install.OFFICE_ARCHIVE_NAME} ({install.OFFICE_DOWNLOAD_BYTES} bytes" in result.stderr
    assert f"it has 6 bytes, not {install.OFFICE_DOWNLOAD_BYTES}" in result.stderr

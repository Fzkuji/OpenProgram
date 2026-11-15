from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "scripts/release/office/prepare.py"


def test_prepare_public_cli_rejects_unpinned_source_and_preserves_ready_pack(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
    (source / "README").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
        check=True,
    )
    output = tmp_path / "office"
    output.mkdir()
    (output / "previous-ready-bytes").write_bytes(b"keep")
    result = subprocess.run(
        ["python3", str(PREPARE), "--source", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "source revision mismatch" in result.stderr
    assert (output / "previous-ready-bytes").read_bytes() == b"keep"


def test_product_manifest_declares_reviewed_office_inputs():
    config = json.loads((ROOT / "scripts/release/product-runtime.json").read_text(encoding="utf-8"))
    office = config["office"]
    assert office["packageVersion"] == "0.3.34"
    assert office["source"] == "d15d12b6945be4d8b0f3aa1806120e740d2950ee"
    assert office["adoptionPatchSha256"] == hashlib.sha256(
        (ROOT / "scripts/release/office/adoption.patch").read_bytes()
    ).hexdigest()
    assert office["reviewedNpmLockSha256"] == hashlib.sha256(
        (ROOT / "scripts/release/office/package-lock.json").read_bytes()
    ).hexdigest()


def make_office_pack(root: Path, marker: bytes = b"first") -> Path:
    from openprogram.programs.applications.office_editor.assets import (
        OFFICE_FONT_MANIFEST_DIGEST, OFFICE_HOST_BUILD_ID, OFFICE_LOCK_SHA256, OFFICE_PATCH_SHA256, OFFICE_SOURCE,
    )
    root.mkdir(parents=True)
    resources = {path: marker for path in (
        "office-host.html", "reset.html", "sw.js", "document_editor_service_worker.js",
        "plugins.json", "themes.json", "onlyoffice-runtime-assets.json", "LICENSE", "npm/public-api.js",
    )}
    for relative, content in resources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {
        "version": 1, "source": OFFICE_SOURCE, "packageVersion": "0.3.34",
        "hostBuildId": OFFICE_HOST_BUILD_ID,
        "expectedHostIdentity": hashlib.sha256(marker).hexdigest(),
        "assembly": {"adoptionPatchSha256": OFFICE_PATCH_SHA256, "reviewedNpmLockSha256": OFFICE_LOCK_SHA256,
                     "fonts": {"manifestDigest": OFFICE_FONT_MANIFEST_DIGEST}},
        "licenses": ["LICENSE"],
        "assets": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                   for name, content in resources.items()],
    }
    (root / "openprogram-office-assets.json").write_text(json.dumps(manifest))
    return root


def test_public_install_repeatedly_selects_complete_version(tmp_path: Path):
    import sys
    from openprogram.programs.applications.office_editor.assets import validate_prepared_office_pack
    source = make_office_pack(tmp_path / "source")
    target = tmp_path / "installed"
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "openprogram.programs.applications.office_editor.assets", "install", "--source", str(source), "--target", str(target)], check=True)
    first = validate_prepared_office_pack(target).root
    assert (first / "office-host.html").read_bytes() == b"first"
    assert len(list((target / "versions").iterdir())) == 1
    second = make_office_pack(tmp_path / "second", b"second")
    subprocess.run([sys.executable, "-m", "openprogram.programs.applications.office_editor.assets", "install", "--source", str(second), "--target", str(target)], check=True)
    assert (validate_prepared_office_pack(target).root / "office-host.html").read_bytes() == b"second"
    assert (first / "office-host.html").read_bytes() == b"first"
    assert not (target / "office").exists()


def test_failed_install_keeps_previous_selected_bytes(tmp_path: Path, monkeypatch):
    import pytest
    import openprogram.programs.applications.office_editor.assets as assets
    source = make_office_pack(tmp_path / "source")
    target = tmp_path / "installed"
    assets.install_office_pack(source, target)
    pointer = (target / "current.json").read_bytes()
    source = make_office_pack(tmp_path / "second", b"second")
    def interrupted_copy(*args, **kwargs):
        raise OSError("disk full during copy")
    monkeypatch.setattr(assets.shutil, "copyfileobj", interrupted_copy)
    with pytest.raises(OSError, match="disk full"):
        assets.install_office_pack(source, target)
    assert (target / "current.json").read_bytes() == pointer
    assert (assets.validate_prepared_office_pack(target).root / "office-host.html").read_bytes() == b"first"
    assert not list((target / "versions").glob(".staging-*"))


def test_source_worker_uses_verified_profile_cache_not_runtime_environment(tmp_path: Path, monkeypatch):
    from openprogram.programs.applications.office_editor import host as server
    from openprogram.programs.applications.office_editor.assets import install_office_pack
    source = make_office_pack(tmp_path / "source")
    cache = tmp_path / "profile-cache"
    install_office_pack(source, cache)
    monkeypatch.setattr(server, "prepared_office_cache", lambda: cache)
    monkeypatch.setenv("OPENPROGRAM_RUNTIME_ROOT", str(tmp_path / "arbitrary"))
    assert server.load_installed_office_pack().available
    (cache / "current.json").write_text('{"version":"../../other"}')
    assert not server.load_installed_office_pack().available


def test_tampered_asset_is_rejected_before_replacing_install(tmp_path: Path):
    import pytest
    from openprogram.programs.applications.office_editor.assets import install_office_pack
    source = make_office_pack(tmp_path / "source")
    target = tmp_path / "installed"
    install_office_pack(source, target)
    previous = (target / "current.json").read_bytes()
    (source / "LICENSE").write_bytes(b"altered license")
    with pytest.raises(ValueError):
        install_office_pack(source, target)
    assert (target / "current.json").read_bytes() == previous


def test_release_stager_verifies_and_copies_lazy_parent_module(tmp_path: Path):
    import sys
    from openprogram.programs.applications.office_editor.assets import OFFICE_PATCH_SHA256, validate_prepared_office_pack
    source = make_office_pack(tmp_path / "source", b"parent module")
    target = tmp_path / "build-office"
    web = tmp_path / "web-public"
    subprocess.run([sys.executable, str(ROOT / "scripts/release/office/stage.py"),
                    "--source", str(source), "--output", str(target), "--web-root", str(web)], check=True)
    assert validate_prepared_office_pack(target).available
    assert (web / "document-assets" / "office" / OFFICE_PATCH_SHA256 / "public-api.js").read_bytes() == b"parent module"
    (source / "npm/public-api.js").write_bytes(b"corrupted")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/release/office/stage.py"),
                             "--source", str(source), "--output", str(target)], capture_output=True)
    assert result.returncode != 0
    assert (validate_prepared_office_pack(target).root / "npm/public-api.js").read_bytes() == b"parent module"


def test_install_rejects_unreviewed_font_generation(tmp_path: Path):
    import pytest
    from openprogram.programs.applications.office_editor.assets import install_office_pack
    source = make_office_pack(tmp_path / 'source')
    manifest_path = source / 'openprogram-office-assets.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['assembly']['fonts'] = {'manifestDigest': '0' * 64, 'fonts': 188}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='font'):
        install_office_pack(source, tmp_path / 'target')
    assert not (tmp_path / 'target/current.json').exists()


def test_malformed_install_pointer_returns_unavailable(tmp_path: Path):
    from openprogram.programs.applications.office_editor.assets import OfficeAssetPack
    target = tmp_path / 'installed'
    target.mkdir()
    (target / 'current.json').write_text('[]')
    pack = OfficeAssetPack.from_root(target)
    assert not pack.available
    assert pack.unavailable_reason


def test_symlink_pack_root_is_unavailable(tmp_path: Path):
    import pytest
    from openprogram.programs.applications.office_editor.assets import OfficeAssetPack, install_office_pack
    source = make_office_pack(tmp_path / 'source')
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    assert not OfficeAssetPack.from_root(alias).available
    with pytest.raises(ValueError):
        install_office_pack(alias, tmp_path / 'target')
    assert not (tmp_path / 'target/current.json').exists()


def test_optional_download_verifies_before_publish_and_cleans_temporary_files(tmp_path, monkeypatch):
    import io
    import zipfile
    import openprogram.programs.applications.office_editor.install as installer
    source = make_office_pack(tmp_path / 'source')
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        for path in source.rglob('*'):
            if path.is_file(): archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    data = content.getvalue()
    cache = tmp_path / 'cache' / 'office'
    monkeypatch.setattr(installer, 'prepared_office_cache', lambda: cache)
    monkeypatch.setattr(installer, 'OFFICE_DOWNLOAD_BYTES', len(data))
    monkeypatch.setattr(installer, 'OFFICE_ARCHIVE_SHA256', hashlib.sha256(data).hexdigest())
    import httpx
    monkeypatch.setattr(installer.safe_http, 'safe_client', lambda *a, **k: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=data))))
    assert installer.download_office_pack().available
    previous = (cache / 'current.json').read_bytes()
    monkeypatch.setattr(installer, 'OFFICE_ARCHIVE_SHA256', '0' * 64)
    import pytest
    with pytest.raises(ValueError, match='verification'):
        installer.download_office_pack()
    assert (cache / 'current.json').read_bytes() == previous
    assert not list(cache.parent.glob('.office-install-*'))


def test_optional_download_rejects_traversal_before_install(tmp_path, monkeypatch):
    import io
    import zipfile
    import pytest
    import openprogram.programs.applications.office_editor.install as installer
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('../escape', b'unsafe')
    data = content.getvalue()
    cache = tmp_path / 'cache' / 'office'
    monkeypatch.setattr(installer, 'prepared_office_cache', lambda: cache)
    monkeypatch.setattr(installer, 'OFFICE_DOWNLOAD_BYTES', len(data))
    monkeypatch.setattr(installer, 'OFFICE_ARCHIVE_SHA256', hashlib.sha256(data).hexdigest())
    import httpx
    monkeypatch.setattr(installer.safe_http, 'safe_client', lambda *a, **k: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=data))))
    with pytest.raises(ValueError, match='path'):
        installer.download_office_pack()
    assert not cache.exists()
    assert not list(cache.parent.glob('.office-install-*'))


def test_managed_worker_requires_separately_installed_office(tmp_path, monkeypatch):
    from openprogram.programs.applications.office_editor import host as server
    make_office_pack(tmp_path / 'runtime/assets/office')
    monkeypatch.setattr(server, 'prepared_office_cache', lambda: tmp_path / 'missing-cache')
    assert not server.load_installed_office_pack().available


def _office_archive(tmp_path):
    import io
    import zipfile
    source = make_office_pack(tmp_path / 'source')
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        for path in source.rglob('*'):
            if path.is_file(): archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    return content.getvalue()


def test_the_archive_is_asked_from_the_installed_versions_release_then_the_newest():
    from openprogram.programs.applications.office_editor.install import (
        OFFICE_ARCHIVE_NAME, OFFICE_RELEASES, office_download_urls)
    latest = f"{OFFICE_RELEASES}/latest/download/{OFFICE_ARCHIVE_NAME}"
    assert OFFICE_RELEASES == "https://github.com/FuzzyKuji/OpenProgram/releases"
    assert office_download_urls("1.3.0") == [f"{OFFICE_RELEASES}/download/v1.3.0/{OFFICE_ARCHIVE_NAME}", latest]
    # A build without a release of its own, or a bare checkout, asks only the newest release.
    assert office_download_urls("1.3.0.dev1") == [latest]
    assert office_download_urls(None) == [latest]


def test_a_version_without_its_own_copy_installs_the_newest_releases_verified_bytes(tmp_path, monkeypatch):
    import httpx
    import pytest
    import openprogram.programs.applications.office_editor.install as installer
    data = _office_archive(tmp_path)
    cache = tmp_path / 'cache' / 'office'
    monkeypatch.setattr(installer, 'prepared_office_cache', lambda: cache)
    monkeypatch.setattr(installer, 'installed_version', lambda: '1.4.0')
    monkeypatch.setattr(installer, 'OFFICE_DOWNLOAD_BYTES', len(data))
    monkeypatch.setattr(installer, 'OFFICE_ARCHIVE_SHA256', hashlib.sha256(data).hexdigest())
    asked = []
    def answer(served):
        def handler(request):
            asked.append(request.url.path)
            return httpx.Response(200, content=served) if '/latest/' in request.url.path else httpx.Response(404)
        return lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(installer.safe_http, 'safe_client', answer(data))
    assert installer.download_office_pack().available
    assert [path.split('/')[-2] for path in asked] == ['v1.4.0', 'download']
    previous = (cache / 'current.json').read_bytes()
    # The newest release's copy is held to the same pin as the version's own.
    monkeypatch.setattr(installer.safe_http, 'safe_client', answer(data[:-1] + b'x'))
    with pytest.raises(ValueError, match='verification'):
        installer.download_office_pack()
    assert (cache / 'current.json').read_bytes() == previous


def test_a_version_no_release_serves_says_which_update_brings_office(tmp_path, monkeypatch):
    import httpx
    import pytest
    import openprogram.programs.applications.office_editor.install as installer
    cache = tmp_path / 'cache' / 'office'
    monkeypatch.setattr(installer, 'prepared_office_cache', lambda: cache)
    monkeypatch.setattr(installer.safe_http, 'safe_client', lambda *a, **k: httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))))
    monkeypatch.setattr(installer, 'installed_version', lambda: '1.2.0')
    with pytest.raises(installer.OfficeArchiveUnpublished) as raised:
        installer.download_office_pack()
    assert raised.value.update_to == '1.3.0' and raised.value.version == '1.2.0'
    assert str(raised.value) == ('OpenProgram 1.2.0 cannot download Office support: releases before 1.3.0 '
                                 'do not include it. Update OpenProgram to 1.3.0 or newer, then install again.')
    assert not cache.exists() and not list(cache.parent.glob('.office-install-*'))
    monkeypatch.setattr(installer, 'installed_version', lambda: '1.3.4')
    with pytest.raises(installer.OfficeArchiveUnpublished) as raised:
        installer.download_office_pack()
    assert raised.value.update_to is None
    assert 'Update OpenProgram to the newest version' in str(raised.value)

"""Real ASAR build receipt and static controller admission, without Electron."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from openprogram.self_update.package_protocol import validate_reopen_package
from openprogram.self_update.supervisor import Artifact, _tree_digest
from tests.component.config.test_distribution_release import _fake_desktop_app

ROOT = Path(__file__).resolve().parents[3]
WRITER = ROOT / "apps/desktop/scripts/write-reopen-protocol.cjs"


@pytest.fixture
def package_factory(tmp_path):
    def build(name="fixture"):
        app = _fake_desktop_app(tmp_path / name, "0.6.2")
        resources = app / "Contents/Resources"
        unpacked = tmp_path / f"{name}-asar"
        unpacked.mkdir()
        for filename in ("main.js", "preload.js", "self-update-reopen.js"):
            shutil.copyfile(ROOT / "apps/desktop" / filename, unpacked / filename)
        subprocess.run(["node", "-e", "require('@electron/asar').createPackage(process.argv[1],process.argv[2]).catch(e=>{console.error(e);process.exit(1)})",
                        str(unpacked), str(resources / "app.asar")], cwd=ROOT, check=True, capture_output=True, timeout=15)
        site = resources / "runtime/python/lib/python3.12/site-packages"
        for target, source in (
            (site / "openprogram/self_update/reopen.py", ROOT / "openprogram/self_update/reopen.py"),
            (site / "openprogram_server/_webui/routes/self_updates.py", ROOT / "apps/server/openprogram_server/_webui/routes/self_updates.py"),
            (resources / "update/install-app.sh", ROOT / "apps/desktop/scripts/install-app.sh"),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        chunk = site / "openprogram_server/_webui/_frontend/_next/static/chunks/client.js"
        chunk.parent.mkdir(parents=True)
        chunk.write_text('window.openprogramDesktop.selfUpdateReopen.sessionLoaded("p1");')
        result = subprocess.run(["node", "-e", "require(process.argv[1]).default({electronPlatformName:'darwin',appOutDir:'fixture',packager:{getResourcesDir:()=>process.argv[2]}}).catch(e=>{console.error(e);process.exit(1)})",
                                 str(WRITER), str(resources)], cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        return app
    return build


def test_after_pack_binds_actual_bytes_and_validator_does_not_write(package_factory):
    app = package_factory()
    before = _tree_digest(app)
    value = validate_reopen_package(app)
    assert value["protocol"] == 1
    assert len(value["bindings"]) == 6
    assert value["bindings"]["desktop"]["path"] == "app.asar"
    assert _tree_digest(app) == before
    package = json.loads((ROOT / "apps/desktop/package.json").read_text())
    assert package["build"]["afterPack"] == "scripts/write-reopen-protocol.cjs"


@pytest.mark.parametrize("role", ["desktop", "installer", "backend", "routes", "frontend", "runtime_manifest"])
def test_changed_packaged_bytes_are_rejected(package_factory, role):
    app = package_factory()
    value = validate_reopen_package(app)
    target = app / "Contents/Resources" / value["bindings"][role]["path"]
    with target.open("ab") as stream:
        stream.write(b"\nchanged")
    with pytest.raises(ValueError, match="reopen protocol"):
        validate_reopen_package(app)


@pytest.mark.parametrize("change", ["schema", "protocol", "path", "symlink", "directory", "oversized", "runtime_prefix"])
def test_malformed_protocol_cannot_expand_reads(package_factory, tmp_path, change):
    app = package_factory()
    resources = app / "Contents/Resources"
    descriptor = resources / "update/reopen-protocol.json"
    data = json.loads(descriptor.read_text())
    if change in ("schema", "protocol"):
        data[change] = True if change == "schema" else 2
    elif change == "path":
        data["bindings"]["desktop"]["path"] = "../secret"
    elif change == "symlink":
        source = resources / "app.asar"
        original = tmp_path / "original.asar"
        source.rename(original)
        source.symlink_to(original)
    elif change == "directory":
        (resources / "app.asar").unlink()
        (resources / "app.asar").mkdir()
    elif change == "runtime_prefix":
        manifest = resources / "runtime/runtime-manifest.json"
        value = json.loads(manifest.read_text())
        value["python"] = "other/bin/python3"
        manifest.write_text(json.dumps(value))
        data["bindings"]["runtime_manifest"]["sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    descriptor.write_text("x" * 16385 if change == "oversized" else json.dumps(data))
    before = descriptor.read_bytes()
    with pytest.raises(ValueError, match="reopen protocol"):
        validate_reopen_package(app)
    assert descriptor.read_bytes() == before


@pytest.mark.parametrize("side", ["candidate", "installed", "frozen_installer"])
def test_prepare_rejects_incompatible_side_before_invoking_installer(package_factory, tmp_path, monkeypatch, side):
    from openprogram.self_update import supervisor

    installed, candidate = package_factory("old"), package_factory("new")
    update = tmp_path / "su_protocol"
    controller = update / "controller/install-app.sh"
    controller.parent.mkdir(parents=True)
    shutil.copyfile(installed / "Contents/Resources/update/install-app.sh", controller)
    if side == "frozen_installer":
        controller.write_text("older controller")
    else:
        app = candidate if side == "candidate" else installed
        (app / "Contents/Resources/update/reopen-protocol.json").unlink()
    artifact = Artifact(candidate, _tree_digest(candidate))
    monkeypatch.setattr(supervisor, "DEFAULT_APP_PATH", str(installed))
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: pytest.fail("must not invoke installer"))
    with pytest.raises(ValueError, match="reopen protocol"):
        supervisor._prepare_install(artifact, update, hashlib.sha256(controller.read_bytes()).hexdigest())


def test_writer_cli_rejects_missing_compiled_reopen_entry(package_factory):
    app = package_factory()
    resources = app / "Contents/Resources"
    descriptor = resources / "update/reopen-protocol.json"
    data = json.loads(descriptor.read_text())
    (resources / data["bindings"]["frontend"]["path"]).write_text("console.log('no recovery client')")
    before = descriptor.read_bytes()
    result = subprocess.run(["node", str(WRITER), "--resources", str(resources)], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and "compiled Web reopen" in result.stderr
    assert descriptor.read_bytes() == before


def test_refresh_copies_installer_and_regenerates_packaged_protocol(package_factory, tmp_path):
    app = package_factory()
    resources = app / "Contents/Resources"
    installer = resources / "update/install-app.sh"
    installer.write_text("old installed controller")
    archive = tmp_path / "app.asar"
    shutil.copyfile(resources / "app.asar", archive)
    staged_installer = tmp_path / "install-app.sh"
    shutil.copyfile(ROOT / "apps/desktop/scripts/install-app.sh", staged_installer)
    # Execute the real archive/resource replacement statements over fixture files;
    # deliberately exclude worker stop/start and OS App opening from this check.
    source = (ROOT / "scripts/refresh-local-app.sh").read_text()
    block = source[source.index('cp "$desktop_asar" "$installed_asar"'):source.index('revision="$build_revision"')]
    setup = 'app_path="$1"; installed_asar="$1/Contents/Resources/app.asar"; desktop_asar="$2"; installer_stage="$3"; repo_root="$4";\n'
    result = subprocess.run(["bash", "-euc", setup + block, "fixture", str(app), str(archive), str(staged_installer), str(ROOT)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert installer.read_bytes() == staged_installer.read_bytes()
    validate_reopen_package(app)

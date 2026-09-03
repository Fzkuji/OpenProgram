"""Controller copies preserve the trusted runtime across App replacement."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


@pytest.fixture
def fake_resources(tmp_path, monkeypatch):
    from openprogram.self_update import controller_bundle as bundle

    resources = tmp_path / "installed-resources"
    runtime = resources / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (resources / "update").mkdir()
    python = runtime / "bin/python"
    python.write_text("original interpreter")
    python.chmod(0o755)
    (runtime / "runtime-manifest.json").write_text(json.dumps({"schema": 2, "python": "bin/python"}))
    (resources / "update/install-app.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(bundle, "_installed_resources", lambda: resources)
    monkeypatch.setattr(bundle, "_probe_runtime", lambda *_: None)
    update = tmp_path / "update"
    update.mkdir(mode=0o700)
    return resources, update


def test_controller_bundle_preserves_original_installer_and_runtime(fake_resources):
    from openprogram.self_update import controller_bundle as bundle
    resources, update = fake_resources
    python = resources / "runtime/bin/python"
    first = bundle.prepare_controller(update)
    assert first.python.is_relative_to(update / "controller/runtime")
    assert first.python.read_text() == "original interpreter"
    assert (update / "controller").stat().st_mode & 0o777 == 0o700
    python.write_text("replacement interpreter")
    (resources / "update/install-app.sh").write_text("replacement installer")
    assert bundle.prepare_controller(update) == first
    assert (update / "controller/install-app.sh").read_text() == "#!/bin/sh\nexit 0\n"
    first.python.write_text("tampered snapshot")
    with pytest.raises(ValueError, match="digest"):
        bundle.prepare_controller(update)


@pytest.mark.parametrize("failure", ["external_symlink", "manifest_escape", "probe", "source_drift"])
def test_snapshot_failure_does_not_publish_or_leave_partial_copy(fake_resources, monkeypatch, failure):
    from openprogram.self_update import controller_bundle as bundle
    resources, update = fake_resources
    if failure == "external_symlink":
        (resources / "runtime/escape").symlink_to(resources / "update/install-app.sh")
    elif failure == "manifest_escape":
        (resources / "runtime/runtime-manifest.json").write_text(json.dumps({"schema": 2, "python": "../update/install-app.sh"}))
    elif failure == "probe":
        def fail(*_):
            raise ValueError("probe failed")
        monkeypatch.setattr(bundle, "_probe_runtime", fail)
    else:
        monkeypatch.setattr(bundle, "_probe_runtime", lambda *_: (resources / "runtime/bin/python").write_text("drift"))
    with pytest.raises((ValueError, RuntimeError)):
        bundle.prepare_controller(update)
    assert list(update.iterdir()) == []


@pytest.fixture
def native_workspace(tmp_path):
    # The complete runtime is large; do not retain copies in pytest's cache.
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="controller-native-") as directory:
        yield Path(directory)


def test_native_controller_imports_after_original_runtime_is_moved(native_workspace, monkeypatch):
    from openprogram.self_update import controller_bundle as bundle

    tmp_path = native_workspace
    installed = Path("/Applications/OpenProgram.app/Contents/Resources/runtime")
    if not (installed / "runtime-manifest.json").is_file():
        pytest.skip("requires the installed macOS standalone runtime")
    resources = tmp_path / "installed-resources"
    runtime = resources / "runtime"
    shutil.copytree(installed, runtime, symlinks=True)
    manifest = json.loads((runtime / "runtime-manifest.json").read_text())
    python = runtime / manifest["python"]
    package = next((runtime / "python").glob("*/lib/python*/site-packages/openprogram"))
    source = Path(bundle.__file__).parent
    shutil.copytree(source, package / "self_update", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    (resources / "update").mkdir()
    installer = Path(__file__).resolve().parents[3] / "apps/desktop/scripts/install-app.sh"
    shutil.copy2(installer, resources / "update/install-app.sh")
    monkeypatch.setattr(bundle, "_installed_resources", lambda: resources)
    update = tmp_path / "update"
    update.mkdir(mode=0o700)
    snapshot = bundle.prepare_controller(update)
    assert snapshot.python != python
    resources.rename(tmp_path / "previous-resources")
    code = (
        "from pathlib import Path; import importlib, sys; "
        "root=Path(sys.argv[1]); "
        "mods=[importlib.import_module(n) for n in "
        "['openprogram.self_update.supervisor','openprogram.self_update.verification','sqlite3','ssl']]; "
        "assert all(Path(m.__file__).resolve().is_relative_to(root) for m in mods); "
        "assert Path(sys.prefix).resolve().is_relative_to(root); print('PINNED_RUNTIME_OK')"
    )
    result = subprocess.run([str(snapshot.python), "-I", "-B", "-c", code, str(update / "controller/runtime")],
                            capture_output=True, text=True, timeout=30, env=bundle.controller_environment())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PINNED_RUNTIME_OK"
    assert bundle.prepare_controller(update) == snapshot

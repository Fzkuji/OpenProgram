"""Build inputs are private copies of matching trusted dependencies, not user environment."""
import os
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time

import pytest

from openprogram.operations.self_update.delivery import controller_bundle as bundle


@pytest.mark.parametrize(
    ("machine", "filename", "sha256"),
    (
        (
            "arm64",
            "electron-v44.4.3-darwin-arm64.zip",
            "6b728f5dcfae74f3f936f2bca5b3cd9b9659ffea464f67939f004acb55425a85",
        ),
        (
            "x86_64",
            "electron-v44.4.3-darwin-x64.zip",
            "015b52631d92187b552ff4e047255f596a7af4707e388a5890951f0b2645764e",
        ),
    ),
)
def test_electron_archive_is_pinned_by_version_and_architecture(
    tmp_path, monkeypatch, machine, filename, sha256
):
    package = tmp_path / "apps/desktop/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"devDependencies": {"electron": "44.4.3"}}))
    monkeypatch.setattr(bundle, "interpreter_architecture", lambda: machine)
    assert bundle._electron_archive_spec(tmp_path) == (filename, sha256)


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
def test_the_desktop_electron_version_is_pinned(monkeypatch, machine):
    # Bumping Electron without the controller pin breaks every complete build.
    root = Path(__file__).resolve().parents[4]
    monkeypatch.setattr(bundle, "interpreter_architecture", lambda: machine)
    filename, _sha256 = bundle._electron_archive_spec(root)
    version = json.loads((root / "apps/desktop/package.json").read_text())["devDependencies"]["electron"]
    assert filename.startswith(f"electron-v{version}-darwin-")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    home, update, candidate = (tmp_path / name for name in ("owner", "update", "candidate"))
    build_home = update / "build-home"
    runtime = update / "controller/runtime"
    for directory in (home, runtime, build_home, candidate / "scripts/release"):
        directory.mkdir(parents=True)
    for saved, requested in (("product-uv.lock", "uv.lock"), ("product-runtime.json", "scripts/release/product-runtime.json")):
        (runtime / saved).write_text("matching frozen input")
        (candidate / requested).write_text("matching frozen input")
    for name in (
        ".cache/uv",
        ".npm/_cacache",
        ".electron-gyp",
        "Library/Caches/electron-builder",
    ):
        path = home / name
        path.mkdir(parents=True)
        (path / "entry").write_text("original")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(bundle, "_load_bundle", lambda path: None)
    monkeypatch.setattr(bundle, "_runtime_python", lambda path: path / "python/bin/python3")
    electron_cache = home / "Library/Caches/electron/cache"
    electron_cache.mkdir(parents=True)
    archive = electron_cache / "electron-test.zip"
    archive.write_bytes(b"trusted electron")
    monkeypatch.setattr(
        bundle,
        "_electron_archive_spec",
        lambda _candidate: (archive.name, bundle._file_digest(archive)),
    )
    return home, update, candidate, build_home


@pytest.mark.macos
def test_matching_build_inputs_are_private_clones_with_closed_environment(inputs, monkeypatch):
    if not Path("/usr/bin/sandbox-exec").is_file():
        pytest.skip("requires macOS copy-on-write copies")
    home, update, candidate, build_home = inputs
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-secret")
    environment = bundle.prepare_build_inputs(update, candidate, build_home, deadline=time.time() + 20)
    original = home / ".cache/uv/entry"
    copied = build_home / ".cache/uv/entry"
    assert copied.read_text() == "original"
    assert copied.stat().st_ino != original.stat().st_ino
    copied.write_text("candidate changed private cache")
    assert original.read_text() == "original"
    assert environment["UV_OFFLINE"] == "1"
    assert environment["NPM_CONFIG_OFFLINE"] == "true"
    assert environment["OPENPROGRAM_UV_BIN"] == str(build_home / "runtime-base/bin/uv")
    assert environment["PATH"].startswith(str(build_home / "runtime-base/bin") + ":")
    assert "OPENAI_API_KEY" not in environment
    assert "UV_CACHE_DIR" in environment
    assert (build_home / ".electron-gyp/entry").read_text() == "original"
    electron_dist = Path(environment["OPENPROGRAM_SELF_UPDATE_ELECTRON_DIST"])
    assert electron_dist.read_bytes() == b"trusted electron"
    assert electron_dist.parent == update / "build-inputs"
    assert (build_home / "runtime-base/product-uv.lock").read_bytes() == (candidate / "uv.lock").read_bytes()
    assert os.stat(build_home / "runtime-base").st_mode & 0o777 == 0o700


@pytest.mark.parametrize("path", ["uv.lock", "scripts/release/product-runtime.json"])
def test_changed_dependency_input_fails_before_copy(inputs, path):
    _home, update, candidate, build_home = inputs
    (candidate / path).write_text("changed")
    with pytest.raises(ValueError, match="different complete runtime"):
        bundle.prepare_build_inputs(update, candidate, build_home, deadline=time.time() + 20)
    assert list(build_home.iterdir()) == []
    assert not (update / "build-inputs").exists()


def test_expired_build_input_budget_does_not_copy(inputs):
    _home, update, candidate, build_home = inputs
    with pytest.raises(TimeoutError, match="deadline expired"):
        bundle.prepare_build_inputs(update, candidate, build_home, deadline=time.time() - 1)
    assert list(build_home.iterdir()) == []


def test_existing_private_input_is_not_overwritten(inputs):
    _home, update, candidate, build_home = inputs
    base = build_home / "runtime-base"
    base.mkdir()
    (base / "preserve").write_text("owned")
    with pytest.raises(ValueError, match="already exists"):
        bundle.prepare_build_inputs(update, candidate, build_home, deadline=time.time() + 20)
    assert (base / "preserve").read_text() == "owned"
    with pytest.raises(ValueError, match="already exists"):
        with bundle.build_inputs(update, candidate, build_home, deadline=time.time() + 20):
            pytest.fail("existing inputs must not be used")
    assert (base / "preserve").read_text() == "owned"


@pytest.mark.macos
def test_failed_build_releases_only_its_private_caches(inputs):
    if not Path("/usr/bin/sandbox-exec").is_file():
        pytest.skip("requires macOS copy-on-write copies")
    home, update, candidate, build_home = inputs
    with pytest.raises(RuntimeError, match="build failed"):
        with bundle.build_inputs(update, candidate, build_home, deadline=time.time() + 20):
            assert (build_home / ".cache/uv/entry").is_file()
            raise RuntimeError("build failed")
    assert list(build_home.iterdir()) == []
    assert (home / ".cache/uv/entry").read_text() == "original"
    assert (update / "controller/runtime/product-uv.lock").is_file()


@pytest.mark.parametrize("mismatch", ["lock", "product", "existing-output"])
def test_public_runtime_preparation_rejects_incompatible_base_before_staging(tmp_path, mismatch):
    repo, base = tmp_path / "repo", tmp_path / "base"
    scripts = repo / "scripts/release"
    scripts.mkdir(parents=True)
    base.mkdir()
    source = Path(__file__).resolve().parents[4] / "scripts/release/prepare-desktop-runtime.sh"
    script = scripts / source.name
    shutil.copy2(source, script)
    (repo / "uv.lock").write_text("lock")
    (scripts / "product-runtime.json").write_text("product")
    (base / "product-uv.lock").write_text("different" if mismatch == "lock" else "lock")
    (base / "product-runtime.json").write_text("different" if mismatch == "product" else "product")
    output = repo / "apps/desktop/build/runtime"
    if mismatch == "existing-output":
        output.mkdir(parents=True)
        (output / "preserve").write_text("owned")
    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={"PATH": "/usr/bin:/bin", "OPENPROGRAM_SELF_UPDATE_RUNTIME_BASE": str(base)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "self-update" in result.stderr
    if mismatch == "existing-output":
        assert (output / "preserve").read_text() == "owned"
    else:
        assert not output.exists()


@pytest.mark.macos
def test_incremental_inputs_skip_electron_and_tolerate_missing_native_caches(inputs, monkeypatch):
    if not Path("/usr/bin/sandbox-exec").is_file():
        pytest.skip("requires macOS copy-on-write copies")
    home, update, candidate, build_home = inputs
    shutil.rmtree(home / ".electron-gyp")
    shutil.rmtree(home / "Library/Caches/electron-builder")
    monkeypatch.setattr(bundle, "stage_electron_distribution",
                        lambda *args, **kwargs: pytest.fail("incremental builds need no Electron"))
    environment = bundle.prepare_build_inputs(update, candidate, build_home,
                                              deadline=time.time() + 20, complete=False)
    assert "OPENPROGRAM_SELF_UPDATE_ELECTRON_DIST" not in environment
    assert list((build_home / ".electron-gyp").iterdir()) == []
    assert (build_home / "Library/Caches/electron-builder").is_dir()


class _Archive:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def safe_client(self, consumer, **_kwargs):
        assert consumer == "self_update.electron"
        archive = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def stream(self, method, url):
                archive.urls.append((method, url))

                class Response:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return False

                    def raise_for_status(self):
                        pass

                    def iter_bytes(self, _size):
                        yield archive.payload

                return Response()

        return Client()


@pytest.mark.parametrize("matches", [True, False])
@pytest.mark.skipif(sys.platform != "darwin", reason="stages the archive with an APFS clone (/bin/cp -c)")
def test_missing_electron_archive_is_downloaded_and_verified(tmp_path, monkeypatch, matches):
    from openprogram.host.security import safe_http

    home = tmp_path / "owner"
    home.mkdir()
    update = tmp_path / "update"
    update.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    payload = b"official electron"
    filename = "electron-v44.4.3-darwin-arm64.zip"
    expected = bundle.hashlib.sha256(payload if matches else b"other").hexdigest()
    monkeypatch.setattr(bundle, "_electron_archive_spec", lambda _candidate: (filename, expected))
    archive = _Archive(payload)
    monkeypatch.setattr(safe_http, "safe_client", archive.safe_client)
    if not matches:
        with pytest.raises(ValueError, match="pinned archive"):
            bundle.stage_electron_distribution(update, tmp_path, deadline=time.time() + 20)
        assert list((home / "Library/Caches/electron").iterdir()) == []
        return
    staged = bundle.stage_electron_distribution(update, tmp_path, deadline=time.time() + 20)
    assert archive.urls == [("GET", f"https://github.com/electron/electron/releases/download/v44.4.3/{filename}")]
    assert (home / "Library/Caches/electron" / filename).read_bytes() == payload
    assert staged.read_bytes() == payload

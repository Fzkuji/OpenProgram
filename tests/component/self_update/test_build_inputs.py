"""Build inputs are private copies of matching trusted dependencies, not user environment."""
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from openprogram.self_update import controller_bundle as bundle


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
        "Library/Caches/electron",
        "Library/Caches/electron-builder",
    ):
        path = home / name
        path.mkdir(parents=True)
        (path / "entry").write_text("original")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(bundle, "_load_bundle", lambda path: None)
    monkeypatch.setattr(bundle, "_runtime_python", lambda path: path / "python/bin/python3")
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
    assert (build_home / "runtime-base/product-uv.lock").read_bytes() == (candidate / "uv.lock").read_bytes()
    assert os.stat(build_home / "runtime-base").st_mode & 0o777 == 0o700


@pytest.mark.parametrize("path", ["uv.lock", "scripts/release/product-runtime.json"])
def test_changed_dependency_input_fails_before_copy(inputs, path):
    _home, update, candidate, build_home = inputs
    (candidate / path).write_text("changed")
    with pytest.raises(ValueError, match="different complete runtime"):
        bundle.prepare_build_inputs(update, candidate, build_home, deadline=time.time() + 20)
    assert list(build_home.iterdir()) == []


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
    source = Path(__file__).resolve().parents[3] / "scripts/release/prepare-desktop-runtime.sh"
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

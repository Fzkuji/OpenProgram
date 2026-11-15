"""Names and paths under the version store."""
from datetime import datetime, timezone
import json
import os
import sys

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions.layout import Layout


def test_version_id_is_utc_stamp_and_short_commit():
    commit = "3b08fd06e123" + "0" * 28
    moment = datetime(2026, 11, 8, 15, 30, 12, tzinfo=timezone.utc)
    assert paths.make_version_id(commit, moment) == "20261108T153012Z-3b08fd06e123"


@pytest.mark.parametrize("value", ["", ".hidden", "../up", "a/b", "a b", "a\n", "x" * 200, None])
def test_invalid_ids_are_refused(value):
    assert not paths.valid_id(value)
    with pytest.raises(ValueError):
        Layout(paths.Path("/r")).version(value)


def _base(lock=b"lock", dependencies='["a"]', python="3.12.10"):
    return paths.make_base_id(
        uv_lock=lock,
        pyproject=f'[project]\nname="x"\nversion="1"\ndependencies={dependencies}\n'.encode(),
        product_runtime=json.dumps({"python": python}).encode(),
    )


def test_base_id_changes_only_with_the_dependency_set():
    assert paths.BASE_ID.fullmatch(_base())
    assert _base().startswith("py3.12-")
    assert _base() == paths.make_base_id(
        uv_lock=b"lock",
        pyproject=b'[project]\nname="x"\nversion="2"\ndescription="other"\ndependencies=["a"]\n',
        product_runtime=json.dumps({"python": "3.12.10"}).encode(),
    )
    assert _base() != _base(lock=b"other")
    assert _base() != _base(dependencies='["b"]')
    assert _base(python="3.13.1").startswith("py3.13-")


def test_a_release_changing_only_its_own_version_keeps_the_base():
    lock = '[[package]]\nname = "openprogram"\nversion = "{}"\nsource = {{ editable = "." }}\n\n[[package]]\nname = "other"\nversion = "{}"\n'
    assert _base(lock=lock.format("1.0.1", "2").encode()) == _base(lock=lock.format("1.0.2", "2").encode())
    assert _base(lock=lock.format("1.0.1", "2").encode()) != _base(lock=lock.format("1.0.1", "3").encode())


def test_runtime_identity_comes_from_the_prefix(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    venv = root / "versions" / "v1" / "venv"
    venv.mkdir(parents=True)
    (root / "current").write_text("")
    found = paths.current_runtime(venv)
    assert found.version == "v1" and found.layout.root == root
    assert paths.current_runtime(tmp_path) is None
    assert paths.current_runtime(tmp_path / "venv") is None
    monkeypatch.setattr(sys, "prefix", str(venv))
    assert paths.managed()


def test_a_flat_runtime_is_not_managed(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    assert paths.current_runtime(runtime) is None


def _running_in_a_version(tmp_path, monkeypatch, *, git=True):
    root = tmp_path / "runtime"
    venv = root / "versions" / "v1" / "venv"
    venv.mkdir(parents=True)
    (root / "current").write_text("")
    base = "py3.12-0123456789abcdef"
    (venv.parent / "version.json").write_text(json.dumps({"base": base}))
    directory = root / "bases" / base
    (directory / "bin").mkdir(parents=True)
    (directory / "assets" / "playwright").mkdir(parents=True)
    if git:
        (directory / "git" / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    return directory


def test_a_sealed_version_is_immutable_without_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    _running_in_a_version(tmp_path, monkeypatch)
    assert paths.immutable_runtime()


def test_an_embedded_runtime_is_immutable_by_the_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    assert not paths.immutable_runtime()
    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    assert paths.immutable_runtime()


@pytest.mark.skipif(os.name == "nt", reason="POSIX Git layout")
def test_a_version_uses_the_git_and_browsers_of_its_base(tmp_path, monkeypatch):
    base = _running_in_a_version(tmp_path, monkeypatch)
    environment = {"PATH": "/usr/bin"}
    paths.apply_base_environment(environment)
    assert environment["PATH"].split(os.pathsep) == [str(base / "git/bin"), str(base / "bin"), "/usr/bin"]
    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == str(base / "assets/playwright")
    assert environment["GIT_EXEC_PATH"] == str(base / "git/libexec/git-core")
    assert environment["GIT_TEMPLATE_DIR"] == str(base / "git/share/git-core/templates")
    paths.apply_base_environment(environment)
    assert environment["PATH"].count(str(base / "git/bin")) == 1, "applying it twice adds nothing"


def test_outside_the_store_the_environment_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    environment = {"PATH": "/usr/bin"}
    paths.apply_base_environment(environment)
    assert environment == {"PATH": "/usr/bin"}


def test_a_version_whose_store_is_hidden_from_it_is_outside_the_store(tmp_path, monkeypatch):
    version = tmp_path / "runtime" / "versions" / "20261108T203035Z-339cb65c4288"
    (version / "venv").mkdir(parents=True)
    (tmp_path / "runtime" / "current").write_text("schema=1\n", encoding="utf-8")
    assert paths.current_runtime(version / "venv") is not None

    original = paths.Path.is_file

    def refused(self):
        if self.name == "current":
            raise PermissionError(1, "Operation not permitted", str(self))
        return original(self)

    monkeypatch.setattr(paths.Path, "is_file", refused)

    assert paths.current_runtime(version / "venv") is None

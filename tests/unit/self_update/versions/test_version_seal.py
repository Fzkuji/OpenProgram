"""Sealing a version and detecting changes to it."""
import json
import os
import zipfile

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import store
from openprogram.operations.self_update.versions.layout import Layout
from tests.support.sealed_versions import BASE, make_layout, make_version


@pytest.fixture
def layout(tmp_path):
    return make_layout(tmp_path / "runtime")


def test_seal_writes_every_part_and_is_clean(layout):
    version = make_version(layout, "v1")
    manifest = json.loads((version / "version.json").read_text())
    assert manifest["frontend_sha"]
    assert (layout.frontends / f"{manifest['frontend_sha']}.zip").is_file()
    assert store.changed_files(version, BASE) == []
    recorded = store.read_files_tsv(version)
    assert not set(recorded) & store.SEAL_FILES
    assert "desktop/main.js" in recorded
    assert any(name.endswith(".pyc") for name in recorded)


def test_the_bundle_is_outside_the_pristine_zip_and_shared(layout):
    first = make_version(layout, "v1")
    second = make_version(layout, "v2")
    with zipfile.ZipFile(first / "pristine.zip") as archive:
        names = archive.namelist()
    assert not any("_frontend" in name for name in names)
    assert any(name.endswith("openprogram/module.py") for name in names)
    assert len(list(layout.frontends.iterdir())) == 1
    make_version(layout, "v3", frontend="different")
    assert len(list(layout.frontends.iterdir())) == 2
    assert json.loads((first / "version.json").read_text())["frontend_sha"] == \
        json.loads((second / "version.json").read_text())["frontend_sha"]


def test_symlinks_are_sealed_as_links(layout):
    version = make_version(layout, "v1")
    with zipfile.ZipFile(version / "pristine.zip") as archive:
        assert archive.read("venv/bin/python3") == b"python"


def test_a_staged_directory_never_replaces_a_version(layout):
    make_version(layout, "v1")
    with pytest.raises(store.SealError):
        make_version(layout, "v1")


def test_an_existing_seal_needs_every_field(layout):
    with pytest.raises(store.SealError):
        store.seal(layout, layout.versions / ".staging-x", {"id": "x"})


def test_edited_added_and_removed_files_are_seen(layout):
    version = make_version(layout, "v1")
    site = paths.site_packages(version, BASE) / "openprogram"
    (site / "module.py").write_text("value = 22\n")
    (site / "added.py").write_text("")
    (version / "desktop" / "main.js").unlink()
    differences = set(store.changed_files(version, BASE))
    relative = site.relative_to(version).as_posix()
    assert {f"{relative}/module.py", f"{relative}/added.py", "desktop/main.js"} <= differences


def test_unlisted_bytecode_is_ignored(layout):
    version = make_version(layout, "v1")
    cache = paths.site_packages(version, BASE) / "openprogram" / "__pycache__"
    (cache / "fresh.cpython-312.pyc").write_bytes(b"x")
    assert store.changed_files(version, BASE) == []


def test_only_sealed_versions_are_listed_newest_first(layout):
    make_version(layout, "20260101T000000Z-aaaaaaaaaaaa")
    make_version(layout, "20260201T000000Z-bbbbbbbbbbbb")
    (layout.versions / ".staging-x").mkdir()
    (layout.versions / "torn").mkdir()
    assert [item["id"] for item in store.list_versions(layout)] == [
        "20260201T000000Z-bbbbbbbbbbbb", "20260101T000000Z-aaaaaaaaaaaa",
    ]


def test_attempts_count_inside_the_window_and_clear(layout):
    layout.boot.mkdir()
    layout.attempts("v1").write_text("100\n550\n590\n")
    assert store.recent_attempts(layout, "v1", now=600) == 3
    assert store.recent_attempts(layout, "v1", now=1000) == 2
    store.clear_attempts(layout, "v1")
    assert store.recent_attempts(layout, "v1", now=600) == 0
    assert layout.attempts("v1").exists()


def test_a_tab_in_a_path_is_refused(layout):
    staging = layout.versions / ".staging-bad"
    (staging / "desktop").mkdir(parents=True)
    (staging / "desktop" / "a\tb").write_text("")
    with pytest.raises(store.SealError):
        list(store.iter_files(staging))

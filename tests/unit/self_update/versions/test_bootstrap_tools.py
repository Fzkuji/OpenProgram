"""The stdlib tools that run outside every version."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import pointer, store
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version, point_at


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"bootstrap_{name}", REPOSITORY / "scripts/release/bootstrap" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


versions_tool = _load("versions")
restore_tool = _load("restore_version")


@pytest.fixture
def layout(tmp_path):
    return make_layout(tmp_path / "state" / "runtime", tools=True)


def test_python_and_bootstrap_write_the_same_format(layout):
    make_version(layout, "v1")
    make_version(layout, "v2")
    point_at(layout, "v1", last_good="v1", last_good_base=BASE)
    following = versions_tool.compare_and_set(layout.root, 5, {"version": "v2"})
    parsed = pointer.read(layout)
    assert parsed.version == "v2" and parsed.generation == 6 and parsed.writer == "bootstrap"
    assert following["generation"] == "6"
    assert layout.pointer.read_text() == pointer.render(parsed)
    pointer.compare_and_set(layout, 6, writer="controller", version="v1")
    assert versions_tool.read_pointer(layout.root)["version"] == "v1"


def test_a_stale_bootstrap_write_is_refused(layout):
    make_version(layout, "v1")
    point_at(layout, "v1")
    with pytest.raises(versions_tool.VersionsError):
        versions_tool.compare_and_set(layout.root, 4, {"version": "v1"})


def test_list_marks_current_and_good(layout, capsys):
    make_version(layout, "20260101T000000Z-aaaaaaaaaaaa")
    make_version(layout, "20260201T000000Z-bbbbbbbbbbbb", extra={"destructive_migration": "drop-old"})
    point_at(layout, "20260201T000000Z-bbbbbbbbbbbb", last_good="20260101T000000Z-aaaaaaaaaaaa", last_good_base=BASE)
    assert versions_tool.main(["--root", str(layout.root), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "[current, destructive migration: drop-old]" in lines[0]
    assert lines[1].endswith("[last good]")
    assert "summary of" in lines[1] and "2026-01-01" in lines[1]


def test_use_switches_the_pointer_with_no_worker_running(layout, capsys):
    make_version(layout, "v1")
    make_version(layout, "v2")
    point_at(layout, "v1", probation="u1", probation_deadline=5)
    assert versions_tool.main(["--root", str(layout.root), "use", "v2"]) == 0
    after = pointer.read(layout)
    assert after.version == "v2" and after.writer == "bootstrap" and after.probation == ""
    assert "moved from v1 to v2" in layout.recovery_log.read_text()


def test_use_refuses_a_version_whose_base_is_gone(layout, capsys):
    make_version(layout, "v1")
    make_version(layout, "v2", base="py3.12-ffffffffffffffff")
    point_at(layout, "v1")
    assert versions_tool.main(["--root", str(layout.root), "use", "v2"]) == 1
    assert pointer.read(layout).version == "v1"
    assert "is gone" in capsys.readouterr().err


def test_use_refuses_an_unsafe_name(layout, capsys):
    make_version(layout, "v1")
    point_at(layout, "v1")
    assert versions_tool.main(["--root", str(layout.root), "use", "../v1"]) == 1


def test_rebuild_picks_the_newest_version_without_git_history(layout, monkeypatch):
    make_version(layout, "20260101T000000Z-aaaaaaaaaaaa")
    make_version(layout, "20260201T000000Z-bbbbbbbbbbbb")
    monkeypatch.setattr(versions_tool, "_git", lambda _root: None)
    assert versions_tool.main(["--root", str(layout.root), "rebuild-pointer"]) == 0
    parsed = pointer.read(layout)
    assert parsed.version == "20260201T000000Z-bbbbbbbbbbbb" and parsed.writer == "bootstrap"


def test_rebuild_refuses_during_seeding(layout):
    make_version(layout, "v1")
    layout.seeding.write_text("")
    assert versions_tool.main(["--root", str(layout.root), "rebuild-pointer"]) == 1


def test_restore_data_replaces_databases_and_keeps_what_it_replaced(layout):
    state = layout.root.parent
    backup = state / "backups" / "pre-switch-u1"
    backup.mkdir(parents=True)
    source = sqlite3.connect(backup / "usage.db")
    source.execute("create table t (v)")
    source.execute("insert into t values ('old')")
    source.commit()
    source.close()
    (backup / "config.json").write_text('{"from": "backup"}')
    live = sqlite3.connect(state / "usage.db")
    live.execute("create table t (v)")
    live.execute("insert into t values ('new')")
    live.commit()
    live.close()
    (state / "config.json").write_text('{"from": "live"}')
    assert versions_tool.main(["--root", str(layout.root), "restore-data", "pre-switch-u1"]) == 0
    restored = sqlite3.connect(state / "usage.db")
    assert restored.execute("select v from t").fetchall() == [("old",)]
    restored.close()
    assert json.loads((state / "config.json").read_text()) == {"from": "backup"}
    safety = next((state / "backups").glob("before-restore-*"))
    assert json.loads((safety / "config.json").read_text()) == {"from": "live"}


def test_restore_data_refuses_other_names(layout):
    assert versions_tool.main(["--root", str(layout.root), "restore-data", "nightly"]) == 1


def test_only_metadata_differs_rewrites_the_seal(layout):
    version = make_version(layout, "v1")
    site = paths.site_packages(version, BASE) / "openprogram"
    import os
    os.utime(site / "module.py", (1_000_000, 1_000_000))
    result = restore_tool.capture(layout.root, "v1")
    assert result["applied"] is False
    assert not (layout.root / "unapplied").exists()
    assert store.changed_files(version, BASE) == []


def test_a_direct_edit_is_saved_with_repository_paths_and_undone(layout):
    version = make_version(layout, "v1")
    site = paths.site_packages(version, BASE) / "openprogram"
    (site / "module.py").write_text("value = 2\n")
    (site / "added.py").write_text("new = True\n")
    (version / "desktop" / "main.js").unlink()
    bundle = paths.frontend_bundle(paths.site_packages(version, BASE))
    (bundle / "index.html").write_text("edited")
    result = restore_tool.capture(layout.root, "v1")
    assert result["applied"] is True
    saved = Path(result["saved"])
    manifest = json.loads((saved / "manifest.json").read_text())
    assert set(manifest["changed"]) == {"openprogram/module.py", "openprogram/interfaces/server/_frontend/index.html"}
    assert manifest["added"] == ["openprogram/added.py"]
    assert manifest["removed"] == ["apps/desktop/main.js"]
    assert manifest["reported"] is False
    assert (saved / "files/openprogram/module.py").read_text() == "value = 2\n"
    patch = (saved / "changes.patch").read_text()
    assert "--- a/openprogram/module.py" in patch and "+value = 2" in patch
    assert "+++ /dev/null" in patch
    assert (site / "module.py").read_text() == "value = 1\n"
    assert not (site / "added.py").exists()
    assert (version / "desktop" / "main.js").is_file()
    assert (bundle / "index.html").read_text().startswith("<html>")
    assert store.changed_files(version, BASE) == []


def test_edited_bytecode_is_restored_without_a_report(layout):
    version = make_version(layout, "v1")
    cache = paths.site_packages(version, BASE) / "openprogram" / "__pycache__"
    (cache / "module.cpython-312.pyc").write_bytes(b"changed!")
    (cache / "fresh.cpython-312.pyc").write_bytes(b"new")
    result = restore_tool.capture(layout.root, "v1")
    assert result["applied"] is False
    assert (cache / "module.cpython-312.pyc").read_bytes() == b"bytecode"
    assert (cache / "fresh.cpython-312.pyc").exists()
    assert not (layout.root / "unapplied").exists()


def test_a_missing_pristine_zip_fails_the_capture(layout, capsys):
    version = make_version(layout, "v1")
    (version / "pristine.zip").unlink()
    assert restore_tool.main(["--root", str(layout.root), "v1"]) == 1


def test_use_forgets_the_failures_of_the_version_chosen(layout, capsys):
    make_version(layout, "v-a")
    make_version(layout, "v-b")
    point_at(layout, "v-a")
    layout.boot.mkdir(parents=True, exist_ok=True)
    layout.attempts("v-b").write_text("1\n2\n3\n")
    (layout.boot / "v-b.desktop-failed").write_text("{}")

    assert versions_tool.main(["--root", str(layout.root), "use", "v-b"]) == 0

    assert not layout.attempts("v-b").exists() and not (layout.boot / "v-b.desktop-failed").exists()


def test_process_alive_on_posix_tells_gone_from_someone_elses(monkeypatch):
    import os as module_os

    assert versions_tool._alive(module_os.getpid())
    monkeypatch.setattr(versions_tool.os, "kill", lambda pid, signal: (_ for _ in ()).throw(ProcessLookupError()))
    assert not versions_tool._alive(123)
    monkeypatch.setattr(versions_tool.os, "kill", lambda pid, signal: (_ for _ in ()).throw(PermissionError()))
    assert versions_tool._alive(123)


def test_taking_back_a_refused_start_removes_only_that_one(layout):
    layout.boot.mkdir(parents=True, exist_ok=True)
    layout.attempts("v-a").write_text("100\n200\n300\n")
    store.forget_last_attempt(layout, "v-a")
    assert layout.attempts("v-a").read_text().split() == ["100", "200"]

"""The version store knows which installs use it, so the last uninstaller can offer to delete it."""
from __future__ import annotations

import json
import os
import threading

from openprogram.operations.self_update.versions import users


def test_an_install_is_listed_once(tmp_path):
    first = users.register(tmp_path, "app", r"C:\Program Files\OpenProgram")
    again = users.register(tmp_path, "app", r"C:\Program Files\OpenProgram")
    assert first == again
    assert [row["kind"] for row in users.users(tmp_path)] == ["app"]
    body = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    assert body["schema"] == 1 and body["users"][0]["install_id"] == first


def test_removing_one_install_leaves_the_others(tmp_path):
    users.register(tmp_path, "app", "/Applications/OpenProgram.app")
    users.register(tmp_path, "cli", str(tmp_path / "identity"))
    left = users.unregister(tmp_path, "app", "/Applications/OpenProgram.app")
    assert [row["kind"] for row in left] == ["cli"]
    assert users.unregister(tmp_path, "cli", str(tmp_path / "identity")) == []
    assert users.users(tmp_path) == []


def test_installs_registering_at_once_are_all_kept(tmp_path):
    folders = [str(tmp_path / f"install-{n}") for n in range(12)]
    threads = [threading.Thread(target=users.register, args=(tmp_path, "cli", folder)) for folder in folders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(row["path"] for row in users.users(tmp_path)) == sorted(folders)


def test_an_unreadable_list_names_no_install(tmp_path):
    (tmp_path / "users.json").write_text("{not json", encoding="utf-8")
    assert users.users(tmp_path) == []
    users.register(tmp_path, "app", "x")
    assert len(users.users(tmp_path)) == 1


def test_the_id_matches_what_the_uninstaller_computes():
    # runtime-users.ps1: SHA-256 of "app\0<full path, lower case>", first 16 hex digits.
    import hashlib
    folder = os.path.normcase(os.path.abspath("/opt/OpenProgram"))
    expected = hashlib.sha256(f"app\0{folder}".encode()).hexdigest()[:16]
    assert users.install_id("app", "/opt/OpenProgram") == expected


def test_a_running_app_registers_by_its_folder(tmp_path, monkeypatch):
    from openprogram.operations.self_update.versions import layout

    monkeypatch.setattr(layout, "current_runtime", lambda: layout.CurrentRuntime(layout.Layout(tmp_path), "v1"))
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", r"C:\Apps\OpenProgram")
    users.register_running_install()
    monkeypatch.delenv("OPENPROGRAM_APP_PATH")
    users.register_running_install()
    assert sorted(row["kind"] for row in users.users(tmp_path)) == ["app", "cli"]

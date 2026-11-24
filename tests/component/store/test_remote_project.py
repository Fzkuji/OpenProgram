"""A project is a folder on one machine.

A folder on an SSH host is checked, listed and changed on that host through
its shell; nothing looks for it on this computer's disk. Here the "host" is
a shell on this computer in its own folder, so the commands really run.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from openprogram.host import machines
from openprogram.host.machines import here, status
from openprogram.state.store.project import project_store as projects


class _ShellHere:
    """An SSH host's backend that runs each command in a local shell, with its own home."""

    def __init__(self, home):
        self.home = str(home)
        self.commands: list[str] = []
        self.down = False

    def run_bytes(self, command, *, data=None, timeout=60.0):
        self.commands.append(command)
        if self.down:
            return 255, b"", b"ssh: connect to host devbox port 22: Connection refused"
        proc = subprocess.run(["bash", "-c", command], input=data or b"", capture_output=True,
                              timeout=timeout, cwd=self.home, env={**os.environ, "HOME": self.home})
        return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def host(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    os.chmod(config, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: config)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: config)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(here, "own_names", lambda: {"studio"})
    monkeypatch.setattr(here, "_resolve", lambda name: set())
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: None)
    monkeypatch.setattr(status, "_check", lambda machine: None)
    status.forget()
    home = tmp_path / "devbox-home"
    (home / "app" / "src").mkdir(parents=True)
    (home / "app" / "README.md").write_text("hello\n", encoding="utf-8")
    (home / "app" / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    backend = _ShellHere(home)
    monkeypatch.setattr("openprogram.host.backend.backend_for", lambda machine: backend)
    machines.save_machine({"id": "devbox", "name": "Dev box",
                           "shell": {"kind": "ssh", "target": "me@devbox", "workdir": str(home)}})
    yield backend
    status.forget()


def test_a_folder_on_a_host_is_a_project_there(host):
    project = projects.resolve_project("app", machine_id="devbox")
    folder = os.path.realpath(os.path.join(host.home, "app"))
    assert (project.machine_id, project.path, project.name) == ("devbox", folder, "app")
    assert not projects.folder_is_here(project)
    assert machines.resolve_for_project(project).id == "devbox"
    # Opening it again is the same project; a missing folder is refused.
    assert projects.resolve_project(folder, machine_id="devbox").id == project.id
    with pytest.raises(projects.ProjectStoreError, match="not a folder on Dev box"):
        projects.resolve_project("nowhere", machine_id="devbox")
    # Local-only features say no instead of reaching for this computer's disk.
    with pytest.raises(ValueError, match="folders on this computer"):
        projects.create_project_worktree(project.id, "/tmp/feature", "feature")


def test_a_host_that_cannot_be_reached_is_named(host):
    host.down = True
    with pytest.raises(projects.ProjectStoreError, match="cannot be reached"):
        projects.resolve_project("app", machine_id="devbox")
    assert status.state_of(machines.get_machine("devbox"), refresh=False)["state"] == "error"


def test_a_machine_that_is_off_or_unknown_takes_no_project(host):
    with pytest.raises(projects.ProjectStoreError, match="unknown machine"):
        projects.resolve_project("app", machine_id="ghost")
    machines.set_enabled("devbox", False)
    with pytest.raises(projects.ProjectStoreError, match="switched off"):
        projects.resolve_project("app", machine_id="devbox")


def test_a_local_folder_is_one_project_whatever_machine_runs_it(host, tmp_path):
    machines.save_machine({"id": "box", "shell": {"kind": "docker"}})
    folder = tmp_path / "work"
    folder.mkdir()
    project = projects.resolve_project(folder, machine_id="box")
    assert project.machine_id == "box" and projects.folder_is_here(project)
    with pytest.raises(projects.ProjectStoreError):
        projects.resolve_project(folder)


def test_browsing_a_hosts_folders(host):
    from openprogram.host.machines.remote import list_folders
    folder, names = list_folders(machines.get_machine("devbox"), "")
    assert folder == os.path.realpath(host.home) and names == ["app"]
    assert list_folders(machines.get_machine("devbox"), "missing") == (None, [])


def test_the_files_panel_reads_and_changes_the_folder_on_the_host(host):
    from openprogram.interfaces.server.ws_actions.files import remote
    project = projects.resolve_project("app", machine_id="devbox")
    target = remote.target(project.id)
    assert target is not None

    entries, _ = remote.directory(target, "")
    assert [(row["name"], row["type"]) for row in entries] == [("src", "dir"), ("README.md", "file")]
    rows, _ = remote.search(target, "", 100)
    assert sorted(row[3] for row in rows) == ["README.md", "src", "src/main.py"]

    read = remote.read_file(target, "README.md")
    assert read["content"] == "hello\n"
    assert remote.write_file(target, "README.md", "changed\n", "stale")["conflict"] is True
    assert remote.write_file(target, "README.md", "changed\n", read["revision"])["ok"]
    assert remote.read_file(target, "README.md")["content"] == "changed\n"

    assert remote.create_entry(target, "docs", "dir") == {"ok": True}
    assert "already exists" in remote.create_entry(target, "docs", "dir")["error"]
    assert remote.rename_entry(target, "README.md", "docs/README.md") == {"ok": True}
    assert remote.copy_entry(target, "src", "lib") == {"ok": True}
    assert remote.delete_entry(target, "lib") == {"ok": True}
    assert remote.read_file(target, "../outside")["error"] == "path escapes project root"
    assert remote.delete_entry(target, "")["error"] == "refusing to delete project root"
    entries, _ = remote.directory(target, "")
    assert [row["name"] for row in entries] == ["docs", "src"]
    # A deleted entry waits in the host's own trash.
    trash = os.path.join(host.home, ".openprogram", "trash")
    [entry] = os.listdir(trash)
    assert os.listdir(os.path.join(trash, entry)) == ["lib"]


def test_a_local_project_never_goes_through_a_shell(host, tmp_path):
    from openprogram.interfaces.server.ws_actions.files import remote
    folder = tmp_path / "here"
    folder.mkdir()
    project = projects.resolve_project(folder)
    assert remote.target(project.id) is None
    assert host.commands == []

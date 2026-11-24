"""Machines: the registry, the environment role, the shell kinds and their backends.

The registry lives in config.json under "machines"; a conversation runs on
its project's machine; the active backend and the file tools follow it and
never fall back to this computer when it is gone, switched off or without a
shell.
"""
from __future__ import annotations

import json
import os
import threading
import types

import pytest

from openprogram.host import machines
from openprogram.host.backend import (
    DEFAULT_DOCKER_IMAGE,
    DockerBackend,
    LocalBackend,
    SshBackend,
    UnavailableBackend,
    backend_for,
    container_name,
    get_active_backend,
)
from openprogram.host.machines import Connection, Machine, MachineError, here, kinds


@pytest.fixture(autouse=True)
def _no_resolver(monkeypatch):
    """Nothing is looked up: this computer has one fixed name, and a name the tests save is never this computer."""
    monkeypatch.setattr(here, "own_names", lambda: {"studio", "studio.local"})
    monkeypatch.setattr(here, "_resolve", lambda host: set())


@pytest.fixture
def removed():
    """Machines whose instances were sent for removal."""
    return []


@pytest.fixture
def cfg_path(tmp_path, monkeypatch, removed):
    """Point every config reader/writer and the secret store at temp files — never the user's."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: removed.append(machine_id))
    return p


def _write(p, cfg):
    p.write_text(json.dumps(cfg), encoding="utf-8")


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture
def screen_kind():
    """A screen kind with a secret setting, standing in for one a provider module registers."""
    kind = kinds.register(kinds.Kind(
        name="test_vnc", connection="screen", label="Test VNC",
        settings={"type": "object",
                  "properties": {"address": {"type": "string"},
                                 "password": {"type": "string", "secret": True}},
                  "required": ["address", "password"]},
        capabilities=frozenset({"pixels"}),
        where=lambda settings: f"VNC at {settings.get('address', '')}"))
    yield kind
    kinds._kinds.pop(("screen", "test_vnc"), None)


def _ssh(machine_id="devbox", **shell):
    return {"id": machine_id, "shell": {"kind": "ssh", "target": "me@devbox", **shell}}


# ---- registry ---------------------------------------------------------------


def test_fresh_install_has_only_this_computer(cfg_path):
    assert [m.id for m in machines.list_machines()] == ["local"]
    assert [m.id for m in machines.list_environments()] == ["local"]
    local = machines.local_machine()
    assert local.is_local and not local.remote_shell and not local.remote_files
    assert local.shell.kind == "local"


def test_save_and_delete(cfg_path, removed):
    machines.save_machine(_ssh(workdir="~/work"))
    machines.save_machine({"id": "box", "name": "Box", "shell": {"kind": "docker"}})
    assert [m.id for m in machines.list_environments()] == ["local", "devbox", "box"]
    box = machines.get_environment("box")
    assert box.shell.get("image") == DEFAULT_DOCKER_IMAGE
    assert box.instance_scope == "conversation" and box.instance("c1") == "box/c1"
    devbox = machines.get_environment("devbox")
    assert devbox.remote_shell and devbox.remote_files and devbox.instance("c1") == "devbox"

    machines.delete_machine("devbox")
    assert machines.get_machine("devbox") is None
    assert removed == ["devbox"]


def test_only_the_machines_section_is_read(cfg_path):
    _write(cfg_path, {"environments": {"default": "devbox", "items": [
        {"id": "devbox", "kind": "ssh", "ssh_target": "me@devbox"}]},
        "backend": {"terminal": "ssh", "ssh_target": "me@old"}})
    assert [m.id for m in machines.list_machines()] == ["local"]


@pytest.mark.parametrize("payload", [
    {"id": "local", "shell": {"kind": "ssh", "target": "h"}},
    {"id": "Bad Id", "shell": {"kind": "ssh", "target": "h"}},
    {"id": "x", "shell": {"kind": "vm"}},
    {"id": "x", "shell": {"kind": "local"}},
    {"id": "x", "shell": {"kind": "ssh"}},
    {"id": "x", "shell": {"kind": "ssh", "target": "-oProxyCommand=evil"}},
    {"id": "x", "shell": {"kind": "ssh", "target": "h", "workdir": "relative/dir"}},
    {"id": "x", "shell": {"kind": "ssh", "target": "h", "port": "22"}},
    {"id": "x", "shell": {"kind": "docker", "image": "--privileged"}},
    {"id": "x", "shell": "ssh"},
    {"id": "x", "name": "Nothing"},
    {"name": ""},
])
def test_invalid_payloads_are_refused(cfg_path, payload):
    with pytest.raises(MachineError):
        machines.save_machine(payload)


def test_a_machine_without_an_id_gets_one_from_its_name(cfg_path):
    first = machines.save_machine({"name": "Dev Box", "shell": {"kind": "docker"}})
    second = machines.save_machine({"name": "Dev Box", "shell": {"kind": "docker"}})
    assert (first.id, second.id) == ("dev-box", "dev-box-2")
    assert first.name == "Dev Box"


def test_saving_keeps_the_position_and_replaces_the_whole_machine(cfg_path):
    machines.save_machine(_ssh("a"))
    machines.save_machine(_ssh("b"))
    machines.save_machine({"id": "a", "name": "A again", "shell": {"kind": "docker"}})
    assert [m.id for m in machines.list_machines()] == ["local", "a", "b"]
    assert machines.get_machine("a").shell == Connection("docker")


def test_this_computer_cannot_be_removed_or_switched_off(cfg_path):
    with pytest.raises(MachineError):
        machines.delete_machine("local")
    with pytest.raises(MachineError):
        machines.set_enabled("local", False)


def test_display_fields_from_public_are_not_stored(cfg_path):
    saved = machines.save_machine(_ssh())
    machines.save_machine(saved.public())
    assert _read(cfg_path)["machines"]["items"][0]["shell"] == {"kind": "ssh", "target": "me@devbox"}


# ---- machines that are not environments ----------------------------------------


def test_a_machine_without_a_shell_is_never_an_environment(cfg_path, screen_kind):
    machines.save_machine({"id": "lab", "screen": {"kind": "test_vnc", "address": "10.0.0.5:5900",
                                                   "password": "pw"}})
    assert [m.id for m in machines.list_machines()] == ["local", "lab"]
    assert [m.id for m in machines.list_environments()] == ["local"]
    assert machines.get_environment("lab") is None
    with pytest.raises(MachineError, match="no shell"):
        machines.resolve_for_project(_project("lab"))


# ---- secrets and kinds this process does not know ---------------------------------


def test_a_secret_is_stored_outside_the_configuration_and_never_sent(cfg_path, screen_kind, tmp_path):
    saved = machines.save_machine({"id": "office", "screen": {"kind": "test_vnc", "address": "10.0.0.5:5900",
                                                              "password": "hunter2"}})
    stored = _read(cfg_path)["machines"]["items"][0]["screen"]
    assert stored == {"kind": "test_vnc", "address": "10.0.0.5:5900", "password": machines.SECRET_MARKER}
    assert "hunter2" not in cfg_path.read_text()
    secret_file = tmp_path / "state" / "auth" / "machines" / "office.json"
    assert json.loads(secret_file.read_text()) == {"screen.password": "hunter2"}
    if os.name != "nt":
        assert secret_file.stat().st_mode & 0o077 == 0
    public = saved.public()
    assert public["screen"]["password"] == machines.SECRET_MARKER
    assert "hunter2" not in json.dumps(public)
    assert machines.secret(saved, "screen", "password") == "hunter2"


@pytest.mark.parametrize("sent", [machines.SECRET_MARKER, None, ""])
def test_a_save_without_the_secret_keeps_the_stored_one(cfg_path, screen_kind, sent):
    machines.save_machine({"id": "office", "screen": {"kind": "test_vnc", "address": "a:1", "password": "pw"}})
    screen = {"kind": "test_vnc", "address": "b:2"}
    if sent is not None:
        screen["password"] = sent
    saved = machines.save_machine({"id": "office", "name": "Office", "screen": screen})
    assert saved.screen.get("address") == "b:2"
    assert machines.secret(saved, "screen", "password") == "pw"


def test_a_new_secret_replaces_the_old_and_deleting_removes_it(cfg_path, screen_kind, tmp_path):
    machines.save_machine({"id": "office", "screen": {"kind": "test_vnc", "address": "a:1", "password": "pw"}})
    saved = machines.save_machine({"id": "office", "screen": {"kind": "test_vnc", "address": "a:1",
                                                              "password": "new"}})
    assert machines.secret(saved, "screen", "password") == "new"
    machines.delete_machine("office")
    assert not (tmp_path / "state" / "auth" / "machines" / "office.json").exists()


def test_a_required_secret_is_still_required(cfg_path, screen_kind):
    with pytest.raises(MachineError, match="password"):
        machines.save_machine({"id": "office", "screen": {"kind": "test_vnc", "address": "a:1"}})


def test_a_kind_this_process_does_not_know_is_kept_verbatim(cfg_path):
    raw = {"kind": "from_elsewhere", "url": "http://lab:5000", "token": {"secret": True}, "extra": [1, 2]}
    _write(cfg_path, {"machines": {"items": [{"id": "lab", "name": "Lab", "screen": raw}]}})
    lab = machines.get_machine("lab")
    assert lab.screen.kind == "from_elsewhere" and lab.kind("screen") is None
    assert lab.public()["screen"]["known"] is False

    # Another machine changing leaves it alone, and sending it back unchanged keeps it.
    machines.save_machine(_ssh())
    machines.save_machine({**lab.public(), "name": "Lab VM"})
    item = next(i for i in _read(cfg_path)["machines"]["items"] if i["id"] == "lab")
    assert item == {"id": "lab", "name": "Lab VM", "screen": raw}

    # A changed one cannot be checked, so it is refused.
    with pytest.raises(MachineError, match="unknown screen kind"):
        machines.save_machine({"id": "lab", "screen": {**raw, "url": "http://other"}})


def test_a_connection_is_named_in_any_language():
    """A kind has a label in English and Chinese; where() is only the detail, the same in both."""
    devbox = _machine("devbox", kind="ssh", target="me@devbox")
    box = _machine("box", kind="docker", image="alpine:3")
    assert devbox.where("shell") == "me@devbox" and box.where("shell") == "alpine:3"
    assert machines.local_machine({}).where("shell") == ""
    assert devbox.describe("shell") == "SSH host me@devbox"
    assert machines.local_machine({}).describe("shell") == "This computer"
    shell = devbox.public()["shell"]
    assert (shell["label"], shell["label_zh"], shell["where"]) == ("SSH host", "SSH 主机", "me@devbox")
    for name in ("ssh", "docker"):
        kind = kinds.get("shell", name).public()
        assert kind["label_zh"] and kind["settings"]["description"] and kind["settings"]["x-zh"]["description"]
        for spec in kind["settings"]["properties"].values():
            assert spec["x-zh"]["title"] and spec["x-zh"]["description"]
    assert "password prompts are not supported" in kinds.get("shell", "ssh").settings["description"]


def test_kinds_name_their_settings_and_validate_them():
    ssh = kinds.get("shell", "ssh")
    assert ssh.public()["settings"]["required"] == ["target"]
    assert ssh.remote_files and ssh.instance_scope == "machine"
    assert kinds.get("shell", "docker").instance_scope == "conversation"
    assert {k.name for k in kinds.all_kinds("shell")} >= {"local", "ssh", "docker"}
    assert kinds.validated("shell", {"kind": "SSH", "target": "me@h", "workdir": ""}) == {
        "kind": "ssh", "target": "me@h"}
    assert kinds.validated("shell", {"kind": "docker"}) == {"kind": "docker", "image": DEFAULT_DOCKER_IMAGE}
    with pytest.raises(kinds.KindError, match="kinds: docker, ssh"):
        kinds.validated("shell", {"kind": "telnet"})
    with pytest.raises(ValueError):
        kinds.register(kinds.Kind(name="x", connection="sound", label="X"))


# ---- discovery -----------------------------------------------------------------


def test_discovered_machines_are_marked_and_never_take_a_configured_id(cfg_path, monkeypatch):
    found = [Machine("iphone-16-9f3a", "iPhone 16", screen=Connection("test_sim")),
             Machine("devbox", "Clash", screen=Connection("test_sim"))]
    machines.save_machine(_ssh())
    kind = kinds.register(kinds.Kind(name="test_sim", connection="screen", label="Simulator",
                                     discover=lambda: found))
    try:
        discovered = machines.discovered_machines()
        assert [m.id for m in discovered] == ["iphone-16-9f3a"]
        assert discovered[0].discovered and discovered[0].public()["discovered"] is True
        # Discovered machines are listed on request only, never stored.
        assert "iphone-16-9f3a" not in [m.id for m in machines.list_machines()]
        with pytest.raises(MachineError, match="discovered"):
            machines.save_machine(_ssh("iphone-16-9f3a"))
    finally:
        kinds._kinds.pop(("screen", kind.name), None)


# ---- projects decide ----------------------------------------------------------


def _project(machine_id=None, *, is_default=False, name="App"):
    return types.SimpleNamespace(id="p1", name=name, machine_id=machine_id, is_default=is_default)


@pytest.fixture
def project_of(monkeypatch):
    """The project each session belongs to, as the project store would answer."""
    from openprogram.state.store.project import project_store

    def install(project):
        monkeypatch.setattr(project_store, "project_for_session", lambda sid: project)
        monkeypatch.setattr(project_store, "get_project", lambda pid: _project(is_default=True, name="Home"))
        return project

    return install


def test_a_conversation_runs_on_its_projects_machine(cfg_path, project_of):
    machines.save_machine(_ssh())
    project_of(_project("devbox"))
    assert machines.resolve_for_session("s1").id == "devbox"


def test_the_home_project_and_a_project_naming_no_machine_run_here(cfg_path, project_of):
    machines.save_machine(_ssh())
    assert machines.resolve_for_project(_project("devbox", is_default=True)).is_local
    assert machines.resolve_for_project(_project(None)).is_local
    project_of(None)
    assert machines.resolve_for_session("s1").is_local


def test_removed_machine_never_falls_back(cfg_path, project_of, monkeypatch):
    project_of(_project("gone"))
    with pytest.raises(MachineError, match="Settings > Machines"):
        machines.resolve_for_session("s1")
    monkeypatch.setattr("openprogram.state.execution_facts.current_session_id", lambda: "s1")
    backend = get_active_backend()
    assert isinstance(backend, UnavailableBackend)
    assert "gone" in backend.run("ls", 5).stderr


def test_switched_off_machine_never_falls_back(cfg_path, project_of):
    machines.save_machine(_ssh())
    project_of(_project("devbox"))
    machines.set_enabled("devbox", False)
    with pytest.raises(MachineError, match="switched off"):
        machines.resolve_for_session("s1")


@pytest.mark.parametrize("change", ["switch_off", "remove", "lose_shell"])
def test_a_machine_switched_off_mid_turn_takes_no_more_work(cfg_path, screen_kind, change):
    """The turn bound the machine at its start; every later command and file call reads it again."""
    from openprogram.host.machines import remote
    devbox = machines.save_machine(_ssh())
    token = machines.bind_turn_environment(devbox)
    try:
        assert isinstance(get_active_backend(), SshBackend)
        if change == "switch_off":
            machines.set_enabled("devbox", False)
        elif change == "remove":
            machines.delete_machine("devbox")
        else:
            machines.save_machine({"id": "devbox", "screen": {"kind": "test_vnc", "address": "a", "password": "p"}})
        with pytest.raises(MachineError, match="devbox"):
            machines.current_environment()
        backend = get_active_backend()
        assert isinstance(backend, UnavailableBackend) and "devbox" in backend.run("ls", 5).stderr
        # Never None, which would put the file tools on this computer's disk.
        fs = remote.remote_fs()
        assert isinstance(fs, remote.RefusedFS)
        with pytest.raises(ConnectionError, match="devbox"):
            fs.stat("/srv/app/config.yml")
    finally:
        machines.reset_turn_environment(token)


def test_file_tools_refuse_on_a_machine_that_is_gone(cfg_path, monkeypatch):
    from openprogram.programs.tools.files import read, write
    from openprogram.state.worktree.context import reset_worktree, set_worktree
    machines.save_machine(_ssh())
    token = machines.bind_turn_environment(machines.get_machine("devbox"))
    tree = set_worktree("/srv/app")
    try:
        machines.delete_machine("devbox")
        written = write.execute(file_path="/srv/app/config.yml", content="x")
        assert written.startswith("Error writing /srv/app/config.yml: ") and "devbox" in written
        assert "devbox" in read.execute("notes.txt")
    finally:
        reset_worktree(tree)
        machines.reset_turn_environment(token)


def test_still_usable_reads_the_machine_again(cfg_path):
    devbox = machines.save_machine(_ssh())
    assert machines.still_usable(devbox) == devbox
    local = machines.local_machine()
    assert machines.still_usable(local) is local
    machines.set_enabled("devbox", False)
    with pytest.raises(MachineError, match="switched off"):
        machines.still_usable(devbox)
    machines.delete_machine("devbox")
    with pytest.raises(MachineError, match="removed"):
        machines.still_usable(devbox)


# ---- backends ---------------------------------------------------------------


def _machine(machine_id, **shell):
    return Machine(machine_id, machine_id, shell=Connection(shell.pop("kind"), shell))


def test_backend_for_each_shell_kind():
    assert isinstance(backend_for(machines.local_machine({})), LocalBackend)
    b = backend_for(_machine("devbox", kind="ssh", target="me@devbox", workdir="/srv/app"))
    assert isinstance(b, SshBackend) and b.target == "me@devbox" and b.workdir == "/srv/app"
    d = backend_for(_machine("box", kind="docker", image="alpine:3"), "sess/1")
    assert isinstance(d, DockerBackend) and d.machine_id == "box" and b.machine_id == "devbox"
    assert backend_for(machines.local_machine({})).machine_id == ""
    assert d.container == container_name("box", "sess/1") == "openprogram-box-sess-1"
    assert d.labels == {"openprogram.machine": "box", "openprogram.conversation": "sess/1"}


def test_a_machine_without_a_shell_runs_nothing():
    with pytest.raises(RuntimeError, match="no shell"):
        backend_for(Machine("lab", "Lab", screen=Connection("osworld", {"url": "http://x"})))
    with pytest.raises(RuntimeError, match="no shell"):
        backend_for(_machine("odd", kind="not_loaded"))


def test_turn_binding_routes_active_backend(cfg_path):
    token = machines.bind_turn_environment(machines.save_machine({"id": "box", "shell": {"kind": "docker"}}))
    try:
        assert isinstance(get_active_backend(), DockerBackend)
    finally:
        machines.reset_turn_environment(token)
    assert isinstance(get_active_backend(), LocalBackend)


def test_ssh_argv_cds_and_multiplexes():
    b = SshBackend("me@devbox", workdir="~/work dir")
    argv = b._ssh_argv("ls")
    assert argv[0] == "ssh" and "BatchMode=yes" in argv
    # Windows OpenSSH has no ControlMaster; the backend multiplexes elsewhere.
    assert any(a.startswith("ControlPath=") for a in argv) == (os.name != "nt")
    assert argv[-2] == "me@devbox"
    assert argv[-1] == "cd ~'/work dir' && ls"
    assert b._ssh_argv("pwd", cwd="/tmp")[-1] == "cd /tmp && pwd"


def test_docker_argv_execs_in_the_conversation_container():
    d = DockerBackend("alpine:3", container="openprogram-box-s1", mounts=("/w",))
    assert d._argv("ls", cwd="/w") == [
        "docker", "exec", "-i", "-w", "/w", "openprogram-box-s1", "sh", "-c", "ls"]
    one_shot = DockerBackend("alpine:3")
    assert one_shot._argv("ls") == ["docker", "run", "--rm", "-i", "alpine:3", "sh", "-c", "ls"]


def test_the_container_is_labelled_with_its_machine_and_conversation(monkeypatch):
    from openprogram.host.backend import docker
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=1 if argv[1] == "inspect" else 0, stdout="", stderr="")

    monkeypatch.setattr(docker.subprocess, "run", run)
    monkeypatch.setattr(docker, "_ready", {})
    d = DockerBackend("alpine:3", container="openprogram-box-s1",
                      labels={"openprogram.machine": "box", "openprogram.conversation": "s1"})
    assert d._ensure_container() is None
    created = next(argv for argv in calls if argv[1] == "run")
    assert "openprogram.machine=box" in created and "openprogram.conversation=s1" in created
    assert f"openprogram.home={docker.home_label()}" in created


def test_the_home_label_is_the_state_directory(monkeypatch, tmp_path):
    from openprogram.host.backend import docker
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "stable")
    stable = docker.home_label()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "dev")
    assert docker.home_label() != stable and len(stable) == 16


def test_a_container_stopped_behind_the_cache_is_started_again(monkeypatch):
    from openprogram.host.backend import docker
    calls = []
    stopped = {"exec": True}

    def run(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "exec" and stopped["exec"]:
            stopped["exec"] = False
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr="Error response from daemon: container 3f2a is not running\n")
        if argv[1] == "inspect":
            return types.SimpleNamespace(returncode=0, stdout=f"false {docker._mount_hash('alpine:3', ())}",
                                         stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(docker.subprocess, "run", run)
    # Docker Desktop restarted after the container was looked at: it is still there, stopped.
    monkeypatch.setattr(docker, "_ready", {"openprogram-box-s1": docker._mount_hash("alpine:3", ())})
    result = DockerBackend("alpine:3", container="openprogram-box-s1").run("ls", 5)
    assert result.exit_code == 0 and result.stdout == "ok"
    assert calls == ["exec", "inspect", "start", "exec"]


def test_a_command_that_prints_the_daemons_words_is_not_run_twice(monkeypatch):
    from openprogram.host.backend import docker
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        return types.SimpleNamespace(returncode=1, stdout="", stderr="docker: No such container: other\n")

    monkeypatch.setattr(docker.subprocess, "run", run)
    monkeypatch.setattr(docker, "_ready", {"openprogram-box-s1": docker._mount_hash("alpine:3", ())})
    result = DockerBackend("alpine:3", container="openprogram-box-s1").run("docker rm other", 5)
    assert result.exit_code == 1 and calls == ["exec"]


def test_a_spawned_command_looks_at_the_container_again(monkeypatch):
    from openprogram.host.backend import docker
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "inspect":
            return types.SimpleNamespace(returncode=0, stdout=f"false {docker._mount_hash('alpine:3', ())}",
                                         stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(docker.subprocess, "run", run)
    # No exec result comes back to a background command, so a stopped container is not trusted to the cache.
    monkeypatch.setattr(docker, "_ready", {"openprogram-box-s1": docker._mount_hash("alpine:3", ())})
    spec = DockerBackend("alpine:3", container="openprogram-box-s1").spawn_spec("sleep 1")
    assert calls == ["inspect", "start"] and spec["args"][:2] == ["docker", "exec"]


def test_no_container_starts_for_a_machine_switched_off_meanwhile(cfg_path, monkeypatch):
    from openprogram.host.backend import docker
    calls = []
    monkeypatch.setattr(docker.subprocess, "run",
                        lambda argv, **kw: calls.append(argv[1]) or types.SimpleNamespace(returncode=0, stdout="",
                                                                                          stderr=""))
    monkeypatch.setattr(docker, "_ready", {})
    # Its backend was made while it was on; it was switched off before the command ran.
    d = backend_for(machines.save_machine({"id": "box", "shell": {"kind": "docker"}}), "s1")
    machines.set_enabled("box", False)
    result = d.run("ls", 5)
    assert result.exit_code == 1 and "switched off" in result.stderr
    with pytest.raises(RuntimeError, match="switched off"):
        d.spawn_spec("ls")
    machines.delete_machine("box")
    assert "removed" in d.run("ls", 5).stderr
    assert calls == []


def test_a_container_removed_with_its_machine_is_not_started_again(cfg_path, monkeypatch):
    from openprogram.host.backend import docker
    calls = []
    d = backend_for(machines.save_machine({"id": "box", "shell": {"kind": "docker"}}), "s1")

    def run(argv, **kwargs):
        calls.append(argv[1])
        # The removal gave up waiting for this command and took the container while it ran.
        machines.set_enabled("box", False)
        return types.SimpleNamespace(returncode=1, stdout="",
                                     stderr=f"Error response from daemon: No such container: {d.container}\n")

    monkeypatch.setattr(docker.subprocess, "run", run)
    monkeypatch.setattr(docker, "_ready", {d.container: docker._mount_hash(d.image, d.mounts)})
    result = d.run("ls", 5)
    assert "switched off" in result.stderr and calls == ["exec"]


def test_an_unusable_machine_refuses_a_spawn_with_its_reason():
    with pytest.raises(RuntimeError, match="switched off"):
        UnavailableBackend("unavailable", "machine 'box' is switched off").spawn("ls")


def test_a_container_removed_behind_the_cache_is_started_again(monkeypatch):
    from openprogram.host.backend import docker
    calls = []
    gone = {"exec": True}

    def run(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "exec" and gone["exec"]:
            gone["exec"] = False
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr="Error response from daemon: No such container: openprogram-box-s1")
        if argv[1] == "inspect":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(docker.subprocess, "run", run)
    d = DockerBackend("alpine:3", container="openprogram-box-s1")
    # The cache says ready, but the container was removed after it was looked at.
    monkeypatch.setattr(docker, "_ready", {"openprogram-box-s1": docker._mount_hash("alpine:3", ())})
    result = d.run("ls", 5)
    assert result.exit_code == 0 and result.stdout == "ok"
    assert calls == ["exec", "inspect", "run", "exec"]


def test_instances_are_removed_by_label(cfg_path, monkeypatch):
    from openprogram.host.backend import docker
    machines.save_machine({"id": "box", "enabled": False, "shell": {"kind": "docker"}})
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="c1\nc2\n" if argv[1] == "ps" else "", stderr="")

    monkeypatch.setattr(docker.subprocess, "run", run)
    machines.remove_instances(machine="box")
    machines.remove_instances(conversation="s1")
    # Only this installation's: the stable and the dev instance share one Docker.
    home = f"label=openprogram.home={docker.home_label()}"
    assert calls == [
        ["docker", "ps", "-aq", "--filter", "label=openprogram.machine=box", "--filter", home],
        ["docker", "rm", "-f", "c1", "c2"],
        ["docker", "ps", "-aq", "--filter", "label=openprogram.conversation=s1", "--filter", home],
        ["docker", "rm", "-f", "c1", "c2"],
    ]
    calls.clear()
    machines.remove_instances()
    assert calls == []


def test_a_conversation_without_a_docker_machine_asks_docker_nothing(cfg_path, monkeypatch):
    from openprogram.host.backend import docker
    calls = []
    monkeypatch.setattr(docker.subprocess, "run", lambda argv, **kw: calls.append(argv))
    machines.save_machine(_ssh())
    machines.remove_instances(conversation="s1")
    assert calls == []


def test_a_machine_switched_on_again_keeps_its_instances(cfg_path, monkeypatch):
    from openprogram.host import backend
    from openprogram.host.backend import docker
    order = []
    monkeypatch.setattr(docker, "remove_containers", lambda **kw: order.append("remove"))
    machines.save_machine({"id": "box", "shell": {"kind": "docker"}})
    kind = kinds.register(kinds.Kind(name="test_scoped", connection="shell", label="Scoped",
                                     remove_instances=lambda machine, conversation: order.append("remove")))
    monkeypatch.setattr(machines, "releasers", [lambda **kw: order.append("release")])
    try:
        # Switched on again before the removal began: nothing is released or removed.
        machines.remove_instances(machine="box")
        assert order == []
        # Switched on again while its running commands finished.
        machines.set_enabled("box", False)

        def finished(machine_id, timeout):
            machines.set_enabled("box", True)
            return True

        monkeypatch.setattr(backend, "wait_idle", finished)
        machines.remove_instances(machine="box")
    finally:
        kinds._kinds.pop(("shell", kind.name), None)
    assert order == ["release"]


def test_running_commands_finish_before_a_machines_instances_go(cfg_path, monkeypatch):
    from openprogram.host import backend
    from openprogram.host.backend import base
    order = []
    monkeypatch.setattr(backend, "wait_idle", lambda machine_id, timeout: order.append(("wait", machine_id, timeout))
                        or True)
    kind = kinds.register(kinds.Kind(name="test_scoped", connection="shell", label="Scoped",
                                     remove_instances=lambda machine, conversation: order.append(("remove", machine))))
    try:
        machines.remove_instances(machine="box")
        machines.remove_instances(conversation="c1")
    finally:
        kinds._kinds.pop(("shell", kind.name), None)
    assert order[:2] == [("wait", "box", machines.RELEASE_WAIT), ("remove", "box")]
    # A conversation's removal waits for nothing: its turn was stopped before.
    assert ("wait", None, machines.RELEASE_WAIT) not in order

    with base.running_on("box"):
        assert base.wait_idle("box", 0) is False
        assert base.wait_idle("other", 0) is True
    assert base.wait_idle("box", 0) is True


def test_a_spawned_process_counts_while_it_lives():
    from openprogram.host.backend import base

    class _Proc:
        alive = True

        def poll(self):
            return None if self.alive else 0

    proc = base.spawned_on("box", _Proc())
    assert base.wait_idle("box", 0) is False
    proc.alive = False
    assert base.wait_idle("box", 0) is True


def test_a_background_command_counts_on_its_machine_while_its_supervisor_lives(monkeypatch):
    import io
    from openprogram.host import backend, processes
    from openprogram.host.backend import base

    class _Supervisor:
        alive = True

        def __init__(self, *args, **kwargs):
            self.stdin = io.BytesIO()
            self.stdin.close = lambda: None
            # The supervisor's line once its record runs.
            self.stdout = io.BytesIO(b"ready\n")

        def poll(self):
            return None if self.alive else 0

        def wait(self):
            return 0

    class _Store:
        path = "store.db"

        def create(self, **fields):
            return {"id": "p1", "status": "starting"}

        def get(self, process_id):
            return {"id": process_id, "status": "running"}

    remote = DockerBackend("alpine:3")
    remote.machine_id = "box"
    remote.spawn_spec = lambda command, cwd=None: {"args": ["docker", "exec"]}
    started = []
    monkeypatch.setattr(processes, "current_owner", lambda: ("s1", None, None))
    monkeypatch.setattr(backend, "get_active_backend", lambda: remote)
    monkeypatch.setattr(processes, "_supervisor_command", lambda store, process_id, launch: ["supervisor"])
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *a, **kw: started.append(_Supervisor()) or started[-1])
    # The follower reads the ready line and reaps; here it runs in place, and
    # this supervisor's wait returns at once.
    monkeypatch.setattr(processes, "threading", types.SimpleNamespace(
        Event=threading.Event,
        Thread=lambda target, **kw: types.SimpleNamespace(start=target)))
    processes.start("make build", store=_Store())
    # Switching the machine off waits for it, as for a command run in the foreground.
    assert base.wait_idle("box", 0) is False
    started[0].alive = False
    assert base.wait_idle("box", 0) is True


def test_no_docker_means_nothing_to_remove(cfg_path, monkeypatch):
    from openprogram.host.backend import docker
    machines.save_machine({"id": "box", "shell": {"kind": "docker"}})

    def run(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(docker.subprocess, "run", run)
    machines.remove_instances(conversation="s1")


# ---- checks -------------------------------------------------------------------


def test_check_walks_every_connection_and_names_an_unknown_kind():
    from openprogram.host.machines.check import check_machine
    assert check_machine(Machine("local", "This computer", shell=Connection("local"))) == (True, "This computer")
    ok, message = check_machine(Machine("lab", "Lab", screen=Connection("not_loaded")))
    assert not ok and "not_loaded" in message


# ---- remote file tools --------------------------------------------------------


@pytest.mark.parametrize("pattern,path,hit", [
    ("*.py", "a.py", True),
    ("*.py", "pkg/a.py", False),
    ("**/*.py", "pkg/sub/a.py", True),
    ("**/*.py", "a.py", True),
    ("src/[!t]*.ts", "src/app.ts", True),
    ("src/[!t]*.ts", "src/test.ts", False),
    ("file?.txt", "file1.txt", True),
])
def test_remote_glob_regex(pattern, path, hit):
    from openprogram.programs.tools.files._remote import _glob_regex
    assert bool(_glob_regex(pattern).match(path)) is hit


class _FakeFS:
    """In-memory stand-in for RemoteFS."""

    def __init__(self):
        self.machine = _machine("devbox", kind="ssh", target="me@devbox")
        self.label = "devbox"
        self.files: dict[str, bytes] = {}

    def stat(self, path):
        from openprogram.host.machines.remote import RemoteStat
        if path in self.files:
            return RemoteStat(True, False, len(self.files[path]))
        return RemoteStat(False, False, 0)

    def read_bytes(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_bytes(self, path, data):
        self.files[path] = data


@pytest.fixture
def remote(monkeypatch):
    from openprogram.state.worktree.context import reset_worktree, set_worktree
    fs = _FakeFS()
    monkeypatch.setattr("openprogram.programs.tools.files._remote.remote_target", lambda: fs)
    token = set_worktree("/srv/app")
    yield fs
    reset_worktree(token)


def test_remote_read_routes_and_resolves_relative_paths(remote):
    from openprogram.programs.tools.files import read
    remote.files["/srv/app/notes.txt"] = b"one\ntwo\n"
    out = read.execute("notes.txt")
    assert out.splitlines()[0] == "# devbox:/srv/app/notes.txt (lines 1-2 of 2)"
    assert "     2\ttwo" in out


def test_remote_edit_requires_a_fresh_read(remote):
    from openprogram.programs.tools.files import _remote as r
    remote.files["/srv/app/a.txt"] = b"hello world"
    assert "before changing it" in r.edit(remote, "a.txt", "world", "there", False)
    r.read(remote, "a.txt", 1, 100)
    assert r.edit(remote, "a.txt", "world", "there", False).startswith("Edited devbox:")
    assert remote.files["/srv/app/a.txt"] == b"hello there"
    # Changed behind the agent's back: refuse until read again.
    remote.files["/srv/app/a.txt"] = b"hello again"
    assert "changed on its machine" in r.edit(remote, "a.txt", "again", "x", False)


def test_remote_write_creates_new_files(remote):
    from openprogram.programs.tools.files import _remote as r
    assert r.write(remote, "/srv/app/new.txt", "hi").startswith("Wrote 2 bytes")
    assert remote.files["/srv/app/new.txt"] == b"hi"


def test_remote_files_go_through_the_machine_backend(monkeypatch):
    from openprogram.host.machines import remote
    devbox = _machine("devbox", kind="ssh", target="me@devbox", workdir="/srv/app")
    fs = remote.RemoteFS(devbox)
    assert isinstance(fs.backend, SshBackend) and fs.backend.target == "me@devbox"
    assert fs.label == "devbox"
    monkeypatch.setattr(machines, "current_environment", lambda: devbox)
    monkeypatch.setattr(remote, "current_environment", lambda: devbox)
    assert remote.remote_fs() is not None
    monkeypatch.setattr(remote, "current_environment",
                        lambda: _machine("box", kind="docker"))
    assert remote.remote_fs() is None
    assert remote.remote_workdir(devbox) == "/srv/app"


def test_the_remote_folder_follows_an_edited_target(monkeypatch):
    from openprogram.host import backend
    from openprogram.host.machines import remote
    homes = {"me@host-a": "/home/me", "deploy@host-b": "/home/deploy"}

    class _Shell:
        def __init__(self, target):
            self.target = target

        def run(self, command, timeout):
            return types.SimpleNamespace(exit_code=0, stdout=homes[self.target] + "\n")

    monkeypatch.setattr(backend, "backend_for", lambda machine: _Shell(machine.shell.get("target")))
    monkeypatch.setattr(remote, "_workdirs", {})
    assert remote.remote_workdir(_machine("devbox", kind="ssh", target="me@host-a")) == "/home/me"
    # Same id, another target: the folder is resolved again.
    assert remote.remote_workdir(_machine("devbox", kind="ssh", target="deploy@host-b")) == "/home/deploy"

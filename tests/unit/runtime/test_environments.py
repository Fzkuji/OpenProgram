"""Environments: where a conversation's commands and file tools run.

The registry lives in config.json; a conversation pins one on its first
message; the active backend and the file tools follow the pinned
environment and never fall back to this computer when it is gone.
"""
from __future__ import annotations

import json
import os

import pytest

from openprogram.host import environments as envs
from openprogram.host.backend import (
    DockerBackend,
    LocalBackend,
    SshBackend,
    UnavailableBackend,
    backend_for,
    get_active_backend,
    session_container_name,
)


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """Point every config reader/writer at a temp file — never the user's."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)

    def _read():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    monkeypatch.setattr("openprogram.state.config.read_config", _read)
    return p


def _write(p, cfg):
    p.write_text(json.dumps(cfg), encoding="utf-8")


# ---- registry ---------------------------------------------------------------


def test_fresh_install_has_only_local(cfg_path):
    assert [e.id for e in envs.list_environments()] == ["local"]
    assert envs.default_environment_id() == "local"
    assert envs.default_environment().is_local


def test_save_set_default_and_delete(cfg_path):
    envs.save_environment({"id": "devbox", "kind": "ssh",
                           "ssh_target": "me@devbox", "workdir": "~/work"})
    envs.save_environment({"id": "box", "kind": "docker"})
    ids = [e.id for e in envs.list_environments()]
    assert ids == ["local", "devbox", "box"]
    assert envs.get_environment("box").image == envs.DEFAULT_DOCKER_IMAGE

    envs.set_default_environment("devbox")
    assert envs.default_environment_id() == "devbox"

    envs.delete_environment("devbox")
    assert envs.get_environment("devbox") is None
    # Removing the default falls back to local, never to a missing id.
    assert envs.default_environment_id() == "local"


@pytest.mark.parametrize("payload", [
    {"id": "local", "kind": "ssh", "ssh_target": "h"},
    {"id": "Bad Id", "kind": "ssh", "ssh_target": "h"},
    {"id": "x", "kind": "vm"},
    {"id": "x", "kind": "ssh", "ssh_target": ""},
    {"id": "x", "kind": "ssh", "ssh_target": "-oProxyCommand=evil"},
    {"id": "x", "kind": "ssh", "ssh_target": "h", "workdir": "relative/dir"},
    {"id": "x", "kind": "docker", "image": "--privileged"},
])
def test_invalid_payloads_are_refused(cfg_path, payload):
    with pytest.raises(envs.EnvironmentError):
        envs.save_environment(payload)


def test_local_cannot_be_removed_and_unknown_default_refused(cfg_path):
    with pytest.raises(envs.EnvironmentError):
        envs.delete_environment("local")
    with pytest.raises(envs.EnvironmentError):
        envs.set_default_environment("nope")


def test_legacy_backend_config_becomes_the_default(cfg_path):
    _write(cfg_path, {"backend": {"terminal": "ssh", "ssh_target": "me@old"}})
    env = envs.default_environment()
    assert env.kind == "ssh" and env.ssh_target == "me@old"

    # The first write folds the legacy entry into the section.
    envs.save_environment({"id": "box", "kind": "docker"})
    cfg = json.loads(cfg_path.read_text())
    assert cfg["environments"]["default"] == env.id
    assert [i["id"] for i in cfg["environments"]["items"]] == [env.id, "box"]


# ---- sessions ---------------------------------------------------------------


class _FakeDB:
    def __init__(self, history=False, environment_id=None):
        self.history = history
        self.rows = {"s1": {"environment_id": environment_id}}

    def get_session(self, sid):
        return self.rows.get(sid)

    def get_branch(self, sid):
        return [{"id": "m1"}] if self.history else []

    def update_session(self, sid, **fields):
        self.rows.setdefault(sid, {}).update(fields)


@pytest.fixture
def fake_db(monkeypatch):
    holder = {}

    def install(db):
        holder["db"] = db
        monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
        return db

    return install


def test_first_message_pins_the_pick(cfg_path, fake_db):
    envs.save_environment({"id": "box", "kind": "docker"})
    db = fake_db(_FakeDB())
    assert envs.assign_new_session_environment(db, "s1", "box") == "box"
    assert db.rows["s1"]["environment_id"] == "box"
    # A later pick does not move it.
    assert envs.assign_new_session_environment(db, "s1", "local") == "box"


def test_no_pick_pins_the_default(cfg_path, fake_db):
    envs.save_environment({"id": "box", "kind": "docker"})
    envs.set_default_environment("box")
    db = fake_db(_FakeDB())
    assert envs.assign_new_session_environment(db, "s1", None) == "box"


def test_old_conversation_keeps_following_the_default(cfg_path, fake_db):
    db = fake_db(_FakeDB(history=True))
    assert envs.assign_new_session_environment(db, "s1", "local") is None
    assert db.rows["s1"]["environment_id"] is None


def test_unknown_pick_is_refused(cfg_path, fake_db):
    db = fake_db(_FakeDB())
    with pytest.raises(envs.EnvironmentError):
        envs.assign_new_session_environment(db, "s1", "ghost")


def test_removed_environment_never_falls_back(cfg_path, fake_db, monkeypatch):
    fake_db(_FakeDB(environment_id="gone"))
    with pytest.raises(envs.EnvironmentError):
        envs.resolve_for_session("s1")
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_session_id", lambda: "s1")
    backend = get_active_backend()
    assert isinstance(backend, UnavailableBackend)
    assert "gone" in backend.run("ls", 5).stderr


# ---- backends ---------------------------------------------------------------


def test_backend_for_each_kind():
    local = envs.LOCAL
    ssh = envs.Environment(id="devbox", name="devbox", kind="ssh",
                           ssh_target="me@devbox", workdir="/srv/app")
    docker = envs.Environment(id="box", name="box", kind="docker", image="alpine:3")
    assert isinstance(backend_for(local), LocalBackend)
    b = backend_for(ssh)
    assert isinstance(b, SshBackend) and b.target == "me@devbox" and b.workdir == "/srv/app"
    d = backend_for(docker, "sess/1")
    assert isinstance(d, DockerBackend)
    assert d.container == session_container_name("box", "sess/1") == "openprogram-box-sess-1"


def test_turn_binding_routes_active_backend(cfg_path):
    env = envs.Environment(id="box", name="box", kind="docker", image="alpine:3")
    token = envs.bind_turn_environment(env)
    try:
        assert isinstance(get_active_backend(), DockerBackend)
    finally:
        envs.reset_turn_environment(token)
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


def test_docker_argv_execs_in_session_container():
    d = DockerBackend("alpine:3", container="openprogram-box-s1", mounts=("/w",))
    assert d._argv("ls", cwd="/w") == [
        "docker", "exec", "-i", "-w", "/w", "openprogram-box-s1", "sh", "-c", "ls"]
    one_shot = DockerBackend("alpine:3")
    assert one_shot._argv("ls") == ["docker", "run", "--rm", "-i", "alpine:3", "sh", "-c", "ls"]


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
        self.env = envs.Environment(id="devbox", name="devbox", kind="ssh",
                                    ssh_target="me@devbox")
        self.files: dict[str, bytes] = {}

    def stat(self, path):
        from openprogram.host.environments.remote import RemoteStat
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
    assert out.splitlines()[0] == "# me@devbox:/srv/app/notes.txt (lines 1-2 of 2)"
    assert "     2\ttwo" in out


def test_remote_edit_requires_a_fresh_read(remote):
    from openprogram.programs.tools.files import _remote as r
    remote.files["/srv/app/a.txt"] = b"hello world"
    assert "before changing it" in r.edit(remote, "a.txt", "world", "there", False)
    r.read(remote, "a.txt", 1, 100)
    assert r.edit(remote, "a.txt", "world", "there", False).startswith("Edited me@devbox:")
    assert remote.files["/srv/app/a.txt"] == b"hello there"
    # Changed behind the agent's back: refuse until read again.
    remote.files["/srv/app/a.txt"] = b"hello again"
    assert "changed on the remote host" in r.edit(remote, "a.txt", "again", "x", False)


def test_remote_write_creates_new_files(remote):
    from openprogram.programs.tools.files import _remote as r
    assert r.write(remote, "/srv/app/new.txt", "hi").startswith("Wrote 2 bytes")
    assert remote.files["/srv/app/new.txt"] == b"hi"

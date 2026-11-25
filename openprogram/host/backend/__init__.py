"""Pluggable execution backend for shell-style tools.

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic.

Three backends ship out of the box:
    local    — subprocess.run on this computer
    docker   — a long-lived container per conversation (``docker exec``)
    ssh      — ``ssh <target> "..."`` per call, one shared connection

Which one runs is decided by the session's environment
(``openprogram.host.machines``): a conversation runs on its project's
machine, and the backend follows that machine's connection method. This
module registers the methods ``ssh`` and ``docker`` with the device layer
(their settings, check and labels) and keeps, in ``SHELLS``, which methods
commands run through: ``local``, ``ssh`` and ``docker``. The device layer
knows nothing of shells; it asks ``shell_for``.
"""
from __future__ import annotations

import contextlib
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from openprogram.host.backend.base import Backend, RunResult, wait_idle
from openprogram.host.backend.local import LocalBackend
from openprogram.host.backend.docker import DockerBackend
from openprogram.host.backend.ssh import SshBackend


class UnavailableBackend(Backend):
    """Stands in when the session's environment can't be used, so every
    tool reports the same clear reason instead of running elsewhere."""

    def __init__(self, backend_id: str, reason: str) -> None:
        self.backend_id = backend_id
        self.reason = reason

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        return RunResult(exit_code=1, stdout="", stderr=self.reason)

    def spawn_spec(self, command: str, cwd: str | None = None) -> dict:
        raise RuntimeError(self.reason)

    def spawn(self, command: str, cwd: str | None = None) -> subprocess.Popen:
        raise RuntimeError(self.reason)


def container_name(machine_id: str, conversation: str) -> str:
    """The name of a conversation's container on a Docker machine.

    A machine id never changes, so the name stays valid for the life of the
    conversation; cleanup finds containers by their labels, not by name.
    """
    raw = f"openprogram-{machine_id}-{conversation}"
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", raw)[:120]


def _session_mounts(session_id: str | None) -> tuple[str, ...]:
    """Host folders a session's container sees: its working directory
    first (the container's starting folder), then extra working dirs."""
    paths: list[str] = []
    with contextlib.suppress(Exception):
        from openprogram.state.worktree.context import current_worktree_path
        wt = current_worktree_path()
        if wt:
            paths.append(wt)
    if session_id:
        from openprogram.host import runtime_facts
        with contextlib.suppress(Exception):
            wd = runtime_facts.session_workdir(session_id)
            if wd:
                paths.append(wd)
        with contextlib.suppress(Exception):
            paths.extend(runtime_facts.session_additional_working_dirs(session_id))
    out: list[str] = []
    for path in paths:
        if path and path.startswith("/") and path not in out:
            out.append(path)
    return tuple(out)


@dataclass(frozen=True)
class Shell:
    """How commands run on a device reached by one connection method."""
    # ``backend(machine, conversation) -> Backend``.
    backend: Callable[..., Backend]
    # One instance for the machine, or one per conversation.
    instance_scope: str = "machine"
    # File tools go through the shell rather than this computer's disk.
    remote_files: bool = False
    # Scoped to the conversation: ``remove_instances(machine_id, conversation_id)`` removes the instances
    # labelled with either (both may be given; one is None), whether or not the machine is still configured.
    remove_instances: Callable[[str | None, str | None], None] | None = None


# The connection methods commands run through, by method name. A device reached any other way
# (another computer's OpenProgram, a virtual machine's screen) runs no commands.
SHELLS: dict[str, Shell] = {}


def shell_for(machine) -> Shell | None:
    """The shell of ``machine``'s connection method, or None when commands cannot run through it."""
    return SHELLS.get(machine.method) if machine is not None and machine.connection is not None else None


def backend_for(machine, session_id: str | None = None) -> Backend:
    """Build the backend that runs commands on ``machine`` for ``session_id``: its connection method's."""
    shell = shell_for(machine)
    if shell is None:
        # Never this computer in its place: a machine without a usable shell runs nothing.
        raise RuntimeError(f"machine {machine.id!r} has no shell commands can run through")
    backend = shell.backend(machine, session_id)
    if not machine.is_local:
        backend.machine_id = machine.id
    return backend


def get_active_backend() -> Backend:
    """The backend for the running session's environment."""
    from openprogram.host.machines import MachineError, current_environment
    try:
        machine = current_environment()
    except MachineError as exc:
        return UnavailableBackend("unavailable", str(exc))
    from openprogram.state.execution_facts import current_session_id
    session_id = current_session_id()
    try:
        return backend_for(machine, session_id)
    except Exception as exc:
        return UnavailableBackend(machine.method or "unavailable", f"machine {machine.id!r}: {exc}")


# -- the shells ----------------------------------------------------------------

DEFAULT_DOCKER_IMAGE = "ubuntu:24.04"


def _local_backend(machine, session_id: str | None) -> Backend:
    return LocalBackend()


def _ssh_backend(machine, session_id: str | None) -> Backend:
    return SshBackend(target=machine.connection.get("target"), workdir=machine.connection.get("workdir"))


def _docker_backend(machine, session_id: str | None) -> Backend:
    from openprogram.host.backend.docker import CONVERSATION_LABEL, MACHINE_LABEL
    image = machine.connection.get("image", DEFAULT_DOCKER_IMAGE)
    if not session_id:
        return DockerBackend(image=image)
    return DockerBackend(image=image, container=container_name(machine.id, session_id),
                         mounts=_session_mounts(session_id),
                         labels={MACHINE_LABEL: machine.id, CONVERSATION_LABEL: session_id})


def _remove_docker_instances(machine: str | None, conversation: str | None) -> None:
    from openprogram.host.backend.docker import remove_containers
    remove_containers(machine=machine, conversation=conversation)


def _docker_settings(settings: dict) -> dict:
    return {**settings, "image": str(settings.get("image") or "").strip() or DEFAULT_DOCKER_IMAGE}


def _check_ssh(machine) -> tuple[bool, str]:
    result = SshBackend(machine.connection.get("target"), machine.connection.get("workdir")).run("pwd", timeout=20)
    if result.exit_code == 0:
        lines = result.stdout.strip().splitlines()
        return True, "Connected" + (f" · {lines[-1]}" if lines else "")
    return False, (result.stderr.strip() or f"ssh exited {result.exit_code}")[:500]


def _check_docker(machine) -> tuple[bool, str]:
    try:
        proc = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return False, "Docker is not installed (docker CLI not on PATH)"
    except subprocess.TimeoutExpired:
        return False, "Docker did not answer within 20s"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or "Docker is not running")[:500]
    return True, f"Docker {proc.stdout.strip()} · image {machine.connection.get('image')}"


def ssh_host(target: str) -> str:
    """The host an SSH target names: ``user@host``, ``ssh://user@host:port`` or a bare host."""
    text = str(target or "").strip()
    if text.lower().startswith("ssh://"):
        from urllib.parse import urlsplit
        return urlsplit(text).hostname or ""
    return text.rsplit("@", 1)[-1].strip("[]")


def _ssh_port(target: str) -> int | None:
    """The port an ``ssh://`` target names, or None."""
    if not target.lower().startswith("ssh://"):
        return None
    from urllib.parse import urlsplit
    try:
        return urlsplit(target).port
    except ValueError:
        return None


def _admit_ssh(settings: dict) -> None:
    from openprogram.host.machines import here
    from openprogram.host.machines.kinds import KindError
    target = str(settings.get("target") or "")
    if _ssh_port(target) not in (None, 22):
        # Another port is another sshd: on a loopback address, a virtual machine or a container whose port is
        # forwarded here, each with its own operating system.
        return
    if here.is_this_computer(ssh_host(target)):
        raise KindError(f"Refused: the SSH target {target} is this computer, which is built in and runs "
                        "commands without being added as a machine.")


def _ssh_where(settings: dict) -> str:
    """The host, with the folder a conversation starts in when one is set: ``me@devbox:/srv/app``."""
    target, workdir = str(settings.get("target") or ""), str(settings.get("workdir") or "")
    return f"{target}:{workdir}" if target and workdir else target


def _register_methods() -> None:
    from openprogram.host.machines.kinds import Kind, register

    register(Kind(
        name="ssh", label="SSH host", label_zh="SSH 主机",
        settings={
            "type": "object",
            "description": "Uses your ssh config and keys; password prompts are not supported.",
            "x-zh": {"description": "使用你的 ssh 配置和密钥；不支持输入密码。"},
            "properties": {
                "target": {"type": "string", "title": "SSH target", "pattern": r"^[^-\s]\S*$",
                           "description": "user@host, or a host from ~/.ssh/config",
                           "x-zh": {"title": "SSH 目标", "description": "user@host，或 ~/.ssh/config 里的主机"},
                           "examples": ["me@devbox"]},
                "workdir": {"type": "string", "title": "Remote folder", "pattern": r"^[/~]",
                            "description": "The absolute remote folder where choosing a project folder starts",
                            "x-zh": {"title": "远程文件夹", "description": "在这台机器上选择项目文件夹时的起始绝对路径"},
                            "examples": ["/home/me/work"]},
            },
            "required": ["target"],
        },
        check=_check_ssh, admit=_admit_ssh, where=_ssh_where))
    register(Kind(
        name="docker", label="Docker container", label_zh="Docker 容器",
        settings={
            "type": "object",
            "description": "Each conversation gets its own container; the conversation's folders are mounted "
                           "at the same paths.",
            "x-zh": {"description": "每个对话有自己的容器；对话的文件夹挂载在相同路径。"},
            "properties": {
                "image": {"type": "string", "title": "Image", "pattern": r"^[^-\s]\S*$",
                          "description": "The Docker image each conversation's container starts from",
                          "x-zh": {"title": "镜像", "description": "每个对话的容器所用的 Docker 镜像"},
                          "default": DEFAULT_DOCKER_IMAGE, "examples": [DEFAULT_DOCKER_IMAGE]},
            },
        },
        check=_check_docker, normalize=_docker_settings,
        where=lambda settings: str(settings.get("image") or DEFAULT_DOCKER_IMAGE)))


SHELLS.update({
    "local": Shell(_local_backend),
    "ssh": Shell(_ssh_backend, remote_files=True),
    "docker": Shell(_docker_backend, instance_scope="conversation", remove_instances=_remove_docker_instances),
})
_register_methods()


__all__ = [
    "Backend",
    "RunResult",
    "SHELLS",
    "Shell",
    "shell_for",
    "DEFAULT_DOCKER_IMAGE",
    "LocalBackend",
    "DockerBackend",
    "SshBackend",
    "UnavailableBackend",
    "backend_for",
    "get_active_backend",
    "container_name",
    "wait_idle",
]

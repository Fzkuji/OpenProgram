"""Pluggable execution backend for shell-style tools.

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``machines.get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic.

Three backends ship out of the box:
    local    — subprocess.run on this computer
    docker   — a long-lived container per conversation (``docker exec``)
    ssh      — ``ssh <target> "..."`` per call, one shared connection

Which one runs is decided by the session's environment
(``openprogram.host.machines``, which sits above the backends): a
conversation runs on its project's machine, and the backend follows that
machine's connection method. This module keeps, in ``SHELLS``, which
methods commands run through: ``local``, ``ssh`` and ``docker``; the
machines ask ``shell_for`` and ``backend_for``, build the backend for the
running session (``machines.get_active_backend``) and register ``ssh``
and ``docker`` as connection methods (``machines.shells``). The backends
know nothing of configured machines.
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


SHELLS.update({
    "local": Shell(_local_backend),
    "ssh": Shell(_ssh_backend, remote_files=True),
    "docker": Shell(_docker_backend, instance_scope="conversation", remove_instances=_remove_docker_instances),
})


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
    "container_name",
    "wait_idle",
]

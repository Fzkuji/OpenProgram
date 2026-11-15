"""Pluggable execution backend for shell-style tools.

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic.

Three backends ship out of the box:
    local    — subprocess.run on the host (default, unchanged behavior)
    docker   — a long-lived container per session (``docker exec``)
    ssh      — ``ssh <target> "..."`` per call, one shared connection

Which one runs is decided by the session's environment
(``openprogram.host.environments``): every conversation picks one when it
is created, and the backend follows it.
"""
from __future__ import annotations

import contextlib
import re

from openprogram.host.backend.base import Backend, RunResult
from openprogram.host.backend.local import LocalBackend
from openprogram.host.backend.docker import DockerBackend
from openprogram.host.backend.ssh import SshBackend


BACKEND_CLASSES: dict[str, type[Backend]] = {
    "local":  LocalBackend,
    "docker": DockerBackend,
    "ssh":    SshBackend,
}


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


def session_container_name(env_id: str, session_id: str) -> str:
    raw = f"openprogram-{env_id}-{session_id}"
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


def backend_for(env, session_id: str | None = None) -> Backend:
    """Build the backend that runs ``env`` for ``session_id``."""
    if env.kind == "ssh":
        return SshBackend(target=env.ssh_target, workdir=env.workdir)
    if env.kind == "docker":
        if not session_id:
            return DockerBackend(image=env.image)
        return DockerBackend(
            image=env.image,
            container=session_container_name(env.id, session_id),
            mounts=_session_mounts(session_id),
        )
    return LocalBackend()


def get_active_backend() -> Backend:
    """The backend for the running session's environment."""
    from openprogram.host.environments import EnvironmentError, current_environment
    try:
        env = current_environment()
    except EnvironmentError as exc:
        return UnavailableBackend("unavailable", str(exc))
    from openprogram.state.execution_facts import current_session_id
    session_id = current_session_id()
    try:
        return backend_for(env, session_id)
    except Exception as exc:
        return UnavailableBackend(env.kind, f"environment {env.id!r}: {exc}")


__all__ = [
    "Backend",
    "RunResult",
    "BACKEND_CLASSES",
    "LocalBackend",
    "DockerBackend",
    "SshBackend",
    "UnavailableBackend",
    "backend_for",
    "get_active_backend",
    "session_container_name",
]

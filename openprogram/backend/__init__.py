"""Pluggable execution backend for shell-style tools.

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic and lets ``openprogram config backend`` actually
reroute where commands execute.

Three backends ship out of the box:
    local    — subprocess.run on the host (default, unchanged behavior)
    docker   — ``docker run --rm -i <image> sh -c "..."`` per call
    ssh      — ``ssh <target> "..."`` per call

Selection is read lazily from ``~/.agentic/config.json`` (via
``setup._read_config``) so ``--profile`` and live config
edits take effect without restarting anything.
"""
from __future__ import annotations

from openprogram.backend.base import Backend, RunResult
from openprogram.backend.local import LocalBackend
from openprogram.backend.docker import DockerBackend
from openprogram.backend.ssh import SshBackend


BACKEND_CLASSES: dict[str, type[Backend]] = {
    "local":  LocalBackend,
    "docker": DockerBackend,
    "ssh":    SshBackend,
}


def get_active_backend() -> Backend:
    """Resolve the currently-configured backend. Falls back to local."""
    try:
        from openprogram.setup import _read_config
        cfg = _read_config()
    except Exception:
        return LocalBackend()
    be = cfg.get("backend", {}) or {}
    kind = (be.get("terminal") or "local").lower()
    cls = BACKEND_CLASSES.get(kind, LocalBackend)
    try:
        if cls is DockerBackend:
            return DockerBackend(image=be.get("docker_image") or "ubuntu:24.04")
        if cls is SshBackend:
            return SshBackend(target=be.get("ssh_target") or "")
        return cls()
    except Exception as e:
        # Config references a backend that can't be built (e.g. ssh_target
        # missing). Log once and fall back so tools don't explode.
        print(f"[backend] {kind} init failed: {e}; falling back to local")
        return LocalBackend()


__all__ = [
    "Backend",
    "RunResult",
    "BACKEND_CLASSES",
    "LocalBackend",
    "DockerBackend",
    "SshBackend",
    "get_active_backend",
]

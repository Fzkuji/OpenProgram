"""What the host layer tells the state layer.

State sits below ``openprogram.host`` (machines, sandbox, backends) and never
imports it. A few state readers still need something only the host has:
the machine a project's folder lives on and whether that machine keeps its
own files (the project registry), whether the sandbox lets a path be read
(reading a conversation's files back) and how to run a command in the
sandbox wherever the platform has one (the memory workspace's shell). The
host layer registers one provider per fact here when it loads
(``openprogram.host.lower_layers``, installed by the root package); the
functions below ask for it.

Without a registered provider each answer is what it is in a process where
the host layer never loaded: no machine but this computer, nothing the
sandbox refuses, and no way to run a command.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "machine",
    "project_machine",
    "environment_refusal",
    "remote_folder",
    "read_violation",
    "sandbox_preferred_invocation",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the host layer's provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown host facts: {sorted(unknown)}")
    _providers.update(providers)


def machine(machine_id: str) -> Any:
    """The configured machine (``name``, ``remote_files`` …), or None."""
    provider = _providers.get("machine")
    return provider(machine_id) if provider else None


def project_machine(project: Any) -> Any:
    """The machine a project's conversations run on; raises when the
    project names one that was removed, switched off or runs no commands."""
    provider = _providers.get("project_machine")
    return provider(project) if provider else None


def environment_refusal(machine: Any) -> str | None:
    """Why a conversation cannot run on ``machine``, or None when it can."""
    provider = _providers.get("environment_refusal")
    return provider(machine) if provider else "no machine can run conversations here"


def remote_folder(machine: Any, path: str) -> str | None:
    """The absolute path of a folder on a machine that keeps its own files,
    or None when it is not a folder there. Raises ``ConnectionError`` when
    the machine cannot be reached."""
    provider = _providers.get("remote_folder")
    if provider is None:
        raise ConnectionError("no machine can be reached from here")
    return provider(machine, path)


def read_violation(path: str) -> str | None:
    """Why the sandbox in force refuses to read ``path``, or None."""
    provider = _providers.get("read_violation")
    return provider(path) if provider else None


def sandbox_preferred_invocation(command: str, cwd: str, *, refusal: str | None = None):
    """``(args, use_shell, env)`` to run ``command`` in ``cwd`` inside the
    sandbox wherever the platform has one, whatever the interactive sandbox
    setting says. Where it has none the command runs outside it with the
    sandbox's filtered environment, unless ``refusal`` is given: then it is
    refused with that message (``{reason}`` names why there is no sandbox)."""
    provider = _providers.get("sandbox_preferred_invocation")
    if provider is None:
        raise RuntimeError("commands run through the host layer, which is not loaded")
    return provider(command, cwd, refusal=refusal)


__all__ = [
    "FACTS",
    "environment_refusal",
    "machine",
    "project_machine",
    "read_violation",
    "register",
    "remote_folder",
    "sandbox_preferred_invocation",
]

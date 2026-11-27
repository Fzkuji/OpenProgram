"""What the host layer hands down to the platform and state layers.

``openprogram.host._platform`` and ``openprogram.state`` sit below the host
and never import it. What they need from it arrives through ports they
own, filled in here:

* ``host._platform.host_facts`` — a setting's stored value (read through
  ``state.settings``, which the platform layer sits below too), and whether
  the sandbox is in force and can run here;
* ``state.host_facts`` — machines and their folders, what the sandbox
  refuses to read and running a command in the sandbox where there is one;
* ``state.setting_rules`` — the checks of the settings the host owns: the
  Web bind host and Origins, the outbound URL policy and the sandbox's
  domain map.

The root package installs these before any other module runs, so no
process reads a setting or a project without them. Each provider imports
its host module when it is called, never here, so loading this stays cheap
and a test that replaces a host function is what the lower layers see.
"""
from __future__ import annotations

from typing import Any

from openprogram.host._platform import host_facts as platform_facts
from openprogram.state import host_facts as state_facts
from openprogram.state import setting_rules


def _setting_value(key: str) -> Any:
    from openprogram.state import settings

    return settings.setting_value(key)


def _sandbox_in_force(policy: Any = None) -> bool:
    from openprogram.host import sandbox

    if policy is None:
        policy = sandbox.resolve_policy()
    return policy is not None and sandbox.unavailable_reason() is None


def _sandbox_unavailable_reason() -> str | None:
    from openprogram.host import sandbox

    return sandbox.unavailable_reason()


def _machine(machine_id: str):
    from openprogram.host import machines

    return machines.get_machine(machine_id)


def _project_machine(project):
    from openprogram.host import machines

    return machines.resolve_for_project(project)


def _environment_refusal(machine) -> str | None:
    from openprogram.host import machines

    return None if machines.usable_as_environment(machine) else machines._not_an_environment(machine)


def _remote_folder(machine, path: str) -> str | None:
    from openprogram.host.machines import remote

    return remote.resolve_folder(machine, path)


def _read_violation(path: str) -> str | None:
    from openprogram.host import sandbox

    return sandbox.validate_read_path(path)


def _sandbox_preferred_invocation(command: str, cwd: str, *, refusal: str | None = None):
    from openprogram.host.backend import local

    return local.sandbox_preferred_invocation(command, cwd, refusal=refusal)


def _validate_web_bind_host(value: Any) -> str | None:
    from openprogram.host.security.backend_endpoint import OwnerAuthError, canonicalize_bind_host

    try:
        canonicalize_bind_host(str(value))
    except OwnerAuthError as exc:
        return str(exc)
    return None


def _validate_web_origins(value: Any) -> str | None:
    if not isinstance(value, list) or not all(isinstance(origin, str) for origin in value):
        return "must be a JSON list of Origin strings"
    from openprogram.host.security.backend_endpoint import OwnerAuthError, canonicalize_origin

    for origin in value:
        try:
            canonicalize_origin(origin)
        except OwnerAuthError as exc:
            return f"invalid Origin {origin!r}: {exc}"
    return None


def _validate_outbound_url(value: Any) -> str | None:
    from openprogram.host.security import outbound_settings

    return outbound_settings.validation_error(value)


def _normalize_outbound_url(value: Any) -> Any:
    from openprogram.host.security import outbound_settings

    return outbound_settings.normalized(value)


def _validate_network_domains(value: Any) -> str | None:
    from openprogram.host.sandbox import network_policy

    return network_policy.validation_error(value)


def register() -> None:
    platform_facts.register(
        setting_value=_setting_value,
        sandbox_in_force=_sandbox_in_force,
        sandbox_unavailable_reason=_sandbox_unavailable_reason,
    )
    state_facts.register(
        machine=_machine,
        project_machine=_project_machine,
        environment_refusal=_environment_refusal,
        remote_folder=_remote_folder,
        read_violation=_read_violation,
        sandbox_preferred_invocation=_sandbox_preferred_invocation,
    )
    setting_rules.register("web.host", validate=_validate_web_bind_host)
    setting_rules.register("web.allowed_origins", validate=_validate_web_origins)
    setting_rules.register(
        "security.outbound_url",
        validate=_validate_outbound_url,
        normalize=_normalize_outbound_url,
    )
    setting_rules.register("sandbox.network_domains", validate=_validate_network_domains)


__all__ = ["register"]

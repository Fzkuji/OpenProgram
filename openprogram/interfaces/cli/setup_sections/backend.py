"""Default environment section.

Where new conversations run: this computer, an SSH host, or a Docker
container. The full list is managed with ``openprogram environments``;
this wizard step picks the default and can add one SSH or Docker
environment on the way.
"""
from __future__ import annotations


def run_backend_section() -> int:
    from openprogram.host.environments import (
        EnvironmentError,
        default_environment_id,
        list_environments,
        save_environment,
        set_default_environment,
    )
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one, _text

    envs = list_environments()
    choices = [env.id for env in envs] + ["+ add SSH host", "+ add Docker container"]
    picked = _choose_one("Default environment for new conversations:",
                         choices, default_environment_id())
    if picked is None:
        print("Cancelled.")
        return 1
    if picked.startswith("+ add"):
        kind = "ssh" if "SSH" in picked else "docker"
        env_id = _text("Environment id (lowercase, e.g. devbox):", default="")
        payload = {"id": env_id or "", "kind": kind}
        if kind == "ssh":
            payload["ssh_target"] = _text("SSH host (user@host):", default="") or ""
            payload["workdir"] = _text("Remote folder (blank = home):", default="") or ""
        else:
            payload["image"] = _text("Container image:", default="ubuntu:24.04") or ""
        try:
            picked = save_environment(payload).id
        except EnvironmentError as exc:
            print(f"[error] {exc}")
            return 1
    try:
        set_default_environment(picked)
    except EnvironmentError as exc:
        print(f"[error] {exc}")
        return 1
    print(f"New conversations start in: {picked}")
    print("Manage the list with `openprogram environments`.")
    return 0

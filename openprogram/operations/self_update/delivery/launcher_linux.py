"""Run the self-update controller under ``systemd --user`` on Linux."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def _unit(label: str) -> str:
    return label.replace("_", "-") + ".service"


def submit_controller(label: str, argv: list[str], env: dict[str, str], log: Path) -> None:
    from .controller import spawn_detached

    tool = shutil.which("systemd-run")
    if tool is not None:
        arguments = [tool, "--user", "--collect", f"--unit={_unit(label)}",
                     "--property=Restart=on-failure", "--property=RestartSec=5",
                     f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}"]
        arguments += [f"--setenv={name}={value}" for name, value in env.items()]
        result = subprocess.run([*arguments, *argv], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or "already" in (result.stderr or "").lower():
            return
    spawn_detached(argv, env, log)


def retire_controller(label: str) -> None:
    tool = shutil.which("systemctl")
    if tool is not None:
        subprocess.run([tool, "--user", "stop", _unit(label)], capture_output=True, text=True, timeout=30)


__all__ = ["retire_controller", "submit_controller"]

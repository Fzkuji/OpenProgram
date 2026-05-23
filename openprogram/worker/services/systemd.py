"""Linux systemd --user integration for the persistent worker.

Writes ``~/.config/systemd/user/openprogram-worker.service`` and runs
``systemctl --user daemon-reload && enable --now`` so the worker starts
immediately and on every subsequent login.

For the unit to keep running after the user logs out (e.g. SSH session
ends), the user typically needs ``sudo loganctl enable-linger $USER``.
We surface a hint about that without running it ourselves — touching
sudo is out of scope for ``openprogram worker install``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from openprogram.worker import paths as worker_paths

UNIT_NAME = "openprogram-worker.service"


def _unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "systemd" / "user" / UNIT_NAME


def _systemctl(*args: str) -> tuple[int, str]:
    if shutil.which("systemctl") is None:
        return 127, "systemctl not found"
    try:
        out = subprocess.run(
            ["systemctl", "--user", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as e:
        return 1, str(e)
    return out.returncode, (out.stdout + out.stderr).strip()


def _build_unit() -> str:
    python = sys.executable
    log = worker_paths.log_path()
    return (
        "[Unit]\n"
        "Description=OpenProgram persistent worker (webui + channels)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -u -m openprogram worker run\n"
        f"WorkingDirectory={Path.home()}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install() -> int:
    unit_file = _unit_path()
    unit_file.parent.mkdir(parents=True, exist_ok=True)

    from openprogram.worker.lifecycle import current_worker_pid, stop_worker
    if current_worker_pid() is not None:
        stop_worker()

    unit_file.write_text(_build_unit())
    rc, msg = _systemctl("daemon-reload")
    if rc != 0:
        print(f"systemctl daemon-reload failed (rc={rc}): {msg}")
        return rc
    rc, msg = _systemctl("enable", "--now", UNIT_NAME)
    if rc != 0:
        print(f"systemctl enable --now failed (rc={rc}): {msg}")
        return rc

    print(f"openprogram worker installed as systemd user service ({UNIT_NAME}).")
    print(f"  unit:  {unit_file}")
    print(f"  logs:  {worker_paths.log_path()}")
    print()
    print("It is now running and will start at login.")
    print("To keep it running after logout:  sudo loginctl enable-linger $USER")
    print("Check status:  openprogram worker status")
    return 0


def uninstall() -> int:
    unit_file = _unit_path()
    if not unit_file.exists():
        print(f"openprogram worker: no systemd user unit at {unit_file}.")
        return 0
    _systemctl("disable", "--now", UNIT_NAME)
    try:
        unit_file.unlink()
    except OSError as e:
        print(f"failed to remove {unit_file}: {e}")
        return 1
    _systemctl("daemon-reload")
    print(f"openprogram worker uninstalled (removed {unit_file}).")
    return 0


def status() -> int:
    unit_file = _unit_path()
    print(f"systemd user unit: {unit_file}")
    print(f"  installed: {'yes' if unit_file.exists() else 'no'}")
    if not unit_file.exists():
        return 0
    rc, msg = _systemctl("is-enabled", UNIT_NAME)
    print(f"  enabled:   {msg or ('yes' if rc == 0 else 'no')}")
    rc, msg = _systemctl("is-active", UNIT_NAME)
    print(f"  active:    {msg or ('yes' if rc == 0 else 'no')}")
    return 0

"""Terminal exec backend section."""
from __future__ import annotations

from typing import Any


def run_backend_section() -> int:
    """Where shell-style tools (bash, execute_code, ...) actually run.

    Wizard surfaces local/docker/ssh so users can record intent; only
    'local' is currently implemented at runtime.
    """
    from openprogram.setup import _choose_one, _read_config, _text, _write_config
    cfg = _read_config()
    be = cfg.get("backend", {}) or {}
    cur_terminal = be.get("terminal") or "local"

    choices = ["local", "docker", "ssh"]
    picked = _choose_one("Terminal backend:", choices, cur_terminal)
    if picked is None:
        print("Cancelled.")
        return 1

    entry: dict[str, Any] = {"terminal": picked}
    if picked == "docker":
        image = _text("Container image:", default=be.get("docker_image", "ubuntu:24.04"))
        entry["docker_image"] = image or "ubuntu:24.04"
    elif picked == "ssh":
        host = _text("SSH host (user@host):", default=be.get("ssh_target", ""))
        entry["ssh_target"] = host or ""
    cfg["backend"] = entry
    _write_config(cfg)
    print(f"Terminal backend: {picked}")
    if picked != "local":
        print("[info] Only the 'local' backend is currently implemented at "
              "runtime. Your selection is stored for when other backends land.")
    return 0

"""Start the out-of-process controller for one update, on any platform.

The controller runs from ``versions/<controller_version>/venv`` and a service
manager restarts it when it exits abnormally. This module builds its command
and chooses the adapter; each adapter knows one service manager.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from ..store import SelfUpdateStore
from ..types import _validate_update_id
from ..versions import layout as paths

MODULE = "openprogram.operations.self_update.control.supervisor"
_PASSED = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP",
           "LANG", "LC_ALL", "OPENPROGRAM_PROFILE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
           "http_proxy", "https_proxy", "no_proxy")
_DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# Saved beside the update records with the same private permissions. The
# controller's own environment stays the short list above, because a service
# definition can be read by other programs; only a worker started directly gets this.
WORKER_ENVIRONMENT = "worker-environment.json"
_NOT_A_WORKER_SETTING = ("PYTHON", "VIRTUAL_ENV", "OPENPROGRAM_TRIAL", "__", "XPC_")


class ControllerLaunchError(RuntimeError):
    """The controller could not be handed to a service manager."""


def label(update_id: str) -> str:
    return f"ai.openprogram.self-update.{_validate_update_id(update_id)}"


def command(store: SelfUpdateStore, update_id: str) -> list[str]:
    version = store.load(update_id).request.controller_version
    if not version:
        raise ControllerLaunchError("this update names no controller version")
    layout = paths.for_state(store.root.parent)
    python = paths.venv_python(layout.version(version) / "venv")
    return [str(python), "-I", "-B", "-m", MODULE, _validate_update_id(update_id)]


def environment() -> dict[str, str]:
    values = {name: os.environ[name] for name in _PASSED if name in os.environ}
    values.setdefault("HOME", str(Path.home()))
    values["PATH"] = os.environ.get("PATH", _DEFAULT_PATH) if os.name == "nt" else _DEFAULT_PATH
    return values


def _encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def save_worker_environment(*, store: SelfUpdateStore | None = None) -> None:
    """Remember what this worker was started with: API keys, PATH entries and the like."""
    target = store or SelfUpdateStore()
    target.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    values = {name: value for name, value in os.environ.items()
              if not name.startswith(_NOT_A_WORKER_SETTING) and _encodable(value)}
    target._write_json(target.root / WORKER_ENVIRONMENT, {"schema": 1, "environment": values})


def worker_environment(*, store: SelfUpdateStore | None = None) -> dict[str, str] | None:
    """The environment for a worker this controller starts itself; ``None`` when none was saved."""
    target = store or SelfUpdateStore()
    try:
        values = target._read_json(target.root / WORKER_ENVIRONMENT)["environment"]
    except Exception:  # noqa: BLE001 - a worker with the short list still starts.
        return None
    if not isinstance(values, dict):
        return None
    return {**os.environ, **{str(name): str(value) for name, value in values.items()}}


def spawn_detached(argv: list[str], env: dict[str, str], log: Path) -> None:
    """Last resort where no service manager exists: nothing restarts it."""
    from openprogram.host._platform.process_ownership import no_window_creation_flags

    with open(log, "a", encoding="utf-8") as stream:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, env=env,
                         start_new_session=True, creationflags=no_window_creation_flags(), cwd=str(Path.home()))


def _adapter():
    if sys.platform == "darwin":
        from . import launcher as adapter
    elif sys.platform == "win32":
        from . import launcher_windows as adapter
    else:
        from . import launcher_linux as adapter
    return adapter


def launch_controller(update_id: str, *, store: SelfUpdateStore | None = None) -> None:
    target = store or SelfUpdateStore()
    log = target.root / update_id / "supervisor.log"
    _adapter().submit_controller(label(update_id), command(target, update_id), environment(), log)


def retire_controller(update_id: str) -> None:
    """Remove the service job; its update needs nothing more."""
    try:
        _adapter().retire_controller(label(update_id))
    except Exception:  # noqa: BLE001 - a job left behind exits at once and does no harm.
        import logging
        logging.getLogger(__name__).warning("controller job not removed", exc_info=True)


__all__ = ["ControllerLaunchError", "WORKER_ENVIRONMENT", "command", "environment", "label", "launch_controller",
           "retire_controller", "save_worker_environment", "spawn_detached", "worker_environment"]

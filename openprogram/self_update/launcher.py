"""Submit one trusted, one-shot self-update supervisor through launchd."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import time

from openprogram.self_update.store import SelfUpdateStore
from openprogram.self_update.types import TERMINAL_PHASES, UpdatePhase
from openprogram.store.session.git_session import atomic_write_text


class LaunchError(RuntimeError):
    """The one-shot supervisor could not be submitted safely."""


@dataclass(frozen=True)
class LaunchResult:
    label: str
    submitted: bool
    already_running: bool


def _launchctl(*args: str) -> tuple[int, str]:
    executable = Path("/bin/launchctl")
    if not executable.is_file() or executable.is_symlink():
        return 127, "launchctl not found"
    try:
        result = subprocess.run(
            [str(executable), *args],
            capture_output=True,
            text=True,
            timeout=15,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(Path.home()),
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return result.returncode, (result.stdout + result.stderr).strip()


def _controller_body(update_id: str, root: Path, installer_sha256: str, python: Path) -> str:
    from .controller_bundle import controller_environment
    arguments = [
        "/usr/bin/env", "-i",
        *(f"{name}={value}" for name, value in controller_environment().items()),
        str(python),
        "-I",
        "-B",
        "-m",
        "openprogram.self_update.supervisor",
        "--state-root",
        str(root),
        "--installer-sha256",
        installer_sha256,
        update_id,
    ]
    return "#!/bin/sh\nset -eu\nexec " + " ".join(map(shlex.quote, arguments)) + "\n"


def _ready_pid(update_dir: Path, update_id: str, installer_sha256: str) -> int | None:
    try:
        descriptor = os.open(update_dir / "supervisor.ready", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            value = json.loads(handle.read(4097))
        if (
            isinstance(value, dict) and value.get("schema") == 1
            and value.get("update_id") == update_id
            and value.get("installer_sha256") == installer_sha256
            and type(value.get("pid")) is int and value["pid"] > 0
        ):
            return value["pid"]
    except (OSError, ValueError, TypeError):
        pass
    return None


def _wait_ready(
    update_dir: Path,
    update_id: str,
    installer_sha256: str,
    expected_pid: int,
    timeout: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ready_pid(update_dir, update_id, installer_sha256) == expected_pid:
            try:
                os.kill(expected_pid, 0)
                return True
            except OSError:
                pass
        time.sleep(0.05)
    return False


def _submit_supervisor(
    store: SelfUpdateStore, update_id: str, *, resume: bool, repair_id: str | None = None
) -> tuple[LaunchResult, str, int]:
    update_dir = store.root / update_id
    from .controller_bundle import prepare_controller, _load_bundle
    try:
        bundle = _load_bundle(update_dir / "controller") if resume else prepare_controller(update_dir)
    except Exception as exc:
        raise LaunchError(f"trusted controller bundle is unavailable: {exc}") from exc
    installer_sha256 = bundle.installer_sha256
    controller = update_dir / "supervisor.sh"
    log = update_dir / "supervisor.log"
    if log.is_symlink() or (log.exists() and not log.is_file()):
        raise LaunchError("supervisor log path is not a regular file")
    body = _controller_body(update_id, store.root, installer_sha256, bundle.python)
    if controller.is_symlink() or (controller.exists() and not controller.is_file()):
        raise LaunchError("supervisor controller path is not a regular file")
    if resume and not controller.is_file():
        raise LaunchError("saved supervisor controller is missing")
    if controller.exists() and controller.read_text(encoding="utf-8") != body:
        raise LaunchError(
            "existing supervisor controller does not match trusted content"
        )
    if not resume:
        atomic_write_text(controller, body)
        controller.chmod(0o700)

    label = f"ai.openprogram.self-update.{update_id}"
    if repair_id is not None:
        label += f".repair.{repair_id}"
    domain = f"gui/{os.getuid()}/{label}"
    rc, message = _launchctl("print", domain)
    if rc != 0 and rc != 113 and "could not find service" not in message.lower() and "not found" not in message.lower():
        raise LaunchError(f"launchctl status failed ({rc}): {message}")
    submitted = rc != 0
    prior_pid = _ready_pid(update_dir, update_id, installer_sha256)
    if submitted:
        ready = update_dir / "supervisor.ready"
        if ready.exists() and not ready.is_symlink():
            ready.unlink()
        rc, message = _launchctl(
            "submit", "-l", label, "-o", str(log), "-e", str(log), "--", str(controller),
        )
        if rc != 0:
            raise LaunchError(f"launchctl submit failed ({rc}): {message}")
    # Without -k, launchd returns the existing PID or starts a stopped service.
    # The documented -p output avoids parsing launchctl print's diagnostic text.
    rc, message = _launchctl("kickstart", "-p", domain)
    if rc != 0:
        raise LaunchError(f"launchctl kickstart failed ({rc}): {message}")
    if not message.isascii() or not message.isdecimal() or int(message) <= 0:
        raise LaunchError("launchctl did not return a valid controller PID")
    pid = int(message)
    return LaunchResult(label, submitted, not submitted and prior_pid == pid), installer_sha256, pid


def launch_supervisor(update_id: str, *, resume: bool = False) -> LaunchResult:
    """Launch once, or resume only the originally saved trusted controller."""
    store = SelfUpdateStore()
    record = store.load(update_id)
    if record.request.update_id != update_id:
        raise LaunchError("self-update request identity mismatch")
    with store._locked():
        from .maintenance import load_maintenance
        from .owner_repair import load_repair, read_result, cleanup_error
        record = store._load_unlocked(update_id)
        repair_id = None
        if record.state.phase in TERMINAL_PHASES:
            marker = load_maintenance(store)
            repair = load_repair(store, record) if resume else None
            result = read_result(store, repair) if repair else None
            if repair is not None and cleanup_error(store, repair) is None and (result is None or result["status"] == "recovered"):
                repair_id = repair["repair_id"]
            if (not resume or (record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY and repair_id is None)
                or marker is None or marker["update_id"] != update_id):
                return LaunchResult(f"ai.openprogram.self-update.{update_id}", False, False)
            if store._load_active_unlocked() is not None:
                raise LaunchError("terminal maintenance conflicts with an active update")
        result, installer_sha256, pid = _submit_supervisor(store, update_id, resume=resume, repair_id=repair_id)
    if not _wait_ready(store.root / update_id, update_id, installer_sha256, pid):
        if resume and store.load(update_id).state.phase in TERMINAL_PHASES:
            return result
        if result.submitted and not resume:
            _launchctl("remove", result.label)
        raise LaunchError("submitted supervisor did not become ready")
    return result


__all__ = ["LaunchError", "LaunchResult", "launch_supervisor"]

"""Submit one trusted, one-shot self-update supervisor through launchd."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from openprogram.self_update.store import SelfUpdateStore
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


def _trusted_installer_source() -> Path:
    return Path(
        "/Applications/OpenProgram.app/Contents/Resources/update/install-app.sh"
    )


def _snapshot_installer(update_dir: Path) -> tuple[Path, str]:
    source = _trusted_installer_source()
    if not source.is_file() or source.is_symlink():
        raise LaunchError("trusted installed self-update installer is unavailable")
    try:
        content = source.read_bytes()
        content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LaunchError("trusted installed self-update installer is unreadable") from exc
    digest = hashlib.sha256(content).hexdigest()
    target = update_dir / "install-app.sh"
    if target.exists() or target.is_symlink():
        if (
            not target.is_file()
            or target.is_symlink()
            or hashlib.sha256(target.read_bytes()).hexdigest() != digest
        ):
            raise LaunchError("existing installer snapshot does not match trusted content")
    else:
        atomic_write_text(target, content.decode("utf-8"))
    target.chmod(0o700)
    return target, digest


def _controller_body(update_id: str, root: Path, installer_sha256: str) -> str:
    arguments = [
        sys.executable,
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


def _wait_ready(
    update_dir: Path,
    update_id: str,
    installer_sha256: str,
    timeout: float = 5.0,
) -> bool:
    deadline = time.time() + timeout
    marker = update_dir / "supervisor.ready"
    while time.time() < deadline:
        if marker.is_file() and not marker.is_symlink():
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
                pid = value.get("pid") if isinstance(value, dict) else None
                if (
                    isinstance(value, dict)
                    and value.get("schema") == 1
                    and value.get("update_id") == update_id
                    and value.get("installer_sha256") == installer_sha256
                    and isinstance(pid, int)
                    and pid > 0
                ):
                    os.kill(pid, 0)
                    return True
            except (OSError, ValueError, TypeError):
                pass
        time.sleep(0.05)
    return False


def launch_supervisor(update_id: str) -> LaunchResult:
    """Create the fixed controller script and submit its launchd job once."""
    store = SelfUpdateStore()
    record = store.load(update_id)
    if record.request.update_id != update_id:
        raise LaunchError("self-update request identity mismatch")
    update_dir = store.root / update_id
    _installer, installer_sha256 = _snapshot_installer(update_dir)
    controller = update_dir / "supervisor.sh"
    log = update_dir / "supervisor.log"
    if log.is_symlink() or (log.exists() and not log.is_file()):
        raise LaunchError("supervisor log path is not a regular file")
    body = _controller_body(update_id, store.root, installer_sha256)
    if controller.is_symlink() or (controller.exists() and not controller.is_file()):
        raise LaunchError("supervisor controller path is not a regular file")
    if controller.exists() and controller.read_text(encoding="utf-8") != body:
        raise LaunchError(
            "existing supervisor controller does not match trusted content"
        )
    atomic_write_text(controller, body)
    controller.chmod(0o700)

    label = f"ai.openprogram.self-update.{update_id}"
    domain = f"gui/{os.getuid()}/{label}"
    rc, message = _launchctl("print", domain)
    if rc == 0:
        if not _wait_ready(update_dir, update_id, installer_sha256):
            raise LaunchError("submitted supervisor did not become ready")
        return LaunchResult(label, submitted=False, already_running=True)
    if rc != 113 and "could not find service" not in message.lower() and "not found" not in message.lower():
        raise LaunchError(f"launchctl status failed ({rc}): {message}")
    ready = update_dir / "supervisor.ready"
    if ready.exists() and not ready.is_symlink():
        ready.unlink()
    rc, message = _launchctl(
        "submit",
        "-l",
        label,
        "-o",
        str(log),
        "-e",
        str(log),
        "--",
        str(controller),
    )
    if rc != 0:
        raise LaunchError(f"launchctl submit failed ({rc}): {message}")
    if not _wait_ready(update_dir, update_id, installer_sha256):
        _launchctl("remove", label)
        raise LaunchError("submitted supervisor did not become ready")
    return LaunchResult(label, submitted=True, already_running=False)


__all__ = ["LaunchError", "LaunchResult", "launch_supervisor"]

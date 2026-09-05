"""Linux systemd --user integration for the persistent worker.

Writes ``~/.config/systemd/user/openprogram-worker.service`` for the default
profile (or a profile-specific unit for a named profile) and runs ``systemctl
--user daemon-reload && enable --now`` so the worker starts immediately and on
every subsequent login.

For the unit to keep running after the user logs out (e.g. SSH session
ends), the user typically needs ``sudo loginctl enable-linger $USER``.
We surface a hint about that without running it ourselves — touching
sudo is out of scope for ``openprogram worker install``.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from openprogram.paths import get_active_profile
from openprogram.worker import paths as worker_paths

UNIT_NAME = "openprogram-worker.service"


def _unit_name() -> str:
    """Return a stable, filesystem-safe unit name for the active profile.

    Keep the historical name for the default profile.  Named profiles use a
    digest rather than embedding user input in a systemd identifier or path;
    this also lets multiple profile workers coexist without one profile's
    start/stop/install operation claiming another profile's service.
    """

    profile = get_active_profile()
    if profile is None:
        return UNIT_NAME
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:16]
    return f"openprogram-worker-profile-{digest}.service"


def _unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "systemd" / "user" / _unit_name()


def _systemctl(*args: str) -> tuple[int, str]:
    if shutil.which("systemctl") is None:
        return 127, "systemctl not found"
    try:
        out = subprocess.run(
            ["systemctl", "--user", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return 124, "systemctl --user timed out"
    except OSError as e:
        return 1, str(e)
    return out.returncode, (out.stdout + out.stderr).strip()


def _unit_quote(value: str, *, command_argument: bool = False) -> str:
    """Quote one systemd value without activating specifier expansion.

    Unit files treat ``%`` as a specifier even inside quotes.  ExecStart also
    expands ``$NAME``; doubling both metacharacters preserves literal paths
    such as ``/home/100%/Open Program/$runtime/python``.
    """

    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    escaped = escaped.replace("%", "%%")
    if command_argument:
        escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def _service_environment() -> dict[str, str]:
    """Environment a login service must retain from the CLI launcher.

    Managed runtimes locate bundled browsers and the GUI detector through
    these variables, while PATH is needed for user-installed tools and bwrap.
    Keep this an explicit non-secret allowlist: copying the complete install
    shell environment into a persistent unit would leak provider keys.
    """

    values = {"PYTHONUNBUFFERED": "1"}
    for name in (
        "PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "GPA_MODEL_PATH",
        "OPENPROGRAM_IMMUTABLE_RUNTIME",
        "OPENPROGRAM_STATE_DIR",
        "OPENPROGRAM_BIN_DIR",
        "OPENPROGRAM_PROFILE",
        "OPENPROGRAM_WEB_PORT",
        "OPENPROGRAM_WORKDIR",
        "OPENPROGRAM_NO_WEB",
    ):
        value = os.environ.get(name)
        if value:
            values[name] = value
    return values


def _build_unit() -> str:
    from openprogram.worker.lifecycle import _detached_worker_command

    command = " ".join(
        _unit_quote(arg, command_argument=True)
        for arg in _detached_worker_command()
    )
    log = str(worker_paths.log_path())
    environment = "".join(
        f"Environment={_unit_quote(name + '=' + value)}\n"
        for name, value in _service_environment().items()
    )
    return (
        "[Unit]\n"
        "Description=OpenProgram persistent worker (webui + channels)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        f"WorkingDirectory={_unit_quote(str(Path.home()))}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"StandardOutput={_unit_quote('append:' + log)}\n"
        f"StandardError={_unit_quote('append:' + log)}\n"
        f"{environment}"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _write_unit(unit_file: Path, content: bytes) -> None:
    """Atomically replace a user unit and make the file contents durable."""

    temporary = unit_file.with_name(f".{unit_file.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, unit_file)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_install(
    unit_file: Path,
    previous: bytes | None,
    *,
    unit_name: str,
    was_enabled: bool,
    was_active: bool,
    detached_was_running: bool,
) -> None:
    """Best-effort rollback after daemon-reload/enable fails."""

    _systemctl("disable", "--now", unit_name)
    try:
        if previous is None:
            unit_file.unlink(missing_ok=True)
        else:
            _write_unit(unit_file, previous)
    except OSError as exc:
        print(f"warning: could not restore previous systemd unit: {exc}")
    _systemctl("daemon-reload")
    if previous is not None:
        if was_enabled:
            _systemctl("enable", unit_name)
    _resume_previous_worker(
        unit_name=unit_name,
        was_active=was_active,
        detached_was_running=detached_was_running,
    )


def _resume_previous_worker(
    *,
    unit_name: str,
    was_active: bool,
    detached_was_running: bool,
) -> None:
    """Restore whichever worker ownership model was active before install."""

    if was_active:
        _systemctl("start", unit_name)
    elif detached_was_running:
        from openprogram.worker.lifecycle import spawn_detached

        spawn_detached(prefer_service=False)


def _print_systemd_error(action: str, rc: int, message: str) -> None:
    print(f"systemctl --user {action} failed (rc={rc}): {message}")
    lowered = message.lower()
    if (
        rc == 127
        or "not found" in lowered
        or "not been booted with systemd" in lowered
    ):
        print("This Linux host does not provide systemd user services.")
        print("Use `openprogram worker start` or the host's service manager instead.")
    elif "failed to connect to bus" in lowered or "no medium found" in lowered:
        print("No systemd user bus is available in this session.")
        print("Log in through a normal user session, or run `openprogram worker start`.")


def _linger_enabled() -> bool | None:
    """Return the current user's systemd linger state when discoverable."""

    executable = shutil.which("loginctl")
    user = os.environ.get("USER")
    if executable is None or not user:
        return None
    try:
        result = subprocess.run(
            [executable, "show-user", user, "--property=Linger", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value in {"yes", "true", "1"}:
        return True
    if value in {"no", "false", "0"}:
        return False
    return None


def install() -> int:
    unit_name = _unit_name()
    unit_file = _unit_path()

    # Verify the executable and the per-user bus before stopping a healthy
    # detached worker or touching an existing unit.  Minimal containers and
    # non-systemd WSL distributions commonly ship a `systemctl` binary that
    # cannot talk to a user manager.
    rc, msg = _systemctl("show-environment")
    if rc != 0:
        _print_systemd_error("show-environment", rc, msg)
        return rc or 1

    try:
        previous = unit_file.read_bytes() if unit_file.exists() else None
    except OSError as exc:
        print(f"failed to read existing systemd unit {unit_file}: {exc}")
        return 1
    enabled_rc, enabled_msg = _systemctl("is-enabled", unit_name)
    if enabled_rc not in {0, 1}:
        _print_systemd_error("is-enabled", enabled_rc, enabled_msg)
        return enabled_rc or 1
    active_rc, active_msg = _systemctl("is-active", unit_name)
    if active_rc not in {0, 3}:
        _print_systemd_error("is-active", active_rc, active_msg)
        return active_rc or 1
    was_enabled = enabled_rc == 0
    was_active = active_rc == 0

    from openprogram.worker.lifecycle import current_worker_pid, stop_worker
    running_pid = current_worker_pid()
    detached_was_running = running_pid is not None and not was_active
    if was_active:
        rc, msg = _systemctl("stop", unit_name)
        if rc != 0:
            _print_systemd_error("stop", rc, msg)
            return rc or 1
    elif running_pid is not None and stop_worker(prefer_service=False) != 0:
        return 1

    try:
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        _write_unit(unit_file, _build_unit().encode("utf-8"))
    except OSError as exc:
        print(f"failed to write systemd unit {unit_file}: {exc}")
        _resume_previous_worker(
            unit_name=unit_name,
            was_active=was_active,
            detached_was_running=detached_was_running,
        )
        return 1
    rc, msg = _systemctl("daemon-reload")
    if rc != 0:
        _print_systemd_error("daemon-reload", rc, msg)
        _restore_install(
            unit_file,
            previous,
            unit_name=unit_name,
            was_enabled=was_enabled,
            was_active=was_active,
            detached_was_running=detached_was_running,
        )
        return rc or 1
    rc, msg = _systemctl("enable", "--now", unit_name)
    if rc != 0:
        _print_systemd_error("enable --now", rc, msg)
        _restore_install(
            unit_file,
            previous,
            unit_name=unit_name,
            was_enabled=was_enabled,
            was_active=was_active,
            detached_was_running=detached_was_running,
        )
        return rc or 1

    print(f"openprogram worker installed as systemd user service ({unit_name}).")
    print(f"  unit:  {unit_file}")
    print(f"  logs:  {worker_paths.log_path()}")
    print()
    print("It is now running and will start at login.")
    linger = _linger_enabled()
    if linger is False:
        print("It will stop after logout because user lingering is disabled.")
        print("To keep it running after logout:  sudo loginctl enable-linger $USER")
    elif linger is None:
        print("To keep it running after logout, verify:  loginctl show-user $USER -p Linger")
    print("Check status:  openprogram worker status")
    return 0


def start_if_installed() -> int | None:
    """Start the managed service, or return ``None`` when none is installed.

    Reusing the transactional installer deliberately refreshes ``ExecStart``
    and the packaged-runtime environment.  That matters after an immutable
    CLI upgrade: a plain ``systemctl start`` could otherwise keep launching a
    Python path captured from the previous release.
    """

    if not _unit_path().is_file():
        return None
    return install()


def stop_if_installed() -> int | None:
    """Stop the systemd-owned worker without replacing it with a detached one."""

    if not _unit_path().is_file():
        return None
    unit_name = _unit_name()
    rc, message = _systemctl("stop", unit_name)
    if rc != 0:
        _print_systemd_error("stop", rc, message)
        return rc or 1

    # Clean up a detached worker left by an older CLI whose restart command did
    # not understand service ownership.  Bypass service dispatch to avoid
    # recursing back into this function.
    from openprogram.worker.lifecycle import current_worker_pid, stop_worker

    if current_worker_pid() is not None:
        return stop_worker(prefer_service=False)
    print(f"openprogram worker stopped via systemd ({unit_name}).")
    return 0


def restart_if_installed() -> int | None:
    """Refresh and restart an installed unit using the current CLI runtime."""

    if not _unit_path().is_file():
        return None
    return install()


def uninstall() -> int:
    unit_name = _unit_name()
    unit_file = _unit_path()
    if not unit_file.exists():
        print(f"openprogram worker: no systemd user unit at {unit_file}.")
        return 0
    disable_rc, disable_msg = _systemctl("disable", "--now", unit_name)
    if disable_rc != 0:
        _print_systemd_error("disable --now", disable_rc, disable_msg)
        print(f"The unit file was kept at {unit_file}; uninstall was not applied.")
        return disable_rc or 1
    try:
        unit_file.unlink()
    except OSError as e:
        print(f"failed to remove {unit_file}: {e}")
        return 1
    reload_rc, reload_msg = _systemctl("daemon-reload")
    print(f"openprogram worker uninstalled (removed {unit_file}).")
    if reload_rc != 0:
        _print_systemd_error("daemon-reload", reload_rc, reload_msg)
        print("The service is stopped and disabled; systemd still needs a daemon reload.")
    return reload_rc


def status() -> int:
    unit_name = _unit_name()
    unit_file = _unit_path()
    print(f"systemd user unit: {unit_file}")
    print(f"  installed: {'yes' if unit_file.exists() else 'no'}")
    if not unit_file.exists():
        return 0
    rc, msg = _systemctl("is-enabled", unit_name)
    if rc not in {0, 1}:
        _print_systemd_error("is-enabled", rc, msg)
        return rc or 1
    print(f"  enabled:   {'yes' if rc == 0 else 'no'}")
    rc, msg = _systemctl("is-active", unit_name)
    if rc not in {0, 3}:
        _print_systemd_error("is-active", rc, msg)
        return rc or 1
    print(f"  active:    {'yes' if rc == 0 else 'no'}")
    return 0

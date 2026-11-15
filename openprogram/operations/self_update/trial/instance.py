"""One version running as a trial: a separate process on another port.

It runs on a copied temporary data directory and starts through the signed
launcher pointed at a fake root, so the launcher path is exercised too.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import ntpath
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Mapping

from ..verification.system_probe import ProbeTarget
from ..versions import layout as paths
from ..versions import pointer
from ..versions.layout import Layout
from ..versions.removal import remove_tree
from . import sandbox

_log = logging.getLogger(__name__)

COLD_START_SECONDS = 120
MIGRATION_LIMIT_SECONDS = 30 * 60
_MIGRATION_FRESH_SECONDS = 15
_STOP_SECONDS = 20
KEPT_FAILED_SECONDS = 7 * 86400
LIVE_PORTS = (18100, 18200)
_LOOPBACK_HOSTS = "127.0.0.1,localhost,::1"
_POSIX_SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
WINDOWS_NOTE = (
    "Windows has no OS sandbox for a trial yet: the temporary data directory, "
    "the separate port and the sanitized copy are the only isolation."
)


class TrialError(RuntimeError):
    """A check failed; the message is the reason the owner reads."""


def windows_system_path(system_root: str) -> tuple[str, ...]:
    """The directories Windows itself puts on ``PATH``.

    ``System32`` holds ``cmd``, ``taskkill``, ``schtasks`` and ``where``, and
    PowerShell has a directory of its own; ``%SystemRoot%`` alone finds none of
    them from ``cmd`` or ``shutil.which``.
    """
    system32 = ntpath.join(system_root, "System32")
    return (system32, system_root, ntpath.join(system32, "Wbem"),
            ntpath.join(system32, "WindowsPowerShell", "v1.0"))


def _remove(path: Path) -> None:
    left = remove_tree(path)
    if left:
        _log.warning("a trial left %d entries behind under %s: %s", len(left), path, ", ".join(left[:5]))


def _link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=target.is_dir())


def _live_ports(state: Path) -> tuple[int, ...]:
    ports = set(LIVE_PORTS)
    try:
        ports.add(int((state / "worker.port").read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    return tuple(sorted(ports))


@dataclass(frozen=True)
class Workspace:
    """``trials/<update>`` and what is in it."""

    directory: Path

    @property
    def home(self) -> Path:
        return self.directory / "home"

    @property
    def state_dir(self) -> Path:
        return self.home / ".openprogram"

    @property
    def fake_root(self) -> Path:
        return self.directory / "fake-root"

    @property
    def temporary(self) -> Path:
        return self.directory / "tmp"

    @property
    def profile(self) -> Path:
        return self.directory / "sandbox.sb"

    def create(self) -> "Workspace":
        for path in (self.directory, self.home, self.temporary):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self

    def finish(self, *, passed: bool) -> None:
        """Gone on pass. On failure only logs and evidence stay, for 7 days."""
        if passed:
            _remove(self.directory)
            return
        for path in (self.home, self.fake_root, self.temporary):
            _remove(path)
        _remove(sandbox.relay_directory(self.directory))
        if self.directory.exists():
            os.utime(self.directory)


def workspace_for(layout: Layout, update_id: str) -> Workspace:
    return Workspace(layout.trials / paths.require_id(update_id, "update id"))


def prune_trials(layout: Layout, *, now: float | None = None) -> list[str]:
    """Remove what failed trials left, once it is 7 days old."""
    moment = time.time() if now is None else now
    removed: list[str] = []
    if not layout.trials.is_dir():
        return removed
    for entry in layout.trials.iterdir():
        try:
            if entry.is_dir() and moment - entry.stat().st_mtime > KEPT_FAILED_SECONDS:
                _remove(entry)
                if not entry.exists():
                    removed.append(entry.name)
        except OSError:
            continue
    return removed


def _prepare_fake_root(workspace: Workspace, layout: Layout, version: str, base: str) -> Layout:
    """A root of its own whose ``versions/`` entry links to the real version."""
    fake = Layout(workspace.fake_root)
    shutil.rmtree(fake.root, ignore_errors=True)
    fake.root.mkdir(parents=True)
    # The launcher wants a real directory here; what is inside it is the version's own.
    fake.version(version).mkdir(parents=True)
    for entry in layout.version(version).iterdir():
        if entry.is_dir():
            _link(entry, fake.version(version) / entry.name)
        else:
            shutil.copy2(entry, fake.version(version) / entry.name)
    _link(layout.base(base), fake.base(base))
    bootstrap = layout.root / "bootstrap"
    if bootstrap.is_dir():
        _link(bootstrap, fake.root / "bootstrap")
    pointer.create(fake, pointer.Pointer(generation=1, version=version, base=base, writer="trial"))
    return fake


class TrialInstance:
    """Start, wait for and stop one version on the trial's data."""

    def __init__(self, workspace: Workspace, layout: Layout, version: str, base: str, *,
                 port: int, model_port: int, live_state: Path, launcher: Path | None = None,
                 label: str = "candidate", extra_readable: tuple[Path, ...] = ()) -> None:
        self.workspace = workspace
        self.layout = layout
        self.version = version
        self.base = base
        self.port = port
        self.model_port = model_port
        self.live_state = Path(live_state)
        self.launcher = Path(launcher) if launcher is not None else layout.launcher()
        self.label = label
        self.log_path = workspace.directory / f"{label}.log"
        self.extra_readable = extra_readable
        self.migration_seconds = 0.0
        self.process: subprocess.Popen | None = None
        self._relay: sandbox.HostRelay | None = None
        self._fake: Layout | None = None

    @property
    def policy(self) -> sandbox.TrialPolicy:
        readable = (self.layout.version(self.version), self.layout.base(self.base),
                    self.layout.root / "identity", self.layout.root / "bootstrap",
                    self.launcher.parent, *self.extra_readable)
        return sandbox.TrialPolicy(
            directory=self.workspace.directory, live_state=self.live_state,
            readable=tuple(path for path in readable if path.exists()),
            worker_port=self.port, model_port=self.model_port, live_ports=_live_ports(self.live_state),
        )

    def environment(self, *, windows: bool | None = None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        """Nothing of the owner's environment; a credential cannot leak in through it.

        ``windows`` and ``environ`` default to this machine; a test names them.
        """
        windows = os.name == "nt" if windows is None else windows
        environ = os.environ if environ is None else environ
        home, temporary = str(self.workspace.home), str(self.workspace.temporary)
        python_bin = paths.base_python(self.layout.base(self.base)).parent
        system_root = environ.get("SystemRoot", r"C:\Windows")
        system = windows_system_path(system_root) if windows else _POSIX_SYSTEM_PATH
        environment = {
            "HOME": home, "USERPROFILE": home, "TMPDIR": temporary, "TEMP": temporary, "TMP": temporary,
            "OPENPROGRAM_WEB_PORT": str(self.port), "OPENPROGRAM_TRIAL": "1",
            "PATH": (";" if windows else ":").join((str(python_bin), *system)),
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "PYTHONUNBUFFERED": "1",
            # Setting these also stops Python consulting the system's proxy
            # settings, whose loopback proxy the sandbox would refuse; the
            # fake model and the trial's worker are reached directly.
            "NO_PROXY": _LOOPBACK_HOSTS, "no_proxy": _LOOPBACK_HOSTS,
        }
        if windows:
            environment["SystemRoot"] = system_root
        return environment

    def command(self, *arguments: str) -> list[str]:
        """``arguments`` for the version's Python, through launcher and sandbox."""
        if self._fake is None:
            self._fake = _prepare_fake_root(self.workspace, self.layout, self.version, self.base)
        argv = [str(self.launcher), "--root", str(self._fake.root), *arguments]
        python = str(paths.base_python(self.layout.base(self.base)))
        return sandbox.wrap_command(self.policy, argv, profile_path=self.workspace.profile, python=python)

    def start(self) -> None:
        if self.process is not None:
            raise TrialError("this trial instance has already started")
        self._fake = _prepare_fake_root(self.workspace, self.layout, self.version, self.base)
        if not self.launcher.is_file():
            raise TrialError(f"the launcher is missing: {self.launcher}")
        if sandbox.needs_host_relay():
            self._relay = sandbox.HostRelay(self.workspace.directory, self.port, self.model_port)
            self._relay.start()
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(
                self.command("-u", "-m", "openprogram", "worker", "run"), cwd=self.workspace.home,
                env=self.environment(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )

    def _healthy(self, client) -> bool:
        try:
            return client.get(f"http://127.0.0.1:{self.port}/healthz", follow_redirects=False).status_code == 200
        except Exception:  # noqa: BLE001 - not up yet, whatever the transport says
            return False

    def _migrating(self) -> bool:
        try:
            marker = Layout(self.workspace.fake_root).migrating(self.version)
            return time.time() - marker.stat().st_mtime <= _MIGRATION_FRESH_SECONDS
        except OSError:
            return False

    def wait_ready(self, *, seconds: float = COLD_START_SECONDS,
                   migration_limit: float = MIGRATION_LIMIT_SECONDS) -> float:
        """Seconds to ``/healthz``; a moving migration extends the limit."""
        from openprogram.host.security.safe_http import OutboundSecurityConfig, safe_client
        from openprogram.host.security.url_policy import OwnerURLException

        started = time.monotonic()
        deadline = started + seconds
        last = started
        origin = f"http://127.0.0.1:{self.port}"
        security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
        with safe_client("runtime.local_probe", configured_origin=origin, security=security, timeout=2,
                         overall_timeout=migration_limit + seconds) as client:
            return self._wait(client, started, deadline, last, migration_limit)

    def _wait(self, client, started: float, deadline: float, last: float, migration_limit: float) -> float:
        while True:
            now = time.monotonic()
            if self.process is None or self.process.poll() is not None:
                code = None if self.process is None else self.process.returncode
                raise TrialError(f"the {self.label} version exited during start (code {code})")
            if self._healthy(client):
                return now - started
            if self._migrating():
                self.migration_seconds += now - last
                deadline = max(deadline, min(now + _MIGRATION_FRESH_SECONDS, started + migration_limit))
            last = now
            if now >= deadline:
                raise TrialError(f"the {self.label} version did not answer /healthz within {int(deadline - started)} seconds")
            time.sleep(0.25)

    def target(self, doctor_baseline: Mapping[str, bool] | None = None) -> ProbeTarget:
        return ProbeTarget(port=self.port, state_dir=self.workspace.state_dir, doctor_baseline=doctor_baseline)

    def worker_pid(self) -> int:
        return int((self.workspace.state_dir / "worker.lock").read_text(encoding="utf-8").strip())

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            if os.name == "nt":
                from openprogram.host._platform.processes import kill_process_tree

                kill_process_tree(process.pid)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if process is not None and os.name != "nt":
            # The group is gone only when the whole of it is: the worker's children too.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait(timeout=_STOP_SECONDS)
        if self._relay is not None:
            self._relay.stop()
            self._relay = None

    def __enter__(self) -> "TrialInstance":
        self.start()
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.stop()

    def with_label(self, label: str, version: str, base: str) -> "TrialInstance":
        return TrialInstance(self.workspace, self.layout, version, base, port=self.port,
                             model_port=self.model_port, live_state=self.live_state, launcher=self.launcher,
                             label=label, extra_readable=self.extra_readable)


__all__ = ["COLD_START_SECONDS", "MIGRATION_LIMIT_SECONDS", "TrialError", "TrialInstance", "WINDOWS_NOTE",
           "Workspace", "prune_trials", "workspace_for"]

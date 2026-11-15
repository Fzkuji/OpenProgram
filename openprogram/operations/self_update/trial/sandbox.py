"""What a sandboxed self-update step may read, write and reach.

A trial writes only inside its own directory, cannot read the live state
directory, and reaches only loopback. macOS uses a sandbox profile and Linux
uses bubblewrap, proved by running it, because Ubuntu 24.04 installs it and then
forbids the namespaces it needs. Where the OS cannot confine a trial, the
temporary data directory, the port and the sanitized copy are the only
isolation, and every such result says so and why.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import socket
import sys
import threading
from typing import Sequence

_BLOCKED_HOME_DIRECTORIES = (".ssh", ".aws", ".gnupg", ".claude", ".config", "Library/Keychains")
_BLOCKED_HOME_FILES = (".claude.json", ".netrc")
LOCAL_SIGNING = "Library/Application Support/OpenProgram/local-signing"


def _sandbox_profile(
    candidate: Path,
    artifact_root: Path,
    build_home: Path,
    build_tmp: Path,
    *,
    loopback_port: int | None = None,
    trusted_input: Path | None = None,
) -> str:
    def quoted(path: Path) -> str:
        return json.dumps(str(path))

    rules = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork)",
        "(allow process-info* (target same-sandbox))",
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix-sem ipc-posix-shm)",
        "(allow sysctl-read (sysctl-name-prefix \"hw.\"))",
        # uname reads all four fields even when the caller only requests -s.
        '(allow sysctl-read (sysctl-name "kern.ostype") (sysctl-name "kern.osrelease") '
        '(sysctl-name "kern.version") (sysctl-name "kern.hostname"))',
        '(allow sysctl-read (sysctl-name "machdep.cpu.brand_string"))',
        "(deny network*)",
        f"(deny file-read* (subpath {quoted(Path.home() / '.openprogram')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.ssh')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.aws')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.gnupg')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.claude')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.config')}))",
        f"(deny file-read* (literal {quoted(Path.home() / '.claude.json')}))",
        f"(deny file-read* (literal {quoted(Path.home() / '.netrc')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / 'Library/Keychains')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / 'Library/Application Support/OpenProgram/local-signing')}))",
        '(allow file-ioctl file-read-data file-write-data (literal "/dev/null"))',
        '(allow file-read-data (literal "/dev/zero"))',
        '(allow file-read-data (literal "/dev/random"))',
        '(allow file-read-data (literal "/dev/urandom"))',
        f"(allow file-write* (subpath {quoted(candidate)}))",
        f"(allow file-write* (subpath {quoted(artifact_root)}))",
        f"(allow file-write* (subpath {quoted(build_home)}))",
        f"(allow file-write* (subpath {quoted(build_tmp)}))",
        # Agent worktrees live under ~/.openprogram/worktrees by default.
        f"(allow file-read* (subpath {quoted(candidate)}))",
        f"(allow file-read* (subpath {quoted(artifact_root)}))",
        f"(allow file-read* (subpath {quoted(build_home)}))",
        f"(allow file-read* (subpath {quoted(build_tmp)}))",
        '(deny file-read* (subpath "/Applications/OpenProgram.app"))',
        '(deny file-read* (regex #".*/\\.env($|/).*$"))',
    ]
    protected_root = (Path.home() / ".openprogram").resolve()
    metadata_paths: set[Path] = set()
    readable_roots = [candidate, artifact_root, build_home, build_tmp]
    if trusted_input is not None:
        readable_roots.append(trusted_input.parent)
    for root in readable_roots:
        current = root.resolve()
        if not current.is_relative_to(protected_root):
            continue
        while current.is_relative_to(protected_root):
            metadata_paths.add(current)
            if current == protected_root:
                break
            current = current.parent
    for path in sorted(metadata_paths):
        rules.append(f"(allow file-read-metadata (literal {quoted(path)}))")
    if trusted_input is not None:
        rules.append(f"(allow file-read* (literal {quoted(trusted_input)}))")
    if loopback_port is not None:
        if type(loopback_port) is not int or not 1024 <= loopback_port <= 65535 or loopback_port == 18100:
            raise ValueError("invalid packaged smoke port")
        rules.append(f'(allow network* (local ip "localhost:{loopback_port}") '
                     f'(remote ip "localhost:{loopback_port}"))')
    return "\n".join(rules) + "\n"


@dataclass(frozen=True)
class TrialPolicy:
    """The limits of one sandboxed command."""

    directory: Path
    live_state: Path
    readable: tuple[Path, ...]
    worker_port: int
    model_port: int
    live_ports: tuple[int, ...] = ()
    writable: tuple[Path, ...] = ()
    home: Path | None = None  # Whose credential directories are denied.


def _quoted(path: Path) -> str:
    return json.dumps(str(path))


def trial_profile(policy: TrialPolicy) -> str:
    """The macOS profile. The last matching rule wins, so denials come first."""
    home = (policy.home or Path.home()).resolve()
    directory = policy.directory.resolve()
    live = policy.live_state.resolve()
    readable = [path.resolve() for path in policy.readable]
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork)",
        "(allow process-info* (target same-sandbox))",
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix-sem ipc-posix-shm)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow pseudo-tty)",
        "(allow file-ioctl)",
        "(deny network*)",
        f"(deny file-read* (subpath {_quoted(live)}))",
    ]
    rules += [f"(deny file-read* (subpath {_quoted(home / name)}))" for name in _BLOCKED_HOME_DIRECTORIES]
    rules += [f"(deny file-read* (literal {_quoted(home / name)}))" for name in _BLOCKED_HOME_FILES]
    rules.append(f"(deny file-read* (subpath {_quoted(home / LOCAL_SIGNING)}))")
    rules.append('(deny file-read* (regex #".*/\\.env($|/).*$"))')
    rules += [
        '(allow file-write* (literal "/dev/null"))',
        '(allow file-write* (literal "/dev/zero"))',
        '(allow file-write* (literal "/dev/dtracehelper"))',
        '(allow file-write* (regex #"^/dev/(ttys[0-9]+|tty|ptmx|fd/[0-9]+)$"))',
    ]
    for root in (directory, *[path.resolve() for path in policy.writable]):
        rules.append(f"(allow file-read* file-write* (subpath {_quoted(root)}))")
    metadata: set[Path] = set()
    for root in (directory, *readable):
        rules.append(f"(allow file-read* (subpath {_quoted(root)}))")
        current = root
        while current != current.parent and current.is_relative_to(live):
            metadata.add(current)
            current = current.parent
    rules += [f"(allow file-read-metadata (literal {_quoted(path)}))" for path in sorted(metadata)]
    rules.append(f"(allow file-read-metadata (literal {_quoted(live)}))")
    # A "local" filter also matches an outbound connection, so it may only
    # ever sit on bind and inbound; outbound is limited by where it goes: the
    # trial's own worker and fake model. Any other loopback port could be a
    # proxy to the Internet, or another service of the owner's.
    rules += [
        '(allow network-bind network-inbound (local ip "localhost:*"))',
        f'(allow network-outbound (remote ip "localhost:{policy.worker_port}"))',
        f'(allow network-outbound (remote ip "localhost:{policy.model_port}"))',
        f'(allow network-bind network-inbound (local unix-socket (subpath {_quoted(directory)})))',
        f'(allow network-outbound (remote unix-socket (subpath {_quoted(directory)})))',
    ]
    rules += [f'(deny network-outbound (remote ip "localhost:{port}"))' for port in policy.live_ports]
    return "\n".join(rules) + "\n"


def _bubblewrap_command(policy: TrialPolicy, argv: Sequence[str], relay: Sequence[str]) -> list[str]:
    """Filesystem rules as on macOS; a network namespace with only loopback."""
    command = ["bwrap", "--die-with-parent", "--unshare-net", "--unshare-pid", "--new-session",
               "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", str(policy.live_state.resolve())]
    home = (policy.home or Path.home()).resolve()
    for name in (*_BLOCKED_HOME_DIRECTORIES, LOCAL_SIGNING):
        target = home / name
        if target.is_dir():
            command += ["--tmpfs", str(target)]
    for path in policy.readable:
        resolved = path.resolve()
        command += ["--ro-bind", str(resolved), str(resolved)]
    for root in (policy.directory, *policy.writable):
        resolved = root.resolve()
        command += ["--bind", str(resolved), str(resolved)]
    return [*command, "--", *relay, *argv]


def isolation_reason() -> str | None:
    """Why the OS cannot confine a trial here, or ``None`` when it can."""
    if sys.platform == "darwin":
        return None if shutil.which("sandbox-exec", path="/usr/bin") else "macOS needs /usr/bin/sandbox-exec"
    if sys.platform.startswith("linux"):
        from openprogram.host.sandbox import bubblewrap_unavailable_reason

        return bubblewrap_unavailable_reason()
    return f"{sys.platform} has no sandbox for trials yet"


def wrap_with_no_network(policy: TrialPolicy, argv: Sequence[str]) -> list[str]:
    """``argv`` confined on Linux with loopback only and nothing relayed; unchanged on Windows."""
    if sys.platform.startswith("linux") and isolation_reason() is None:
        return _bubblewrap_command(policy, argv, ())
    return list(argv)


def isolation_level() -> str:
    """``sandbox`` where the OS confines a trial, otherwise ``data-only``."""
    return "data-only" if isolation_reason() else "sandbox"


def record_isolation(evidence: Path) -> None:
    """Keep with the trial's evidence how it was isolated, and why not more."""
    payload = {"level": isolation_level(), "reason": isolation_reason()}
    (evidence / "isolation.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def wrap_command(policy: TrialPolicy, argv: Sequence[str], *, profile_path: Path, python: str | None = None) -> list[str]:
    """``argv`` as it runs under ``policy`` on this platform."""
    if isolation_level() != "sandbox":
        return list(argv)
    if sys.platform == "darwin":
        profile_path.write_text(trial_profile(policy), encoding="utf-8")
        return ["/usr/bin/sandbox-exec", "-f", str(profile_path), *argv]
    relay = [python or sys.executable, "-I", "-B", str(install_relay(policy.directory)),
             str(relay_directory(policy.directory)), str(policy.worker_port), str(policy.model_port), "--"]
    return _bubblewrap_command(policy, argv, relay)


# ---- Linux: reaching a network namespace that has only loopback ----------

_RELAY_SOURCE = '''
import os, socket, subprocess, sys, threading

def pump(source, target):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass
    finally:
        for end in (source, target):
            try:
                end.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

def accept(listener, connect):
    while True:
        client, _ = listener.accept()
        try:
            upstream = connect()
        except OSError:
            client.close()
            continue
        for pair in ((client, upstream), (upstream, client)):
            threading.Thread(target=pump, args=pair, daemon=True).start()

def tcp(port):
    return lambda: socket.create_connection(("127.0.0.1", port))

def unix(path):
    def connect():
        end = socket.socket(socket.AF_UNIX)
        end.connect(path)
        return end
    return connect

directory, worker, model, _, *command = sys.argv[1:]
inbound = socket.socket(socket.AF_UNIX)
inbound.bind(os.path.join(directory, "worker.sock"))
inbound.listen(64)
outbound = socket.socket()
outbound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
outbound.bind(("127.0.0.1", int(model)))
outbound.listen(64)
threading.Thread(target=accept, args=(inbound, tcp(int(worker))), daemon=True).start()
threading.Thread(target=accept, args=(outbound, unix(os.path.join(directory, "model.sock"))), daemon=True).start()
raise SystemExit(subprocess.call(command))
'''


def relay_directory(directory: Path) -> Path:
    return directory / "relay"


def install_relay(directory: Path) -> Path:
    """The script that runs inside the namespace, ahead of the real command."""
    target = directory / "relay.py"
    target.write_text(_RELAY_SOURCE, encoding="utf-8")
    relay_directory(directory).mkdir(exist_ok=True)
    return target


class HostRelay:
    """The controller's end of the Linux relay.

    The controller reaches the worker through ``worker.sock`` and the worker
    reaches the fake model through ``model.sock``; nothing else crosses into
    the namespace.
    """

    def __init__(self, directory: Path, worker_port: int, model_port: int) -> None:
        self._directory = relay_directory(directory)
        self._worker_port = worker_port
        self._model_port = model_port
        self._listeners: list[socket.socket] = []

    @staticmethod
    def _pump(source: socket.socket, target: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                target.sendall(data)
        except OSError:
            pass
        finally:
            for end in (source, target):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _serve(self, listener: socket.socket, connect) -> None:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            try:
                upstream = connect()
            except OSError:
                client.close()
                continue
            for pair in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=pair, daemon=True).start()

    def start(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        worker_end = socket.socket()
        worker_end.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        worker_end.bind(("127.0.0.1", self._worker_port))
        worker_end.listen(64)
        model_end = socket.socket(socket.AF_UNIX)
        path = self._directory / "model.sock"
        path.unlink(missing_ok=True)
        model_end.bind(str(path))
        model_end.listen(64)
        self._listeners = [worker_end, model_end]

        def to_worker() -> socket.socket:
            end = socket.socket(socket.AF_UNIX)
            end.connect(str(self._directory / "worker.sock"))
            return end

        def to_model() -> socket.socket:
            return socket.create_connection(("127.0.0.1", self._model_port))

        threading.Thread(target=self._serve, args=(worker_end, to_worker), daemon=True).start()
        threading.Thread(target=self._serve, args=(model_end, to_model), daemon=True).start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.close()
        self._listeners = []
        (self._directory / "model.sock").unlink(missing_ok=True)
        (self._directory / "worker.sock").unlink(missing_ok=True)


def needs_host_relay() -> bool:
    return sys.platform.startswith("linux") and isolation_level() == "sandbox"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


__all__ = [
    "HostRelay", "TrialPolicy", "free_port", "isolation_level", "isolation_reason", "needs_host_relay",
    "record_isolation", "trial_profile", "wrap_command", "_sandbox_profile",
]

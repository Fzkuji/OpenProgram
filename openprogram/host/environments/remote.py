"""Remote filesystem access for SSH environments.

File tools in an SSH conversation read and write the remote disk. Every
operation is one shell command over the environment's multiplexed SSH
connection; file contents travel as raw bytes on stdin/stdout, so
nothing is lost to text decoding.

Read-before-edit protection works like the local one: a successful
read records the file's SHA-256 for the session, and a later edit or
overwrite is refused when the file was never read or has changed since.
"""
from __future__ import annotations

import hashlib
import shlex
import threading
from dataclasses import dataclass
from typing import Optional

from openprogram.host.environments import Environment, current_environment


MAX_READ_BYTES = 32 * 1024 * 1024
_workdirs: dict[tuple[str, str], str] = {}
_workdirs_lock = threading.Lock()
_seen: dict[tuple[str, str, str], str] = {}
_seen_lock = threading.Lock()

NEVER_READ = "never_read"
STALE = "stale"
FRESH = "fresh"


def _q(path: str) -> str:
    return shlex.quote(path)


@dataclass
class RemoteStat:
    exists: bool
    is_dir: bool = False
    size: int = 0


class RemoteFS:
    """Byte-level file operations on one SSH environment."""

    def __init__(self, env: Environment) -> None:
        from openprogram.host.backend.ssh import SshBackend
        self.env = env
        self.backend = SshBackend(env.ssh_target, env.workdir)

    def _run(self, command: str, data: bytes | None = None,
             timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        code, out, err = self.backend.run_bytes(command, data=data, timeout=timeout)
        if code == 255:
            raise ConnectionError(
                f"ssh to {self.env.ssh_target} failed: "
                f"{err.decode('utf-8', 'replace').strip() or 'connection error'}")
        if code in (-1, 127):
            raise ConnectionError(err.decode("utf-8", "replace").strip())
        return code, out, err

    def stat(self, path: str) -> RemoteStat:
        code, out, _ = self._run(
            f"if [ -d {_q(path)} ]; then echo d; "
            f"elif [ -e {_q(path)} ]; then echo f; wc -c < {_q(path)}; "
            f"else echo n; fi")
        lines = out.decode("utf-8", "replace").split()
        if not lines or lines[0] == "n":
            return RemoteStat(False)
        if lines[0] == "d":
            return RemoteStat(True, is_dir=True)
        size = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0
        return RemoteStat(True, size=size)

    def read_bytes(self, path: str) -> bytes:
        info = self.stat(path)
        if not info.exists:
            raise FileNotFoundError(path)
        if info.is_dir:
            raise IsADirectoryError(path)
        if info.size > MAX_READ_BYTES:
            raise OSError(f"{path} is {info.size} bytes; the remote read limit is "
                          f"{MAX_READ_BYTES} bytes")
        code, out, err = self._run(f"cat -- {_q(path)}", timeout=120)
        if code != 0:
            raise OSError(err.decode("utf-8", "replace").strip() or f"cat exited {code}")
        return out

    def write_bytes(self, path: str, data: bytes) -> None:
        parent = path.rsplit("/", 1)[0] or "/"
        # Write to a sibling temp file and rename, so a dropped connection
        # never leaves a half-written target behind.
        tmp = f"{path}.openprogram-tmp"
        code, _, err = self._run(
            f"mkdir -p -- {_q(parent)} && cat > {_q(tmp)} && mv -f -- {_q(tmp)} {_q(path)}",
            data=data, timeout=120)
        if code != 0:
            self._run(f"rm -f -- {_q(tmp)}")
            raise OSError(err.decode("utf-8", "replace").strip() or f"write exited {code}")

    def delete(self, path: str) -> None:
        code, _, err = self._run(f"rm -f -- {_q(path)}")
        if code != 0:
            raise OSError(err.decode("utf-8", "replace").strip())

    def shell(self, command: str, timeout: float = 60.0) -> tuple[int, str, str]:
        code, out, err = self._run(command, timeout=timeout)
        return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def remote_fs() -> Optional[RemoteFS]:
    """The remote filesystem the current tool call must use, or None when
    file tools act on the host disk (local and Docker environments)."""
    try:
        env = current_environment()
    except Exception:
        return None
    return RemoteFS(env) if env.remote_files else None


def remote_workdir(env: Environment) -> Optional[str]:
    """Absolute remote folder an SSH conversation starts in.

    ``~`` and an empty setting resolve through the remote shell once per
    process. Returns None when the host can't be reached; the turn then
    starts with no folder binding and tools need absolute paths.
    """
    wanted = env.workdir or "~"
    key = (env.ssh_target, wanted)
    with _workdirs_lock:
        cached = _workdirs.get(key)
    if cached:
        return cached
    if wanted.startswith("/"):
        resolved = wanted
    else:
        from openprogram.host.backend.ssh import SshBackend
        backend = SshBackend(env.ssh_target, wanted)
        result = backend.run("pwd", timeout=20)
        line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if result.exit_code != 0 or not line.startswith("/"):
            return None
        resolved = line
    with _workdirs_lock:
        _workdirs[key] = resolved
    return resolved


# ---- read-before-edit tracking -------------------------------------------


def _seen_key(env: Environment, path: str) -> tuple[str, str, str]:
    from openprogram.state.execution_facts import current_session_id
    return current_session_id() or "", env.ssh_target, path


def _env_key(env: Environment) -> str:
    return f"ssh:{env.ssh_target}"


def mark_seen(env: Environment, path: str, data: bytes) -> None:
    """Record what was read on the remote host. Inside a turn the record
    goes on the branch's history, as a local read's does
    (``read_tracking.record_baseline``); outside one only this process
    remembers it."""
    digest = hashlib.sha256(data).hexdigest()
    with _seen_lock:
        _seen[_seen_key(env, path)] = digest
    from openprogram.state.execution_facts import current_session_id
    from openprogram.state.store.snapshot.read_tracking import record_baseline
    session_id = current_session_id()
    if session_id:
        record_baseline(session_id, _env_key(env), path, digest, len(data))


def check_fresh(env: Environment, path: str, data: bytes) -> str:
    from openprogram.state.execution_facts import current_session_id
    from openprogram.state.store.snapshot.read_tracking import branch_baseline
    session_id = current_session_id()
    tracked, entry = branch_baseline(session_id, _env_key(env), path) if session_id else (False, None)
    if tracked:
        known = entry.get("hash") if entry is not None else None
    else:
        with _seen_lock:
            known = _seen.get(_seen_key(env, path))
    if known is None:
        return NEVER_READ
    return FRESH if known == hashlib.sha256(data).hexdigest() else STALE


def stale_message(path: str, state: str) -> str:
    if state == NEVER_READ:
        return (f"Error: read {path} before changing it, so edits are made "
                "against its current content.")
    return (f"Error: {path} changed on the remote host since it was last read. "
            "Read it again before changing it.")


__all__ = [
    "FRESH",
    "MAX_READ_BYTES",
    "NEVER_READ",
    "RemoteFS",
    "RemoteStat",
    "STALE",
    "check_fresh",
    "mark_seen",
    "remote_fs",
    "remote_workdir",
    "stale_message",
]

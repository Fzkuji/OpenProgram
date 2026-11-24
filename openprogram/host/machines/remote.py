"""File access on a machine whose shell kind keeps files there (``Kind.remote_files``).

File tools in such a conversation read and write that machine's disk.
Every operation is one shell command through the machine's backend, which
takes bytes on stdin and returns them on stdout (``run_bytes``), so nothing
is lost to text decoding.

Read-before-edit protection works like the local one: a successful
read records the file's SHA-256 for the session, and a later edit or
overwrite is refused when the file was never read or has changed since.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import threading
from dataclasses import dataclass
from typing import Optional

from openprogram.host.machines import Machine, MachineError, current_environment


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
    """Byte-level file operations on one machine, through its shell."""

    def __init__(self, machine: Machine) -> None:
        from openprogram.host.backend import backend_for
        self.machine = machine
        self.backend = backend_for(machine)

    @property
    def label(self) -> str:
        """How results name the machine: its id, as the environment block names it."""
        return self.machine.id

    def _run(self, command: str, data: bytes | None = None,
             timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        from openprogram.host.machines.status import note
        code, out, err = self.backend.run_bytes(command, data=data, timeout=timeout)
        if code == 255:
            message = (f"machine {self.machine.id} cannot be reached: "
                       f"{err.decode('utf-8', 'replace').strip() or 'connection error'}")
            note(self.machine, False, message)
            raise ConnectionError(message)
        if code in (-1, 127):
            message = err.decode("utf-8", "replace").strip()
            note(self.machine, False, message)
            raise ConnectionError(message)
        note(self.machine, True)
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


class RefusedFS(RemoteFS):
    """The conversation's machine can't be used (removed, switched off, without a shell): every operation
    refuses with the reason, so a file tool reports it and never touches this computer's disk."""

    def __init__(self, reason: str) -> None:
        self.machine = None
        self.backend = None
        self.reason = reason

    @property
    def label(self) -> str:
        return "unavailable"

    def _run(self, command: str, data: bytes | None = None,
             timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        raise ConnectionError(self.reason)


def remote_fs() -> Optional[RemoteFS]:
    """The remote filesystem the current tool call must use, or None when
    file tools act on this computer's disk (its own shell, or a shell kind
    that mounts the working directories). A conversation whose machine
    can't be used gets a :class:`RefusedFS`: None would mean this computer."""
    try:
        machine = current_environment()
    except MachineError as exc:
        return RefusedFS(str(exc))
    return RemoteFS(machine) if machine.remote_files else None


def remote_workdir(machine: Machine) -> Optional[str]:
    """Absolute folder on ``machine`` where choosing a project folder there starts.

    ``~`` and an empty setting resolve through the machine's shell once per
    process. Returns None when the machine can't be reached.
    """
    wanted = machine.shell.get("workdir") or "~"
    # The whole connection is in the key: an edited target is another folder, though the id stays.
    key = (machine.id, json.dumps(machine.shell.raw(), sort_keys=True, default=str))
    with _workdirs_lock:
        cached = _workdirs.get(key)
    if cached:
        return cached
    if wanted.startswith("/"):
        resolved = wanted
    else:
        from openprogram.host.backend import backend_for
        result = backend_for(machine).run("pwd", timeout=20)
        line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if result.exit_code != 0 or not line.startswith("/"):
            return None
        resolved = line
    with _workdirs_lock:
        _workdirs[key] = resolved
    return resolved


# ---- folders, for choosing a project's -------------------------------------

#: Most subfolders one listing returns; a folder with more is listed in part.
MAX_FOLDERS = 2000


def _cd(path: str) -> str:
    """A ``cd`` to ``path``: ``~`` and ``~/…`` expand on the machine, anything else is quoted."""
    if not path or path == "~":
        return "cd"
    if path.startswith("~/"):
        return f"cd ~/{_q(path[2:])}"
    return f"cd -- {_q(path)}"


def resolve_folder(machine: Machine, path: str) -> Optional[str]:
    """The absolute, symlink-free path of a folder on ``machine``, or None when it is not a folder there.

    Raises ConnectionError when the machine cannot be reached.
    """
    code, out, _ = RemoteFS(machine).shell(f"{_cd(path.strip())} && pwd -P", timeout=30)
    line = out.strip().splitlines()[-1] if out.strip() else ""
    return line if code == 0 and line.startswith("/") else None


def list_folders(machine: Machine, path: str = "") -> tuple[Optional[str], list[str]]:
    """``(folder, subfolders)`` on ``machine``: the folder resolved and its subfolders' names, sorted.

    An empty ``path`` starts where :func:`remote_workdir` says. The folder is
    None when ``path`` is not a folder there. Raises ConnectionError when the
    machine cannot be reached.
    """
    start = path.strip() or remote_workdir(machine) or "~"
    code, out, _ = RemoteFS(machine).shell(
        f"{_cd(start)} && pwd -P && find . -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null "
        f"| head -n {MAX_FOLDERS}", timeout=30)
    lines = out.splitlines()
    if code != 0 or not lines or not lines[0].startswith("/"):
        return None, []
    names = sorted((line[2:] for line in lines[1:] if line.startswith("./") and line[2:]),
                   key=lambda name: (name.startswith("."), name.lower()))
    return lines[0], names


# ---- read-before-edit tracking -------------------------------------------


def _seen_key(machine: Machine, path: str) -> tuple[str, str, str]:
    from openprogram.state.execution_facts import current_session_id
    return current_session_id() or "", machine.id, path


def _machine_key(machine: Machine) -> str:
    return f"machine:{machine.id}"


def mark_seen(machine: Machine, path: str, data: bytes) -> None:
    """Record what was read on the machine. Inside a turn the record
    goes on the branch's history, as a local read's does
    (``read_tracking.record_baseline``); outside one only this process
    remembers it."""
    digest = hashlib.sha256(data).hexdigest()
    with _seen_lock:
        _seen[_seen_key(machine, path)] = digest
    from openprogram.state.execution_facts import current_session_id
    from openprogram.state.store.snapshot.read_tracking import record_baseline
    session_id = current_session_id()
    if session_id:
        record_baseline(session_id, _machine_key(machine), path, digest, len(data))


def check_fresh(machine: Machine, path: str, data: bytes) -> str:
    from openprogram.state.execution_facts import current_session_id
    from openprogram.state.store.snapshot.read_tracking import branch_baseline
    session_id = current_session_id()
    tracked, entry = branch_baseline(session_id, _machine_key(machine), path) if session_id else (False, None)
    if tracked:
        known = entry.get("hash") if entry is not None else None
    else:
        with _seen_lock:
            known = _seen.get(_seen_key(machine, path))
    if known is None:
        return NEVER_READ
    return FRESH if known == hashlib.sha256(data).hexdigest() else STALE


def stale_message(path: str, state: str) -> str:
    if state == NEVER_READ:
        return (f"Error: read {path} before changing it, so edits are made "
                "against its current content.")
    return (f"Error: {path} changed on its machine since it was last read. "
            "Read it again before changing it.")


__all__ = [
    "FRESH",
    "MAX_READ_BYTES",
    "NEVER_READ",
    "RemoteFS",
    "RemoteStat",
    "STALE",
    "check_fresh",
    "list_folders",
    "mark_seen",
    "remote_fs",
    "remote_workdir",
    "resolve_folder",
    "stale_message",
]

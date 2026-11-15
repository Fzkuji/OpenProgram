#!/usr/bin/env python3
"""Choose which sealed version runs, from outside every version.

Installed as ``<root>/bootstrap/versions.py`` and started by the base's Python,
so it still works when the running version's CLI, updater or agent loop is
broken. Standard library only: it must not import OpenProgram.

The pointer format and write procedure here are the same frozen protocol as
``openprogram/operations/self_update/versions/pointer.py`` and the C launcher.
A contract test has each of them read what the others wrote.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_KEYS = (
    "schema", "generation", "version", "base", "last_good", "last_good_base",
    "previous_good", "previous_good_base", "probation", "probation_deadline", "writer",
)
LOCK_BREAK_SECONDS = 10.0
LOCK_WAIT_SECONDS = 15.0
# What a responding worker gets to checkpoint its running tasks.
STOP_SECONDS = 60.0
KILL_SECONDS = 5.0


class VersionsError(RuntimeError):
    pass


def parse_pointer(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise VersionsError(f"malformed pointer line: {line!r}")
        values[key] = value
    if values.get("schema") != "1" or not values.get("generation", "").isdigit():
        raise VersionsError("unsupported pointer")
    if not _ID.fullmatch(values.get("version", "")) or not _ID.fullmatch(values.get("base", "")):
        raise VersionsError("the pointer names no valid version and base")
    return values


def render_pointer(values: dict[str, str]) -> str:
    ordered = [*_KEYS, *(key for key in values if key not in _KEYS)]
    return "".join(f"{key}={values.get(key, '')}\n" for key in ordered)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        import ctypes

        for attempt in range(20):
            if ctypes.windll.kernel32.MoveFileExW(str(source), str(target), 0x1 | 0x8):
                return
            if attempt == 19 or ctypes.GetLastError() not in (5, 32):
                raise ctypes.WinError()
            time.sleep(0.05)
        return
    os.replace(source, target)
    _fsync_directory(target.parent)


def _write_durably(path: Path, data: bytes) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _Lock:
    def __init__(self, root: Path) -> None:
        self.path = root / "current.lock"

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                self.descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                return self
            except FileExistsError:
                pass
            try:
                if time.time() - self.path.stat().st_mtime > LOCK_BREAK_SECONDS:
                    self.path.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise VersionsError("another writer holds the pointer lock")
            time.sleep(0.05)

    def __exit__(self, *_exc: object) -> None:
        os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


def read_pointer(root: Path) -> dict[str, str]:
    return parse_pointer((root / "current").read_text(encoding="ascii"))


def compare_and_set(root: Path, expected_generation: int, changes: dict[str, str]) -> dict[str, str]:
    with _Lock(root):
        present = read_pointer(root)
        if int(present["generation"]) != expected_generation:
            raise VersionsError(
                f"the pointer moved to generation {present['generation']}; expected {expected_generation}"
            )
        following = {
            **present, **changes,
            "generation": str(expected_generation + 1), "writer": "bootstrap",
        }
        data = render_pointer(following)
        parse_pointer(data)
        _write_durably(root / "current.prev", (root / "current").read_bytes())
        _write_durably(root / "current", data.encode("ascii"))
    return following


def _manifest(root: Path, version: str) -> dict:
    try:
        manifest = json.loads((root / "versions" / version / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VersionsError(f"version {version} has no readable version.json: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("id") != version:
        raise VersionsError(f"version {version} has a mismatched version.json")
    return manifest


def _manifests(root: Path) -> list[dict]:
    found = []
    directory = root / "versions"
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.name.startswith(".") or not _ID.fullmatch(entry.name):
                continue
            try:
                found.append(_manifest(root, entry.name))
            except VersionsError:
                continue
    return sorted(found, key=lambda item: str(item["id"]), reverse=True)


def _git(root: Path, *, windows: bool = os.name == "nt") -> str | None:
    """A base's own Git, where ``install_seed.py`` and the version itself look.

    The bundled Git for Windows keeps its command in ``cmd``; it has no ``bin``.
    """
    relative = "git/cmd/git.exe" if windows else "git/bin/git"
    for base in sorted((root / "bases").glob("*")):
        if (base / relative).is_file():
            return str(base / relative)
    return shutil.which("git")


def rebuild_pointer(root: Path) -> int:
    """Write a pointer when ``current`` and ``current.prev`` are both unusable."""
    if (root / ".seeding").exists():
        raise VersionsError("first-run setup is still running")
    git = _git(root)
    reachable: list[dict] = []
    for manifest in _manifests(root):
        if git is None:
            reachable.append(manifest)
            continue
        result = subprocess.run(
            [git, "-C", str(root / "repo"), "merge-base", "--is-ancestor",
             str(manifest.get("commit")), "refs/heads/local"],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            reachable.append(manifest)
    if not reachable:
        raise VersionsError("no sealed version has a commit on the local line")
    newest = reachable[0]
    generation = 0
    try:
        generation = int(parse_pointer((root / "current.prev").read_text(encoding="ascii"))["generation"]) + 1
    except (OSError, UnicodeDecodeError, VersionsError):
        pass
    values = {
        "schema": "1", "generation": str(generation), "version": str(newest["id"]),
        "base": str(newest["base"]), "writer": "bootstrap",
    }
    with _Lock(root):
        _write_durably(root / "current", render_pointer(values).encode("ascii"))
    # The launcher runs this too, and its stdout may be a protocol stream.
    print(f"rebuilt the pointer: {newest['id']}", file=sys.stderr)
    return 0


def _short_date(version: str) -> str:
    try:
        moment = datetime.strptime(version.split("-", 1)[0], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return "unknown date"
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def list_versions(root: Path) -> int:
    pointer = read_pointer(root)
    labels = {
        pointer.get("version"): "current",
        pointer.get("last_good"): "last good",
        pointer.get("previous_good"): "previous good",
    }
    manifests = _manifests(root)
    if not manifests:
        print("No sealed versions.")
        return 0
    for manifest in manifests:
        name = str(manifest["id"])
        marks = [label for key, label in labels.items() if key == name]
        if pointer.get("version") == name and pointer.get("last_good") == name:
            marks = ["current", "last good"]
        if manifest.get("destructive_migration"):
            marks.append(f"destructive migration: {manifest['destructive_migration']}")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        commit = str(manifest.get("commit", ""))[:10]
        summary = str(manifest.get("summary", "")).splitlines()[0:1]
        print(f"{name}  {commit}  {_short_date(name)}  {summary[0] if summary else ''}{suffix}".rstrip())
    _list_unapplied(root)
    return 0


def _list_unapplied(root: Path) -> None:
    directory = root / "unapplied"
    if not directory.is_dir():
        return
    lines = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name, reverse=True):
        try:
            saved = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
            count = sum(len(saved[key]) for key in ("changed", "added", "removed"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if count:
            lines.append(f"  {entry.name}  {count} file{'' if count == 1 else 's'}  {entry / 'changes.patch'}")
    if lines:
        print("\nDirect edits saved, not applied:")
        print("\n".join(lines))


def _alive(pid: int) -> bool:
    """Whether a process exists. On Windows signal 0 is Ctrl-C, so it is asked differently."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True  # It exists and belongs to someone else.
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # Access denied is not evidence of exit.
    try:
        code = wintypes.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _worker_pid(state: Path) -> int | None:
    try:
        token = (state / "worker.pid").read_text(encoding="utf-8").split()[0]
        pid = int(token)
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid > 0 and _alive(pid) else None


def _ended(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _stop_worker(state: Path) -> None:
    """Stop the worker. A SIGTERM makes it checkpoint running tasks first.

    A worker that does not finish in time is the broken one this tool exists
    to get away from, so it is killed instead of blocking the switch.
    """
    pid = _worker_pid(state)
    if pid is None:
        return
    os.kill(pid, signal.SIGTERM)
    if _ended(pid, STOP_SECONDS):
        return
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if not _ended(pid, KILL_SECONDS):
        raise VersionsError(f"the worker (pid {pid}) did not stop; nothing was changed")


def _launcher(root: Path) -> Path:
    identity = root / "identity"
    if sys.platform == "darwin":
        return identity / "OpenProgram.app/Contents/MacOS/OpenProgram"
    return identity / ("openprogram-launcher.exe" if os.name == "nt" else "openprogram-launcher")


def _start_worker(root: Path, state: Path) -> bool:
    launcher = _launcher(root)
    if not launcher.is_file():
        return False
    log = open(state / "worker.log", "ab")
    try:
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
        subprocess.Popen(
            [str(launcher), "-u", "-m", "openprogram", "worker", "run"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **options,
        )
    finally:
        log.close()
    return True


def _record(root: Path, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(root / "recovery.log", "a", encoding="utf-8") as stream:
        stream.write(f"{stamp} versions: {message}\n")


def use_version(root: Path, version: str) -> int:
    if not _ID.fullmatch(version):
        raise VersionsError(f"invalid version id: {version!r}")
    manifest = _manifest(root, version)
    if not (root / "bases" / str(manifest["base"])).is_dir():
        raise VersionsError(f"version {version} needs base {manifest['base']}, which is gone")
    state = root.parent
    pointer = read_pointer(root)
    if pointer["version"] == version:
        print(f"{version} is already the current version.")
        return 0
    _stop_worker(state)
    following = compare_and_set(
        root, int(pointer["generation"]),
        {"version": version, "base": str(manifest["base"]), "probation": "", "probation_deadline": ""},
    )
    # The owner chose it: its earlier failures must not send it straight back.
    for suffix in ("attempts", "desktop-failed"):
        (root / "boot" / f"{version}.{suffix}").unlink(missing_ok=True)
    _record(root, f"moved from {pointer['version']} to {version} (generation {following['generation']})")
    # A service manager may have restarted the old version while it was stopped.
    _stop_worker(state)
    started = _start_worker(root, state)
    print(f"Now on {version}." if started else f"Now on {version}. Start OpenProgram to use it.")
    return 0


def _restore_database(source: Path, target: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def restore_data(root: Path, backup: str) -> int:
    state = root.parent
    if not backup.startswith("pre-switch-") or not _ID.fullmatch(backup):
        raise VersionsError(f"not a pre-switch backup name: {backup!r}")
    source = state / "backups" / backup
    if not source.is_dir():
        raise VersionsError(f"no such backup: {source}")
    _stop_worker(state)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety = state / "backups" / f"before-restore-{stamp}"
    restored = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source)
        target = state / relative
        if target.exists():
            kept = safety / relative
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, kept)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".db":
            _restore_database(path, target)
        else:
            shutil.copy2(path, target)
        restored.append(relative.as_posix())
    _record(root, f"restored data from {backup}: {len(restored)} file(s)")
    print(f"Restored {len(restored)} file(s) from {backup}. The data it replaced is in {safety}.")
    started = _start_worker(root, state)
    if not started:
        print("Start OpenProgram to use it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openprogram versions")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List retained versions.")
    use = commands.add_parser("use", help="Stop the worker and switch to a retained version.")
    use.add_argument("version")
    restore = commands.add_parser("restore-data", help="Restore a pre-switch data snapshot.")
    restore.add_argument("backup")
    commands.add_parser("rebuild-pointer", help="Rebuild a lost pointer.")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root)
    try:
        if arguments.command == "list":
            return list_versions(root)
        if arguments.command == "use":
            return use_version(root, arguments.version)
        if arguments.command == "restore-data":
            return restore_data(root, arguments.backup)
        return rebuild_pointer(root)
    except (VersionsError, OSError) as exc:
        print(f"openprogram versions: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

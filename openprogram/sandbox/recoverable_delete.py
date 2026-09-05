"""Best-effort recoverable deletion for local agent child processes.

This is an interception convenience, not a security boundary. A child can
bypass it by using an absolute system binary, disabling Python startup hooks,
or making direct syscalls.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


TRASH_ENV = "OPENPROGRAM_RECOVERABLE_TRASH"
_rename = os.rename
_unlink = os.unlink
_rmdir = os.rmdir
_rmtree = shutil.rmtree
_SAFE_RMTREE_SUPPORTED = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.scandir in os.supports_fd
    and os.open in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rmdir in os.supports_dir_fd
)
_manifest_lock = threading.Lock()


class _SourceCleanupFailed(Exception):
    def __init__(self, path: str, error: BaseException) -> None:
        self.path = path
        self.error = error


def _exists(path: str) -> bool:
    return os.path.lexists(path)


def _safe_segment(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "").strip("._")
    return (cleaned or fallback)[:80]


def current_trash_root() -> Path | None:
    """Return the current agent run's trash directory, if a turn is bound."""
    from openprogram.agent.run_control import get_current_session_id, current_token
    from openprogram.paths import get_state_dir

    session_id = get_current_session_id()
    if not session_id:
        return None
    token = current_token(session_id)
    if token is None:
        return None
    return (
        get_state_dir()
        / "trash"
        / _safe_segment(session_id, "session")
        / _safe_segment(token.execution_id, "run")
    )


def _trash_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    value = explicit or os.environ.get(TRASH_ENV)
    if not value:
        raise RuntimeError(f"{TRASH_ENV} is not set")
    return Path(value).absolute()


def _safe_rmtree_supported() -> bool:
    return _SAFE_RMTREE_SUPPORTED


def _empty_directory_fd(fd: int) -> None:
    with os.scandir(fd) as entries:
        for entry in entries:
            try:
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno not in {errno.ENOTDIR, errno.ELOOP}:
                    raise
                _unlink(entry.name, dir_fd=fd)
                continue
            try:
                _empty_directory_fd(child_fd)
            finally:
                os.close(child_fd)
            _rmdir(entry.name, dir_fd=fd)


def _physical_rmtree(path: str) -> None:
    if not _safe_rmtree_supported():
        # Windows has no dir_fd/O_NOFOLLOW equivalent in Python. Recoverable
        # deletion is an interception convenience rather than a sandbox
        # boundary, so keep directory recovery functional there and retain
        # the fd-relative, symlink-race-resistant implementation on POSIX.
        if os.path.islink(path):
            _unlink(path)
        else:
            _rmtree(path)
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _empty_directory_fd(fd)
    finally:
        os.close(fd)
    _rmdir(path)


def _copy_then_delete(
    source: str,
    destination: str,
    kind: str,
    before_cleanup: Callable[[str], None] | None = None,
) -> None:
    partial = destination + ".partial-" + uuid.uuid4().hex
    try:
        if kind == "symlink":
            os.symlink(os.readlink(source), partial)
        elif kind == "directory":
            shutil.copytree(source, partial, symlinks=True)
        else:
            shutil.copy2(source, partial, follow_symlinks=False)
        _rename(partial, destination)
    except Exception:
        if _exists(partial):
            _physical_rmtree(partial) if os.path.isdir(partial) and not os.path.islink(partial) else _unlink(partial)
        raise

    quarantine = os.path.join(
        os.path.dirname(source),
        f".{os.path.basename(source)}.openprogram-delete-{uuid.uuid4().hex}",
    )
    try:
        _rename(source, quarantine)
        if before_cleanup is not None:
            before_cleanup(quarantine)
    except Exception:
        if _exists(quarantine) and not _exists(source):
            _rename(quarantine, source)
        if kind == "directory":
            _physical_rmtree(destination)
        else:
            _unlink(destination)
        raise
    try:
        if kind == "directory":
            _physical_rmtree(quarantine)
        else:
            _unlink(quarantine)
    except Exception as exc:
        raise _SourceCleanupFailed(quarantine, exc) from exc


def _move(
    source: str,
    destination: str,
    kind: str,
    before_cleanup: Callable[[str], None] | None = None,
) -> None:
    try:
        _rename(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_then_delete(source, destination, kind, before_cleanup)


def _kind(source: str) -> str:
    if os.path.islink(source):
        return "symlink"
    if os.path.isdir(source):
        return "directory"
    return "file"


def _validate_kind(source: str, kind: str, expect: str) -> None:
    if expect == "file" and kind == "directory":
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), source)
    if expect in {"directory", "empty_directory", "tree"} and kind != "directory":
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), source)
    if expect == "empty_directory":
        try:
            next(os.scandir(source))
        except StopIteration:
            return
        raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), source)


def _lock_manifest_file(fd: int) -> Callable[[], None] | None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

        def unlock() -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

        return unlock
    try:
        import fcntl
    except ImportError:
        return None
    fcntl.flock(fd, fcntl.LOCK_EX)
    return lambda: fcntl.flock(fd, fcntl.LOCK_UN)


def _append_manifest(root: Path, entry: dict[str, Any]) -> None:
    line = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    with _manifest_lock:
        fd = os.open(root / "manifest.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        unlock = None
        start = None
        try:
            unlock = _lock_manifest_file(fd)
            start = os.fstat(fd).st_size
            _write_all(fd, line)
            os.fsync(fd)
        except Exception:
            if start is not None:
                os.ftruncate(fd, start)
                os.fsync(fd)
            raise
        finally:
            try:
                if unlock is not None:
                    unlock()
            finally:
                os.close(fd)


def _unlink_quietly(path: Path) -> None:
    """Drop a sidecar marker whose record now lives in the manifest."""
    try:
        _unlink(path)
    except OSError:
        pass


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "write made no progress")
        offset += written


def move_to_trash(
    path: str | os.PathLike[str],
    *,
    expect: str = "any",
    missing_ok: bool = False,
    trash_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Move one path into the run trash and append its recovery record."""
    source = os.path.abspath(os.fsdecode(path))
    if not _exists(source):
        if missing_ok:
            return None
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)

    root = _trash_root(trash_root)
    source_location = os.path.join(
        os.path.realpath(os.path.dirname(source)), os.path.basename(source),
    )
    root_location = os.path.realpath(root)
    for source_candidate, root_candidate in (
        (source, str(root)),
        (source_location, root_location),
    ):
        try:
            common = os.path.commonpath((source_candidate, root_candidate))
        except ValueError:  # different Windows volumes
            continue
        if common in {source_candidate, root_candidate}:
            raise OSError(errno.EINVAL, "cannot delete a path containing its own trash", source)

    kind = _kind(source)
    _validate_kind(source, kind, expect)
    items = root / "items"
    items.mkdir(parents=True, exist_ok=True)
    entry_id = uuid.uuid4().hex
    basename = _safe_segment(os.path.basename(source), "item")
    destination = items / f"{entry_id}-{basename}"
    entry = {
        "id": entry_id,
        "original_path": source,
        "trash_path": str(destination),
        "kind": kind,
        "deleted_at": time.time(),
    }
    pending = root / "pending" / f"{entry_id}.json"

    def record_cross_filesystem_copy(cleanup_path: str) -> None:
        # The copy is complete and the source is parked at *cleanup_path*;
        # a crash from here until the manifest append would otherwise lose
        # both the recovery record and the parked source. This marker is a
        # sidecar file rather than a manifest line because the cross-device
        # path is the *normal* path under bubblewrap — each --bind is its
        # own mount, so renaming the workspace into the trash root always
        # raises EXDEV — and a manifest that carries two lines per deletion
        # there but one per deletion on macOS is a record format that
        # depends on the platform.
        pending.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            pending,
            json.dumps({**entry, "source_cleanup_path": cleanup_path,
                        "source_cleanup_status": "pending"}),
            0o600,
        )

    try:
        _move(source, str(destination), kind, record_cross_filesystem_copy)
    except _SourceCleanupFailed as failure:
        failed_entry = {
            **entry,
            "source_cleanup_path": failure.path,
            "source_cleanup_status": "error",
            "source_cleanup_error": f"{type(failure.error).__name__}: {failure.error}",
        }
        _append_manifest(root, failed_entry)
        _unlink_quietly(pending)
        raise failure.error
    try:
        _append_manifest(root, entry)
        _unlink_quietly(pending)
    except Exception:
        try:
            _move(str(destination), source, kind)
        finally:
            raise
    return entry


def _latest_manifest_entries(manifest: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    # Sidecars first, so a deletion that later reached the manifest is
    # reported from the manifest and an interrupted one is still listed.
    for sidecar in sorted(manifest.parent.glob("pending/*.json")):
        try:
            candidate = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
            latest[candidate["id"]] = candidate
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            candidate = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
            continue
        latest[candidate["id"]] = candidate
    return list(latest.values())


def _run_entries(trash_base: Path):
    # A run that crashed mid-deletion has a pending sidecar and no manifest
    # yet, so the run directories are collected rather than the manifests.
    runs = {p.parent for p in trash_base.glob("*/*/manifest.jsonl")}
    runs |= {p.parent.parent for p in trash_base.glob("*/*/pending/*.json")}
    for run in sorted(runs):
        try:
            entries = _latest_manifest_entries(run / "manifest.jsonl")
        except OSError:
            continue
        for entry in entries:
            yield run, entry


def list_deleted(
    *,
    trash_base: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """List the latest record for each deletion captured under local runs."""
    if trash_base is None:
        from openprogram.paths import get_state_dir

        base = get_state_dir() / "trash"
    else:
        base = Path(trash_base)
    records = []
    for root, entry in _run_entries(base):
        if entry.get("restore_status") == "complete":
            status = "restored"
        elif _exists(str(entry.get("trash_path", ""))):
            status = "available"
        else:
            status = "missing"
        records.append({
            **entry,
            "status": status,
            "session": root.parent.name,
            "turn": root.name,
        })

    def updated_at(entry: dict[str, Any]) -> float:
        try:
            return float(entry.get("restored_at") or entry.get("deleted_at") or 0)
        except (TypeError, ValueError):
            return 0

    return sorted(records, key=updated_at, reverse=True)


def restore_deleted(
    entry_id: str,
    *,
    trash_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Restore one manifest entry without overwriting an existing path."""
    root = _trash_root(trash_root)
    manifest = root / "manifest.jsonl"
    entry = next(
        (candidate for candidate in _latest_manifest_entries(manifest)
         if candidate["id"] == entry_id),
        None,
    )
    if entry is None:
        raise KeyError(entry_id)
    source = str(entry["trash_path"])
    destination = str(entry["original_path"])
    if _exists(destination):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), destination)
    if not _exists(source):
        raise FileNotFoundError(errno.ENOENT, "trash item is no longer available", source)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    _move(source, destination, str(entry["kind"]))
    _append_manifest(root, {
        **entry,
        "restore_status": "complete",
        "restored_at": time.time(),
    })
    return Path(destination)


def restore_deleted_anywhere(
    entry_id: str,
    *,
    trash_base: str | os.PathLike[str] | None = None,
) -> Path:
    """Find one deletion across local run manifests and restore it."""
    if trash_base is None:
        from openprogram.paths import get_state_dir

        base = get_state_dir() / "trash"
    else:
        base = Path(trash_base)
    matches = [root for root, entry in _run_entries(base) if entry["id"] == entry_id]
    if not matches:
        raise KeyError(entry_id)
    if len(matches) > 1:
        raise RuntimeError(f"duplicate trash entry id: {entry_id}")
    return restore_deleted(entry_id, trash_root=matches[0])


def _atomic_write(path: Path, content: str, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    fd = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        _write_all(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if fd is not None:
            os.close(fd)
        if _exists(str(temporary)):
            _unlink(temporary)


def _write_shell_shims(root: Path) -> Path:
    bin_dir = root / "shims" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("rm", "rmdir", "unlink"):
        path = bin_dir / name
        content = (
            "#!/bin/sh\n"
            f'exec env OPENPROGRAM_DELETE_HELPER=1 "$OPENPROGRAM_DELETE_PYTHON" '
            f'-m openprogram.sandbox.recoverable_delete shell {name} "$@"\n'
        )
        _atomic_write(path, content, 0o755)
    return bin_dir


def prepare_child_env(base: dict[str, str] | None = None) -> dict[str, str] | None:
    """Inject shims only while an agent session is bound to this process."""
    root = current_trash_root()
    if root is None:
        return base
    env = dict(os.environ if base is None else base)
    root.mkdir(parents=True, exist_ok=True)
    bin_dir = _write_shell_shims(root)
    shim_dir = Path(__file__).with_name("shims")
    package_root = Path(__file__).resolve().parents[2]
    env[TRASH_ENV] = str(root)
    env["OPENPROGRAM_DELETE_PYTHON"] = sys.executable
    env["PATH"] = os.pathsep.join((str(bin_dir), env.get("PATH", "")))
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(shim_dir), str(package_root), env.get("PYTHONPATH", "")) if part
    )
    preload = shim_dir / "node_preload.cjs"
    if os.name == "nt":
        # NODE_OPTIONS parses backslashes as escapes. Forward slashes remain
        # valid Win32 paths and survive Node's option parser unchanged.
        preload_value = preload.as_posix()
    else:
        preload_value = str(preload)
    node_option = f'--require="{preload_value}"'
    env["NODE_OPTIONS"] = " ".join(part for part in (node_option, env.get("NODE_OPTIONS", "")) if part)
    return env


def sandbox_writable_root() -> str | None:
    root = current_trash_root()
    return str(root) if root is not None else None


def _dir_fd_path(path: str | bytes | os.PathLike[str], dir_fd: int | None) -> str | bytes | os.PathLike[str]:
    if dir_fd is not None:
        raise NotImplementedError("recoverable deletion does not support dir_fd")
    return path


def install_python_shims() -> None:
    """Patch common Python deletion APIs inside an agent child interpreter."""
    import pathlib

    if getattr(os.unlink, "_openprogram_recoverable", False):
        return

    def unlink(path, *, dir_fd=None):
        return move_to_trash(_dir_fd_path(path, dir_fd), expect="file") and None

    unlink._openprogram_recoverable = True  # type: ignore[attr-defined]

    def rmdir(path, *, dir_fd=None):
        return move_to_trash(_dir_fd_path(path, dir_fd), expect="empty_directory") and None

    def path_unlink(self, missing_ok=False):
        move_to_trash(self, expect="file", missing_ok=missing_ok)

    def path_rmdir(self):
        move_to_trash(self, expect="empty_directory")

    def rmtree(path, ignore_errors=False, onerror=None, *, onexc=None, dir_fd=None):
        try:
            move_to_trash(_dir_fd_path(path, dir_fd), expect="tree")
        except Exception as exc:
            if ignore_errors:
                return
            handler = onexc or onerror
            if handler is None:
                raise
            handler(rmtree, path, exc if onexc else sys.exc_info())

    rmtree.avoids_symlink_attacks = getattr(shutil.rmtree, "avoids_symlink_attacks", False)
    os.unlink = unlink
    os.remove = unlink
    os.rmdir = rmdir
    shutil.rmtree = rmtree
    pathlib.Path.unlink = path_unlink
    pathlib.Path.rmdir = path_rmdir


def _shell_rm(args: list[str]) -> int:
    force = recursive = allow_dir = False
    targets: list[str] = []
    options = True
    for arg in args:
        if options and arg == "--":
            options = False
        elif options and arg.startswith("--"):
            if arg == "--force": force = True
            elif arg == "--recursive": recursive = True
            elif arg == "--dir": allow_dir = True
            elif arg in {"--verbose", "--preserve-root", "--no-preserve-root"}: pass
            else:
                print(f"rm: unsupported option {arg}", file=sys.stderr)
                return 1
        elif options and arg.startswith("-") and arg != "-":
            for flag in arg[1:]:
                if flag == "f": force = True
                elif flag in "rR": recursive = True
                elif flag == "d": allow_dir = True
                elif flag == "v": pass
                else:
                    print(f"rm: unsupported option -{flag}", file=sys.stderr)
                    return 1
        else:
            targets.append(arg)
    if not targets:
        if force:
            return 0
        print("rm: missing operand", file=sys.stderr)
        return 1
    failed = False
    for target in targets:
        try:
            is_dir = os.path.isdir(target) and not os.path.islink(target)
            expect = "tree" if is_dir and recursive else "empty_directory" if is_dir and allow_dir else "file"
            move_to_trash(target, expect=expect, missing_ok=force)
        except OSError as exc:
            failed = True
            print(f"rm: {target}: {exc.strerror or exc}", file=sys.stderr)
    return int(failed)


def _shell_rmdir(args: list[str]) -> int:
    parents = False
    targets: list[str] = []
    for arg in args:
        if arg in {"-p", "--parents"}:
            parents = True
        elif arg.startswith("-"):
            print(f"rmdir: unsupported option {arg}", file=sys.stderr)
            return 1
        else:
            targets.append(arg)
    if not targets:
        print("rmdir: missing operand", file=sys.stderr)
        return 1
    failed = False
    for target in targets:
        current = os.path.abspath(target)
        while True:
            try:
                move_to_trash(current, expect="empty_directory")
            except OSError as exc:
                failed = True
                print(f"rmdir: {current}: {exc.strerror or exc}", file=sys.stderr)
                break
            if not parents:
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return int(failed)


def _shell_unlink(args: list[str]) -> int:
    if len(args) != 1:
        print("unlink: expected exactly one operand", file=sys.stderr)
        return 1
    try:
        move_to_trash(args[0], expect="file")
        return 0
    except OSError as exc:
        print(f"unlink: {args[0]}: {exc.strerror or exc}", file=sys.stderr)
        return 1


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "shell":
        command, args = argv[1], argv[2:]
        if command == "rm": return _shell_rm(args)
        if command == "rmdir": return _shell_rmdir(args)
        if command == "unlink": return _shell_unlink(args)
    if len(argv) >= 3 and argv[0] == "delete":
        expect = argv[2]
        missing_ok = len(argv) > 3 and argv[3] == "missing-ok"
        try:
            move_to_trash(argv[1], expect=expect, missing_ok=missing_ok)
            return 0
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 1
    print("usage: recoverable_delete shell <rm|rmdir|unlink> ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

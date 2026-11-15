"""The ``current`` pointer: one ASCII file, one atomic rename per move.

The format and the write procedure are a frozen protocol. The C launcher, the
shell and ``bootstrap/versions.py`` each read and write this same file, and
none of them changes through a self-update.

Write procedure: take ``current.lock`` with exclusive create (a lock older than
10 s is broken), reread ``current`` and compare ``generation``, write
``current.<pid>.tmp`` with the generation plus one and fsync it, copy the old
pointer to ``current.prev`` the same way, rename over ``current``, fsync the
directory, release the lock.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import os
from pathlib import Path
import time
from typing import Callable

from .layout import Layout, valid_id

SCHEMA = 1
LOCK_BREAK_SECONDS = 10.0
LOCK_WAIT_SECONDS = 15.0
_WINDOWS_RETRIES = 20
_WINDOWS_RETRY_SECONDS = 0.05
# Windows refuses to open, rename or delete a file another handle holds: the
# launcher or the shell reading it, an indexer, an antivirus scan.
_WINDOWS = os.name == "nt"
_log = logging.getLogger(__name__)
_KNOWN = (
    "schema", "generation", "version", "base", "last_good", "last_good_base",
    "previous_good", "previous_good_base", "probation", "probation_deadline", "writer",
)


class PointerError(RuntimeError):
    """The pointer is missing or unreadable."""


class PointerConflict(RuntimeError):
    """Another writer moved the pointer first, or holds the lock."""


@dataclass(frozen=True)
class Pointer:
    generation: int
    version: str
    base: str
    last_good: str = ""
    last_good_base: str = ""
    previous_good: str = ""
    previous_good_base: str = ""
    probation: str = ""
    probation_deadline: int | None = None
    writer: str = ""
    extras: tuple[tuple[str, str], ...] = ()

    def fallback(self) -> tuple[str, str] | None:
        """Where a failing ``version`` goes back to, if anywhere."""
        if self.version == self.last_good and self.last_good:
            pair = (self.previous_good, self.previous_good_base)
        else:
            pair = (self.last_good, self.last_good_base)
        return pair if pair[0] and pair[1] else None


def _text(value: object, label: str, *, identifier: bool) -> str:
    text = "" if value is None else str(value)
    if text and identifier and not valid_id(text):
        raise PointerError(f"invalid {label}: {text!r}")
    return text


def parse(text: str) -> Pointer:
    if not text.isascii():
        raise PointerError("the pointer is not ASCII")
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise PointerError(f"malformed pointer line: {line!r}")
        values[key] = value
    if values.get("schema") != str(SCHEMA):
        raise PointerError(f"unsupported pointer schema: {values.get('schema')!r}")
    generation = values.get("generation", "")
    if not generation.isdigit():
        raise PointerError(f"invalid generation: {generation!r}")
    version, base = values.get("version", ""), values.get("base", "")
    if not valid_id(version) or not valid_id(base):
        raise PointerError("the pointer names no valid version and base")
    deadline = values.get("probation_deadline", "")
    if deadline and not deadline.isdigit():
        raise PointerError(f"invalid probation_deadline: {deadline!r}")
    return Pointer(
        generation=int(generation),
        version=version,
        base=base,
        last_good=_text(values.get("last_good"), "last_good", identifier=True),
        last_good_base=_text(values.get("last_good_base"), "last_good_base", identifier=True),
        previous_good=_text(values.get("previous_good"), "previous_good", identifier=True),
        previous_good_base=_text(values.get("previous_good_base"), "previous_good_base", identifier=True),
        probation=_text(values.get("probation"), "probation", identifier=True),
        probation_deadline=int(deadline) if deadline else None,
        writer=values.get("writer", ""),
        extras=tuple((key, value) for key, value in values.items() if key not in _KNOWN),
    )


def render(pointer: Pointer) -> str:
    lines = [
        ("schema", str(SCHEMA)),
        ("generation", str(pointer.generation)),
        ("version", pointer.version),
        ("base", pointer.base),
        ("last_good", pointer.last_good),
        ("last_good_base", pointer.last_good_base),
        ("previous_good", pointer.previous_good),
        ("previous_good_base", pointer.previous_good_base),
        ("probation", pointer.probation),
        ("probation_deadline", "" if pointer.probation_deadline is None else str(pointer.probation_deadline)),
        ("writer", pointer.writer),
        *pointer.extras,
    ]
    return "".join(f"{key}={value}\n" for key, value in lines)


def _retrying(operation: Callable[[], object]) -> object:
    """Ride out a Windows sharing violation; POSIX never hits the retry."""
    for attempt in range(_WINDOWS_RETRIES):
        try:
            return operation()
        except PermissionError:
            if not _WINDOWS or attempt == _WINDOWS_RETRIES - 1:
                raise
            time.sleep(_WINDOWS_RETRY_SECONDS)
    raise AssertionError("unreachable")


def read(layout: Layout) -> Pointer:
    try:
        text = _retrying(lambda: layout.pointer.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError) as exc:
        raise PointerError(f"cannot read the pointer: {exc}") from exc
    return parse(str(text))


def read_or_recover(layout: Layout) -> Pointer:
    """The pointer, restored from ``current.prev`` when ``current`` is damaged."""
    try:
        return read(layout)
    except PointerError:
        previous = parse(layout.previous_pointer.read_text(encoding="ascii"))
    _write_new(layout, previous)
    return read(layout)


def _fsync_directory(path: Path) -> None:
    if _WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace(source: Path, target: Path) -> None:
    """Rename ``source`` over ``target`` durably."""
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        # The error is read from ctypes' own copy: the thread's last error can
        # be overwritten before Python reads it, and a lost code is not retried.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        replace_existing_write_through = 0x1 | 0x8
        def attempt() -> None:
            if not move(str(source), str(target), replace_existing_write_through):
                raise ctypes.WinError(ctypes.get_last_error())
        _retrying(attempt)
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


def _acquire(layout: Layout, *, wait: float = LOCK_WAIT_SECONDS) -> int:
    deadline = time.monotonic() + wait
    while True:
        try:
            return os.open(layout.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        except PermissionError:
            if not _WINDOWS:
                raise
        try:
            if time.time() - layout.lock.stat().st_mtime > LOCK_BREAK_SECONDS:
                layout.lock.unlink(missing_ok=True)
                continue
        except FileNotFoundError:
            continue  # Released since the open: take it now.
        except OSError:
            # Windows: the lock is being deleted or scanned and cannot be read
            # or removed yet. Wait like for any other holder, up to the deadline.
            pass
        if time.monotonic() >= deadline:
            raise PointerConflict("another writer holds the pointer lock")
        time.sleep(0.05)


def _release(layout: Layout, descriptor: int) -> None:
    """Give the lock back. Never raises: the write it guarded has already landed or failed.

    A lock that cannot be removed (Windows, while a scanner holds it) is
    broken by the next writer once it is ``LOCK_BREAK_SECONDS`` old.
    """
    os.close(descriptor)
    try:
        _retrying(lambda: layout.lock.unlink(missing_ok=True))
    except OSError:
        _log.warning("the pointer lock could not be removed; it expires by age", exc_info=True)


def _write_new(layout: Layout, pointer: Pointer) -> None:
    descriptor = _acquire(layout)
    try:
        _write_durably(layout.pointer, render(pointer).encode("ascii"))
    finally:
        _release(layout, descriptor)


def create(layout: Layout, pointer: Pointer) -> Pointer:
    """Write the first pointer of a root. Refuses to replace an existing one."""
    descriptor = _acquire(layout)
    try:
        if layout.pointer.exists():
            raise PointerConflict("the root already has a pointer")
        _write_durably(layout.pointer, render(pointer).encode("ascii"))
    finally:
        _release(layout, descriptor)
    return pointer


def compare_and_set(layout: Layout, expected_generation: int, *, writer: str, ending_probation: bool = False,
                    **changes: object) -> Pointer:
    """Apply ``changes`` if the pointer is still at ``expected_generation``."""
    descriptor = _acquire(layout)
    try:
        present = read(layout)
        if present.generation != expected_generation:
            raise PointerConflict(
                f"the pointer moved to generation {present.generation}; expected {expected_generation}"
            )
        following = replace(present, generation=present.generation + 1, writer=writer, **changes)  # type: ignore[arg-type]
        # Parsing what we are about to write catches an invalid id before it lands.
        data = render(following)
        parse(data)
        # Ending probation is not a move: restoring ``current.prev`` afterwards
        # must not bring back an expired probation and read as a failed switch.
        kept = render(replace(following, generation=present.generation)).encode("ascii") if ending_probation \
            else layout.pointer.read_bytes()
        _write_durably(layout.previous_pointer, kept)
        _write_durably(layout.pointer, data.encode("ascii"))
    finally:
        _release(layout, descriptor)
    return following


def activate(
    layout: Layout, expected_generation: int, *, version: str, base: str,
    update_id: str, deadline: int, writer: str = "controller",
) -> Pointer:
    """Move to a candidate on probation."""
    return compare_and_set(
        layout, expected_generation, writer=writer, version=version, base=base,
        probation=update_id, probation_deadline=deadline,
    )


def commit(layout: Layout, expected_generation: int, *, writer: str = "controller") -> Pointer:
    """End probation: the running version becomes ``last_good``, the old one shifts down."""
    present = read(layout)
    return compare_and_set(
        layout, expected_generation, writer=writer,
        previous_good=present.last_good, previous_good_base=present.last_good_base,
        last_good=present.version, last_good_base=present.base,
        probation="", probation_deadline=None, ending_probation=True,
    )


def write_pending_rollback(layout: Layout, candidate: str) -> None:
    """Journal a rollback for the launcher to apply at the next start."""
    _write_durably(layout.pending_rollback, f"version={candidate}\n".encode("ascii"))


def rollback(
    layout: Layout, candidate: str, *, deadline: float, writer: str = "controller",
    clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Return the pointer from ``candidate``; ``moved``, ``already`` or ``pending``.

    Unlike a forward write, a conflict is not a failure: rereading shows
    whether someone already moved it. If the write still fails at ``deadline``
    the rollback is journaled for the launcher.
    """
    while True:
        try:
            present = read(layout)
            if present.version != candidate:
                return "already"
            target = present.fallback()
            if target is None:
                raise PointerConflict("no version to return to")
            compare_and_set(
                layout, present.generation, writer=writer, version=target[0], base=target[1],
                probation="", probation_deadline=None,
            )
            return "moved"
        except (PointerConflict, PointerError, OSError):
            if clock() >= deadline:
                write_pending_rollback(layout, candidate)
                return "pending"
            sleep(0.2)

"""A config file as text, read and written whole, and dotted key paths.

A symlinked file is written through to its target, and a file with other
hard links in place; otherwise the write is a temp file renamed over the
original, keeping its mode. A file that cannot be read or
does not parse is :class:`ConfigUnreadable`: its contents are unknown, so
nothing is written to it.
"""
from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


class ConfigUnreadable(Exception):
    """The config file cannot be read or edited safely; it is left alone."""


@dataclass
class ConfigText:
    path: Path          # the file written (a symlink's target)
    text: str
    exists: bool
    newline: str
    bom: bool = False   # written with a UTF-8 byte order mark, kept (AstrBot's utf-8-sig)

    @classmethod
    def read(cls, path: Path) -> "ConfigText":
        if ROW_MARK in Path(path).name:
            return SqliteRowText.read_row(Path(path))
        target = Path(os.path.realpath(path))
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return cls(target, "", False, "\n")
        except OSError as exc:
            raise ConfigUnreadable(f"{path} cannot be read: {exc}") from exc
        bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig" if bom else "utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigUnreadable(f"{path} is not UTF-8 text") from exc
        return cls(target, text, True, "\r\n" if "\r\n" in text else "\n", bom)

    def write(self, text: str) -> None:
        """Replace the file with ``text`` atomically, keeping its mode."""
        write_atomic(self.path, text, keep_mode=self.exists, bom=self.bom)
        self.text, self.exists = text, True


#: In a path's name, what makes it one row of a SQLite database's key/value
#: table: ``<db>#sqlite:<table>:<key>`` (:func:`row_path`).
ROW_MARK = "#sqlite:"


def row_path(db: Path, table: str, key: str) -> Path:
    """The path that stands for row ``key`` of ``table`` (columns ``key`` and
    ``value``) of the SQLite database ``db``: read and written as the row's
    value, as a file's text is (NewMax's ``settings`` row ``app``)."""
    return Path(db).with_name(f"{Path(db).name}{ROW_MARK}{table}:{key}")


class SqliteRowText(ConfigText):
    """One row's value of a SQLite key/value table, as a config file's text.

    After magpie's ``newMaxReadApp`` / ``newMaxEdit`` (internal/agent/
    newmax.go, MIT): read without writing a byte beside the database, and
    written in one ``BEGIN IMMEDIATE`` transaction that waits on the app's
    own. A database not there yet is never made: the app makes it, with its
    tables, and a write before then fails."""

    db: Path
    table: str
    key: str

    @classmethod
    def read_row(cls, path: Path) -> "SqliteRowText":
        import sqlite3
        name, _, row = path.name.partition(ROW_MARK)
        table, _, key = row.partition(":")
        if not table.isidentifier():
            raise ConfigUnreadable(f"{path}: not a table name")
        db = Path(os.path.realpath(path.with_name(name)))
        found = cls(path, "", False, "\n")
        found.db, found.table, found.key = db, table, key
        if not db.is_file():
            return found
        try:
            hit = _read_untouched(db, f'SELECT value FROM "{table}" WHERE key = ?', (key,))
        except (sqlite3.Error, OSError) as exc:
            raise ConfigUnreadable(f"{db} cannot be read: {exc}") from exc
        if hit is not None:
            found.text, found.exists = str(hit[0]), True
        return found

    def _change(self, text: Optional[str]) -> None:
        import sqlite3
        if not self.db.is_file():
            raise OSError(f"{self.db} isn't there yet")
        try:
            with _closing(sqlite3.connect(f"{self.db.as_uri()}?mode=rw", uri=True, timeout=5,
                                          isolation_level=None)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if text is None:
                        conn.execute(f'DELETE FROM "{self.table}" WHERE key = ?', (self.key,))
                    else:
                        conn.execute(f'INSERT INTO "{self.table}" (key, value) VALUES (?, ?) '
                                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (self.key, text))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise OSError(f"{self.db}: {exc}") from exc

    def write(self, text: str) -> None:
        self._change(text)
        self.text, self.exists = text, True

    def delete(self) -> None:
        """Take the row out."""
        self._change(None)
        self.text, self.exists = "", False


def _read_untouched(db: Path, query: str, args: tuple) -> Any:
    """The first row ``query`` answers from another app's database ``db``,
    read without a byte written beside it (:func:`open_untouched`)."""
    with open_untouched(db) as conn:
        return conn.execute(query, args).fetchone()


@contextmanager
def open_untouched(db: Path) -> Iterator[Any]:
    """A read-only connection to another app's database ``db``, made without
    a byte written beside it (magpie ``sessions.OpenUntouched``). Even
    ``mode=ro`` makes a WAL database's ``-shm`` (and an empty ``-wal``)
    beside it, so: with no WAL content the file is opened ``immutable=1``,
    which takes no lock and makes no file; with some, the database and its
    WAL are copied into a folder of OpenProgram's and the copy is read.
    ``sqlite3.Error`` when it can't be read."""
    import shutil
    import sqlite3
    import tempfile
    db = Path(db)
    wal = db.with_name(db.name + "-wal")
    try:
        pending = wal.stat().st_size > 0
    except OSError:
        pending = False
    if not pending:
        with _closing(sqlite3.connect(f"{db.as_uri()}?immutable=1", uri=True, timeout=5)) as conn:
            yield conn
        return
    with tempfile.TemporaryDirectory(prefix="openprogram-sqlite-") as tmp:
        copied = Path(tmp) / "store.db"
        try:
            shutil.copyfile(db, copied)
            shutil.copyfile(wal, copied.with_name("store.db-wal"))
        except OSError as exc:
            raise sqlite3.OperationalError(str(exc)) from exc
        with _closing(sqlite3.connect(str(copied), timeout=3)) as conn:
            yield conn


def _closing(conn: Any) -> Any:
    from contextlib import closing
    return closing(conn)


def target(path: Path) -> Path:
    """The file a write of ``path`` goes to (magpie ``edit.Target``): when
    ``path`` is a symlink, the file at the end of its links, the path it
    names even when nothing is there yet. Renaming over the link instead
    would turn it into a file of its own and leave its target as it was."""
    return Path(os.path.realpath(path))


def hard_linked(path: Path) -> bool:
    """Whether the regular file at ``path`` has other names too (magpie ``hardLinked``)."""
    import stat
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink > 1


def _write_in_place(path: Path, data: bytes) -> bool:
    """Write ``data`` over the file at ``path`` itself, which keeps its mode,
    owner and every other name it has (magpie ``writeInPlace``); the whole
    new text waits in a temp file beside it until written. False when the
    file can't be opened for writing, to be renamed over as any other."""
    try:
        fd = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
    except PermissionError:
        return False
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp.write_bytes(data)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    # kept, with the whole new text, when the write above failed part way
    os.unlink(tmp)
    return True


def write_bytes_atomic(path: Path, data: bytes, *, keep_mode: bool = True, mode: int = 0o600) -> None:
    """Replace the file at ``path`` with ``data`` so a reader never sees it
    half written: a temp file beside it renamed over it, keeping its mode
    (``mode`` for a new file, or with ``keep_mode`` False). A symlink stays
    one and its target is written; a file with other hard links is written
    in place, so every name keeps the new text."""
    path = target(Path(path))
    if hard_linked(path) and _write_in_place(path, data):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep_mode:
        try:
            mode = path.stat().st_mode & 0o7777
        except OSError:
            pass
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        if hasattr(os, "chmod"):
            os.chmod(tmp, mode)
        from openprogram.host._platform.filesystem import replace_file
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_atomic(path: Path, text: str, *, keep_mode: bool = True, bom: bool = False) -> None:
    """:func:`write_bytes_atomic` of ``text`` as UTF-8, after a byte order mark with ``bom``."""
    write_bytes_atomic(path, (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"), keep_mode=keep_mode)


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def keep_crlf(out: str, orig: str) -> str:
    """``out`` with ``\\r\\n`` at every break when ``orig`` used it for all of them.

    After magpie's ``keepCRLF`` (internal/edit/json.go, MIT): a file that
    mixed endings is left as it was."""
    n = orig.count("\n")
    if n == 0 or orig.count("\r\n") != n:
        return out
    return out.replace("\r\n", "\n").replace("\n", "\r\n")


# Dotted key paths over nested dicts

def split_path(dotted: str) -> list[str]:
    """``a.b.c`` as its parts; a ``\\.`` is a dot inside a key (``chat\\.defaultModel``)."""
    parts, current, i = [], [], 0
    while i < len(dotted):
        char = dotted[i]
        if char == "\\" and i + 1 < len(dotted):
            current.append(dotted[i + 1])
            i += 2
            continue
        if char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


def get_path(data: Any, dotted: str) -> tuple[bool, Any]:
    node: Any = data
    for part in split_path(dotted):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = split_path(dotted)
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def delete_path(data: dict[str, Any], dotted: str) -> None:
    parts = split_path(dotted)
    node: Any = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)

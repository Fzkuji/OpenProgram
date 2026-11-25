"""Surgical edits of a harness's own config file: TOML (Codex) and JSON (Claude Code).

Only the keys being set or removed change. Comments, key order, blank lines
and CRLF line endings stay as they were; a symlinked file is written through
to its target; the write is a temp file renamed over the original. A file that
does not parse is :class:`ConfigUnreadable`: its contents are unknown, so
nothing is written to it. Every TOML edit is checked by parsing the result
and comparing it with the intended change, and refused when anything else
would differ.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigUnreadable(Exception):
    """The config file cannot be read or edited safely; it is left alone."""


@dataclass
class ConfigText:
    path: Path          # the file written (a symlink's target)
    text: str
    exists: bool
    newline: str

    @classmethod
    def read(cls, path: Path) -> "ConfigText":
        target = Path(os.path.realpath(path))
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return cls(target, "", False, "\n")
        except OSError as exc:
            raise ConfigUnreadable(f"{path} cannot be read: {exc}") from exc
        try:
            text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigUnreadable(f"{path} is not UTF-8 text") from exc
        return cls(target, text, True, "\r\n" if "\r\n" in text else "\n")

    def write(self, text: str) -> None:
        """Replace the file with ``text`` atomically, keeping its mode."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o600
        if self.exists:
            try:
                mode = self.path.stat().st_mode & 0o777
            except OSError:
                pass
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
        try:
            try:
                os.write(fd, text.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            if hasattr(os, "chmod"):
                os.chmod(tmp, mode)
            from openprogram.host._platform.filesystem import replace_file
            replace_file(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.text, self.exists = text, True


# TOML

def toml_load(text: str, where: str = "config") -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigUnreadable(f"{where} is not valid TOML: {exc}") from exc


def toml_value(value: Any) -> str:
    """A TOML literal for a string, bool or number."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]#]+?)\s*\]\]?\s*(#.*)?$")
_SIMPLE_VALUE = r'("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^#\s]+)'


def _header_name(line: str) -> str | None:
    match = _HEADER.match(line.rstrip("\r\n"))
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1)).replace('"', "").replace("'", "")


def _key_line(key: str) -> re.Pattern:
    quoted = re.escape(key)
    return re.compile(rf'^(\s*(?:{quoted}|"{quoted}")\s*=\s*){_SIMPLE_VALUE}?(\s*#.*)?\s*$')


def _section(lines: list[str], table: str | None) -> tuple[int, int] | None:
    """``(start, end)`` line range of a table's body; the top level for ``None``."""
    start = 0
    if table is not None:
        for i, line in enumerate(lines):
            if _header_name(line) == table:
                start = i + 1
                break
        else:
            return None
    end = len(lines)
    for i in range(start, len(lines)):
        if _header_name(lines[i]) is not None:
            end = i
            break
    return start, end


def _ensure_newline(lines: list[str], newline: str) -> None:
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline


def _set_in(lines: list[str], span: tuple[int, int], key: str, value: Any, newline: str) -> None:
    start, end = span
    pattern = _key_line(key)
    for i in range(start, end):
        match = pattern.match(lines[i].rstrip("\r\n"))
        if match:
            ending = lines[i][len(lines[i].rstrip("\r\n")):] or newline
            lines[i] = f"{match.group(1)}{toml_value(value)}{match.group(3) or ''}{ending}"
            return
    at = end
    while at > start and (not lines[at - 1].strip() or lines[at - 1].lstrip().startswith("#")):
        at -= 1
    if at > 0 and not lines[at - 1].endswith(("\n", "\r")):
        lines[at - 1] += newline
    lines.insert(at, f"{key} = {toml_value(value)}{newline}")


def _expected(doc: dict[str, Any], sets: dict[str, Any], deletes: list[str],
              table: str | None, table_values: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(doc)
    for key, value in sets.items():
        result[key] = value
    for key in deletes:
        result.pop(key, None)
    if table:
        node = result
        for part in table.split("."):
            node = node.setdefault(part, {})
        node.update(table_values)
    return result


def toml_edit(text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
              table: str | None = None, table_values: dict[str, Any] | None = None,
              where: str = "config") -> str:
    """``text`` with top-level ``sets``/``deletes`` and ``table_values`` set in ``[table]``."""
    sets, deletes, table_values = sets or {}, deletes or [], table_values or {}
    doc = toml_load(text, where)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for key in deletes:
        span = _section(lines, None)
        pattern = _key_line(key)
        lines = [line for i, line in enumerate(lines)
                 if not (span[0] <= i < span[1] and pattern.match(line.rstrip("\r\n")))]
    for key, value in sets.items():
        _set_in(lines, _section(lines, None), key, value, newline)
    if table and table_values:
        span = _section(lines, table)
        if span is None:
            node: Any = doc
            for part in table.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node is not None:
                raise ConfigUnreadable(f"{where} defines [{table}] in a form this edit does not change")
            _ensure_newline(lines, newline)
            if lines and lines[-1].strip():
                lines.append(newline)
            lines.append(f"[{table}]{newline}")
            lines.extend(f"{key} = {toml_value(value)}{newline}" for key, value in table_values.items())
        else:
            for key, value in table_values.items():
                _set_in(lines, _section(lines, table), key, value, newline)
    edited = "".join(lines)
    if toml_load(edited, where) != _expected(doc, sets, deletes, table, table_values):
        raise ConfigUnreadable(f"{where} could not be edited without changing other settings")
    return edited


# JSON

def json_load(text: str, where: str = "settings") -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigUnreadable(f"{where} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{where} is not a JSON object")
    return data


def json_dump_like(original: str, data: dict[str, Any]) -> str:
    """``data`` written in ``original``'s indentation and line endings."""
    indent: str | int = 2
    for line in original.splitlines():
        match = re.match(r"^([ \t]+)\"", line)
        if match:
            indent = match.group(1)
            break
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    newline = "\r\n" if "\r\n" in original else "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    if not original or original.endswith(("\n", "\r")):
        text += newline
    return text


def get_path(data: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def delete_path(data: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    node: Any = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)

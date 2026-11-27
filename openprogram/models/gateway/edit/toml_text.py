"""Surgical TOML edits (Codex's ``config.toml``, and every TOML agent after it).

Only the keys being set or removed change. Comments, key order, blank lines
and CRLF line endings stay as they were. Every edit is checked by parsing the
result and comparing it with the intended change, and refused when anything
else would differ (as magpie's ``internal/edit/toml.go`` refuses, MIT).
"""
from __future__ import annotations

import copy
import json
import re
import tomllib
from typing import Any, Callable

from .text import ConfigUnreadable


# TOML

def toml_load(text: str, where: str = "config") -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigUnreadable(f"{where} is not valid TOML: {exc}") from exc


def toml_value(value: Any) -> str:
    """A TOML literal for a string, bool, number, array or inline table."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key_literal(str(k))} = {toml_value(v)}" for k, v in value.items()) + " }" if value else "{}"
    return json.dumps(str(value), ensure_ascii=False)


_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]#]+?)\s*\]\]?\s*(#.*)?$")
_SIMPLE_VALUE = r'("(?:[^"\\]|\\.)*"|\'[^\']*\'|\{[^{}\n]*\}|\[[^\[\]\n]*\]|[^#\s]+)'


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
    found = _complex_value(lines, span, key)
    if found is not None and (found[1] > found[0] or not pattern.match(lines[found[0]].rstrip("\r\n"))):
        # an inline array or table over several lines (a model list)
        first, last, head = found
        ending = lines[last][len(lines[last].rstrip("\r\n")):] or newline
        comment = _trailing_comment(lines, first, last, head)
        lines[first:last + 1] = [f"{head}{toml_value(value)}{comment}{ending}"]
        return
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
    lines.insert(at, f"{_key_literal(key)} = {toml_value(value)}{newline}")


def _complex_value(lines: list[str], span: tuple[int, int], key: str) -> tuple[int, int, str] | None:
    """``(first line, last line, "key = ")`` of a key whose value is an inline
    array or table, on one line or several (a model list)."""
    quoted = re.escape(key)
    head = re.compile(rf'^(\s*(?:{quoted}|"{quoted}")\s*=\s*)')
    for i in range(span[0], span[1]):
        match = head.match(lines[i])
        if not match:
            continue
        value = lines[i][match.end():]
        for j in range(i, span[1]):
            if j > i:
                value += lines[j]
            try:
                tomllib.loads("v = " + value)
            except tomllib.TOMLDecodeError:
                continue
            return i, j, match.group(1)
        return None
    return None


def _trailing_comment(lines: list[str], first: int, last: int, head: str) -> str:
    """The comment after the value on its last line (``] # keep me``), with
    the space before it; ``""`` for none."""
    if last > first:
        before, body = lines[first][len(head):] + "".join(lines[first + 1:last]), lines[last].rstrip("\r\n")
    else:
        before, body = "", lines[first].rstrip("\r\n")[len(head):]
    for k, char in enumerate(body):
        if char != "#":
            continue
        try:
            tomllib.loads("v = " + before + body[:k])
        except tomllib.TOMLDecodeError:
            continue
        return body[len(body[:k].rstrip()):]
    return ""


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key_literal(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _norm(table: str | None) -> str | None:
    return None if table is None else re.sub(r"\s+", "", table).replace('"', "").replace("'", "")


def _split(key: str) -> tuple[str | None, str, list[str]]:
    """``(table as written, key, parts)`` of a dotted key; ``\\.`` keeps a dot in a name."""
    from .text import split_path
    parts = split_path(key)
    table = ".".join(parts[:-1]) or None
    clean = [part.strip('"').strip("'") for part in parts]
    return table, parts[-1].strip('"').strip("'"), clean


def _implied(lines: list[str], table: str | None) -> bool:
    """``table`` exists only through headers of tables inside it (``[agents.x]``
    with no ``[agents]``), so a header of its own may still be added; a table
    made by dotted keys or written inline may not, which the parse after the
    edit refuses."""
    if table is None:
        return False
    return any((name := _header_name(line)) is not None and name.startswith(table + ".") for line in lines)


def _without_table(lines: list[str], span: tuple[int, int]) -> list[str]:
    """``lines`` without a table's header and body, the blank line before it too."""
    start, end = span[0] - 1, span[1]
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    return lines[:start] + lines[end:]


def _array_of_tables(key: str, value: Any) -> bool:
    """A top-level key set to a list of tables, written as ``[[key]]`` blocks."""
    return ("." not in key and isinstance(value, list) and bool(value)
            and all(isinstance(item, dict) for item in value))


_KEY_START = re.compile(r"""^\s*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_.\-]+)\s*=""")


def _value_end(lines: list[str], i: int, start: int) -> int:
    """The last line of the value that begins at ``lines[i][start:]``: an
    inline array or table, or a multi-line string, may run over several."""
    value = lines[i][start:]
    for j in range(i, len(lines)):
        if j > i:
            value += lines[j]
        try:
            tomllib.loads("v = " + value)
        except tomllib.TOMLDecodeError:
            continue
        return j
    return i


def _tables(lines: list[str]) -> list[dict[str, Any]]:
    """Each table header in ``lines`` (a line inside a multi-line value is
    none): ``name``, ``array``, ``from`` and ``to``, the line after its last
    key (after the header for one with none), so the comments and blank lines
    after it are not its own (magpie ``tomlContentEnd``)."""
    tables: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        name = _header_name(line) if line.lstrip().startswith("[") else None
        if name is not None:
            tables.append({"name": name, "array": line.lstrip().startswith("[["), "from": i, "to": i + 1})
            i += 1
            continue
        match = _KEY_START.match(line)
        if match:
            j = _value_end(lines, i, match.end())
            if tables:
                tables[-1]["to"] = j + 1
            i = j + 1
            continue
        i += 1
    return tables


def _entries(lines: list[str], key: str) -> list[tuple[list[tuple[int, int]], Any]]:
    """The ``[[key]]`` entries: each one's line spans (its block, then those
    of its child tables) and what it reads as (magpie ``namedTOMLArray``)."""
    tables = _tables(lines)
    out: list[tuple[list[tuple[int, int]], Any]] = []
    for n, table in enumerate(tables):
        if table["name"] != key or not table["array"]:
            continue
        spans = [(table["from"], table["to"])]
        for later in tables[n + 1:]:
            if later["name"] == key:
                break
            if later["name"].startswith(key + "."):
                spans.append((later["from"], later["to"]))
        text = "".join("".join(lines[a:b]) for a, b in spans)
        try:
            entry = (tomllib.loads(text).get(key) or [None])[0]
        except tomllib.TOMLDecodeError:
            entry = None
        out.append((spans, entry))
    return out


def _blank(line: str) -> bool:
    return not line.strip()


def _trim_end(lines: list[str]) -> list[str]:
    while lines and _blank(lines[-1]):
        lines = lines[:-1]
    return lines


def _set_blocks(lines: list[str], key: str, value: list[Any], ours: Callable[[Any], bool] | None,
                newline: str) -> tuple[list[str], list[Any]]:
    """``lines`` with OpenProgram's ``[[key]]`` entries (those ``ours`` says
    are; every one without it) replaced by the entries of ``value`` that are,
    where the first of them was, else at the end. The user's entries and the
    comments and blank lines between and after entries stay as written
    (magpie ``SetTOMLArrayTables``). Also what ``key`` then reads as."""
    entries = _entries(lines, key)
    found = _complex_value(lines, _section(lines, None) or (0, 0), key)
    if found is not None:
        # a top-level ``key = [...]`` is replaced by blocks
        del lines[found[0]:found[1] + 1]
        entries, ours = _entries(lines, key), None
    mine = (lambda item: True) if ours is None else ours
    theirs = [entry for _spans, entry in entries if not mine(entry)]
    new_ours = [item for item in value if mine(item)]
    if [item for item in value if not mine(item)] != theirs:
        # the user's entries change too: every entry is written again
        mine, theirs, new_ours = (lambda item: True), [], list(value)
    matched = [(spans, entry) for spans, entry in entries if mine(entry)]
    before = sum(1 for spans, entry in entries
                 if not mine(entry) and (not matched or spans[0][0] < matched[0][0][0][0]))
    result = [*theirs[:before], *new_ours, *theirs[before:]]
    replacement: list[str] = []
    for item in new_ours:
        if replacement:
            replacement.append(newline)
        replacement.append(f"[[{_key_literal(key)}]]{newline}")
        replacement.extend(f"{_key_literal(str(k))} = {toml_value(v)}{newline}" for k, v in item.items())
    spans = sorted(span for spans_, _entry in matched for span in spans_)
    if not spans:
        if not replacement:
            return lines, result
        out = _trim_end(list(lines))
        _ensure_newline(out, newline)
        if out:
            out.append(newline)
        return out + replacement, result
    out = []
    at = 0
    for n, (first, last) in enumerate(spans):
        gap = lines[at:first]
        # the blank lines between two entries replaced go with them, or every
        # sync would add another
        if n == 0 or any(not _blank(line) for line in gap):
            out.extend(gap)
        if n == 0:
            if not replacement:
                out = _trim_end(out)
            else:
                _ensure_newline(out, newline)
            out.extend(replacement)
        at = last
    rest = lines[at:]
    if rest and out and not out[-1].endswith(("\n", "\r")):
        out[-1] += newline
    out.extend(rest)
    return out, result


def toml_edit(text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
              table: str | None = None, table_values: dict[str, Any] | None = None,
              where: str = "config", members: dict[str, Callable[[Any], bool]] | None = None) -> str:
    """``text`` with ``sets``/``deletes`` (dotted: ``table.key``; bare: top level)
    and ``table_values`` set in ``[table]``. A top-level list of tables is
    written as ``[[key]]`` blocks; with ``members[key]``, only the entries it
    says are OpenProgram's are replaced, the user's left as written."""
    sets, deletes = dict(sets or {}), list(deletes or [])
    for key, value in (table_values or {}).items():
        sets[f"{table}.{key.replace('.', chr(92) + '.')}"] = value
    doc = toml_load(text, where)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    expected = copy.deepcopy(doc)
    from .text import delete_path, get_path, set_path
    # an array of tables at the top level ([[providers]], [[models]]) is
    # written whole, as its blocks (magpie SetTOMLArrayTables)
    members = members or {}
    tables = {key: value for key, value in sets.items() if "." not in key and (
        _array_of_tables(key, value) or key in members and isinstance(value, list)
        and all(isinstance(item, dict) for item in value))}
    for key in (k for k in deletes if "." not in k):
        lines, _result = _set_blocks(lines, key, [], None, newline)
    for key, value in tables.items():
        sets.pop(key)
        lines, result = _set_blocks(lines, key, value, members.get(key), newline)
        if result:
            expected[key] = copy.deepcopy(result)
        else:
            expected.pop(key, None)
    for dotted in deletes:
        owner, key, parts = _split(dotted)
        # headers name their tables unescaped (a quoted "b.1" reads b.1)
        name = _norm(".".join(parts))
        whole = _section(lines, name)
        if whole is not None:
            # a table with a header of its own goes with its header, and its
            # sub-tables ([models."a".x]) with it
            lines = _without_table(lines, whole)
            while True:
                sub = next((h for line in lines if (h := _header_name(line)) is not None
                            and (_norm(h) or "").startswith(name + ".")), None)
                span = _section(lines, _norm(sub)) if sub is not None else None
                if span is None:
                    break
                lines = _without_table(lines, span)
            delete_path(expected, ".".join(p.replace(".", "\\.") for p in parts))
            # a parent only its sub-tables made ([models] of [models."a"]) goes with the last of them
            for depth in range(len(parts) - 1, 0, -1):
                parent = ".".join(p.replace(".", "\\.") for p in parts[:depth])
                present, node = get_path(expected, parent)
                if present and node == {} and _section(lines, _norm(".".join(parts[:depth]))) is None:
                    delete_path(expected, parent)
            continue
        span = _section(lines, _norm(owner))
        if span is not None:
            pattern = _key_line(key)
            found = _complex_value(lines, span, key)
            if found is not None and (found[1] > found[0] or not pattern.match(lines[found[0]].rstrip("\r\n"))):
                # an inline array or table over several lines
                del lines[found[0]:found[1] + 1]
                span = _section(lines, _norm(owner)) or (0, 0)
            lines = [line for i, line in enumerate(lines)
                     if not (span[0] <= i < span[1] and pattern.match(line.rstrip("\r\n")))]
        delete_path(expected, ".".join(p.replace(".", "\\.") for p in parts))
    for dotted, value in sets.items():
        owner, key, parts = _split(dotted)
        span = _section(lines, _norm(owner))
        if span is None:
            present, _node = get_path(expected, ".".join(p.replace(".", "\\.") for p in parts[:-1]))
            if present and not _implied(lines, _norm(owner)):
                raise ConfigUnreadable(f"{where} defines [{owner}] in a form this edit does not change")
            _ensure_newline(lines, newline)
            if lines and lines[-1].strip():
                lines.append(newline)
            lines.append(f"[{'.'.join(_key_literal(part) for part in parts[:-1])}]{newline}")
            lines.append(f"{_key_literal(key)} = {toml_value(value)}{newline}")
        else:
            _set_in(lines, span, key, value, newline)
        set_path(expected, ".".join(p.replace(".", "\\.") for p in parts), value)
    edited = "".join(lines)
    if toml_load(edited, where) != expected:
        raise ConfigUnreadable(f"{where} could not be edited without changing other settings")
    return edited

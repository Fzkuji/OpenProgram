"""JSON and JSONC edits that keep comments, key order and indentation.

Ported from yetone/magpie ``internal/edit/json.go`` and ``jsonc_valid.go``
(MIT License, Copyright (c) yetone). Comments are blanked out in a copy of
the same length, so offsets found in the copy are offsets in the file; each
change is a splice of the original text: a value replaced where it is, a new
key inserted as the first member of its object at the object's indentation,
a removed member taken out with its own line and comma. The result must
parse as JSONC the way the apps that write it read it (a trailing comma
allowed, a lone one not) and hold exactly the intended change, or nothing is
written.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .text import ConfigUnreadable, delete_path, keep_crlf, set_path, split_path

_WS = " \t\r\n"


# Reading

def blank_comments(raw: str) -> str | None:
    """``raw`` with each comment turned to spaces, line breaks kept; ``None``
    when a block comment is never closed (magpie ``blankComments``)."""
    out = list(raw)
    in_str = False
    i = 0
    n = len(out)
    while i < n:
        c = out[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "/" and i + 1 < n and out[i + 1] == "/":
            while i < n and out[i] != "\n":
                out[i] = " "
                i += 1
            continue
        elif c == "/" and i + 1 < n and out[i + 1] == "*":
            end = raw.find("*/", i + 2)
            if end < 0:
                return None
            for j in range(i, end + 2):
                if out[j] not in "\r\n":
                    out[j] = " "
            i = end + 2
            continue
        i += 1
    return "".join(out)


def _trailing_commas(b: str) -> list[int]:
    """Offsets of commas (outside strings) a closing bracket follows."""
    at: list[int] = []
    in_str = False
    i = 0
    while i < len(b):
        c = b[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < len(b) and b[j] in _WS:
                j += 1
            if j < len(b) and b[j] in "}]":
                at.append(i)
        i += 1
    return at


def _lone(b: str, comma: int) -> bool:
    """A comma with nothing before it: ``{ , }``, ``[,]``, ``,,``."""
    j = comma - 1
    while j >= 0 and b[j] in _WS:
        j -= 1
    return j < 0 or b[j] in "{[,"


def to_json(raw: str) -> str | None:
    """Comment-free, trailing-comma-free JSON of the same length; ``None`` if not JSONC."""
    b = blank_comments(raw)
    if b is None:
        return None
    chars = list(b)
    for i in _trailing_commas(b):
        if _lone(b, i):
            return None
        chars[i] = " "
    return "".join(chars)


def valid_jsonc(raw: str) -> bool:
    """Whether ``raw`` is JSONC as OpenCode, VS Code and Zed read it (magpie ``ValidJSONC``)."""
    stripped = to_json(raw)
    if stripped is None:
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def jsonc_load(text: str, where: str = "settings") -> dict[str, Any]:
    if not text.strip():
        return {}
    stripped = to_json(text)
    try:
        data = json.loads(stripped) if stripped is not None else None
    except ValueError as exc:
        raise ConfigUnreadable(f"{where} is not valid JSON: {exc}") from exc
    if stripped is None:
        raise ConfigUnreadable(f"{where} is not valid JSON: an unclosed comment or a stray comma")
    if not isinstance(data, dict):
        raise ConfigUnreadable(f"{where} is not a JSON object")
    return data


# Spans in comment-free text

def _skip(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _string_end(s: str, i: int) -> int:
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i + 1
        i += 1
    raise ConfigUnreadable("an unterminated string")


def _value_end(s: str, i: int) -> int:
    c = s[i]
    if c == '"':
        return _string_end(s, i)
    if c in "{[":
        depth = 0
        while i < len(s):
            c = s[i]
            if c == '"':
                i = _string_end(s, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise ConfigUnreadable("an unclosed object")
    while i < len(s) and s[i] not in ",}] \t\r\n":
        i += 1
    return i


def _members(s: str, open_: int):
    """``(key, key_start, value_start, value_end)`` of the object opening at ``open_``."""
    i = _skip(s, open_ + 1)
    while i < len(s) and s[i] != "}":
        if s[i] == ",":
            i = _skip(s, i + 1)
            continue
        key_end = _string_end(s, i)
        key = json.loads(s[i:key_end])
        colon = _skip(s, key_end)
        start = _skip(s, colon + 1)
        end = _value_end(s, start)
        yield key, i, start, end
        i = _skip(s, end)


def _lookup(s: str, parts: list[str]) -> tuple[int, int] | None:
    """The span of the value at ``parts``, or ``None``."""
    at = _skip(s, 0)
    end = _value_end(s, at)
    for part in parts:
        if s[at] != "{":
            return None
        for key, _key_start, start, stop in _members(s, at):
            if key == part:
                at, end = start, stop
                break
        else:
            return None
    return at, end


def _match_brace(s: str, open_: int) -> int:
    try:
        return _value_end(s, open_) - 1
    except ConfigUnreadable:
        return -1


# Writing

def _line_indent(raw: str, pos: int) -> str:
    start = raw.rfind("\n", 0, pos) + 1
    i = start
    while i < len(raw) and raw[i] in " \t":
        i += 1
    return raw[start:i]


def _indent_unit(raw: str, pos: int, base: str) -> str:
    nl = raw.find("\n", pos)
    if nl >= 0:
        following = _line_indent(raw, nl + 1)
        if len(following) > len(base) and following.startswith(base):
            return following[len(base):]
    if "\t" in base or "\n\t" in raw:
        return "\t"
    return "  "


def _dumps(value: Any, base: str, unit: str) -> str:
    text = json.dumps(value, indent=unit, ensure_ascii=False)
    return text.replace("\n", "\n" + base)


def _pretty(raw: str) -> bool:
    """Whether the file is laid out over lines. A file of one line keeps to
    one, its own line end after it aside, so a key put in and taken out again
    leaves it as it was; an empty one ending in a line end is laid out like
    a new file."""
    body = raw.strip()
    if "\n" in body:
        return True
    return "\n" in raw and not (to_json(body) or body)[1:-1].strip()


def _marshal_at(raw: str, pos: int, value: Any) -> str:
    if not _pretty(raw):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    base = _line_indent(raw, pos)
    return _dumps(value, base, _indent_unit(raw, pos, base))


def _nest(parts: list[str], value: Any) -> Any:
    for part in reversed(parts):
        value = {part: value}
    return value


def _insert_key(raw: str, stripped: str, from_: int, key: str, value: Any) -> str:
    """``key: value`` as the first member of the object at or after ``from_``."""
    open_ = from_
    while open_ < len(raw) and stripped[open_] != "{":
        open_ += 1
    close = _match_brace(stripped, open_) if open_ < len(raw) else -1
    if close < 0:
        raise ConfigUnreadable(f"cannot locate the object for {key!r}")
    literal = json.dumps(key, ensure_ascii=False)
    pretty = _pretty(raw)
    empty = not raw[open_ + 1:close].strip()
    members = bool(stripped[open_ + 1:close].strip())
    if not pretty:
        inserted = literal + ":" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if members:
            inserted += ","
        return raw[:open_ + 1] + inserted + raw[open_ + 1:]
    base = _line_indent(raw, open_)
    unit = _indent_unit(raw, open_, base)
    child = base + unit
    member = child + literal + ": " + _dumps(value, child, unit)
    if empty:
        return raw[:open_] + "{\n" + member + "\n" + base + "}" + raw[close + 1:]
    if not members:
        return raw[:open_ + 1] + "\n" + member + raw[open_ + 1:]
    return raw[:open_ + 1] + "\n" + member + "," + raw[open_ + 1:]


def _strip(raw: str) -> str:
    stripped = to_json(raw)
    if stripped is None:
        raise ConfigUnreadable("invalid JSON/JSONC")
    try:
        root = json.loads(stripped)
    except ValueError as exc:
        raise ConfigUnreadable(f"invalid JSON/JSONC: {exc}") from exc
    if not isinstance(root, dict):
        raise ConfigUnreadable("top level is not a JSON object")
    return stripped


def _set(raw: str, parts: list[str], value: Any) -> str:
    stripped = _strip(raw)
    for i in range(len(parts), 0, -1):
        span = _lookup(stripped, parts[:i])
        if span is None:
            continue
        start, end = span
        if i == len(parts) or stripped[start] != "{":
            return raw[:start] + _marshal_at(raw, start, _nest(parts[i:], value)) + raw[end:]
        return _insert_key(raw, stripped, start, parts[i], _nest(parts[i + 1:], value))
    return _insert_key(raw, stripped, 0, parts[0], _nest(parts[1:], value))


def _past_line_end(raw: str, i: int) -> int:
    if raw.startswith("\r\n", i):
        return i + 2
    if raw.startswith("\n", i):
        return i + 1
    return i


def _next_token(raw: str, i: int) -> int:
    stripped = blank_comments(raw) or raw
    while i < len(stripped) and stripped[i] in _WS:
        i += 1
    return i


def _delete(raw: str, parts: list[str]) -> str:
    stripped = _strip(raw)
    span = _lookup(stripped, parts)
    if span is None or span[0] == 0:
        return raw
    value_start, b = span
    a = value_start
    while a > 0 and stripped[a - 1] in " \t\r\n:":
        a -= 1
    if a == 0 or stripped[a - 1] != '"':
        raise ConfigUnreadable(f"cannot locate key {'.'.join(parts)!r}")
    a -= 1
    while a > 0 and stripped[a - 1] != '"':
        a -= 1
    a -= 1
    line_start = raw.rfind("\n", 0, a) + 1
    alone = not raw[line_start:a].strip()
    j = b
    while j < len(stripped) and stripped[j] in _WS:
        j += 1
    if j < len(stripped) and stripped[j] == ",":
        start, stop = a, j + 1
        if alone:
            start = line_start
            stop = _past_line_end(raw, stop)
        out = raw[:start] + raw[stop:]
    else:
        t = _next_token(raw, b)
        if t < len(raw) and raw[t] == ",":
            raw = raw[:t] + raw[t + 1:]
            stripped = stripped[:t] + stripped[t + 1:]
        start, stop = a, b
        if alone:
            start = line_start
            e = stop
            while e < len(raw) and raw[e] in " \t":
                e += 1
            p = _past_line_end(raw, e)
            if p > e:
                stop = p
        out = raw[:start] + raw[stop:]
        k = a
        while k > 0 and stripped[k - 1] in _WS:
            k -= 1
        if k > 0 and stripped[k - 1] == ",":
            out = out[:k - 1] + out[k:]
    # an object left empty collapses to {}
    again = to_json(out)
    if again is not None:
        parent = _lookup(again, parts[:-1]) if len(parts) > 1 else (_skip(again, 0), len(again))
        if parent is not None and again[parent[0]] == "{":
            open_ = parent[0]
            close = _match_brace(again, open_)
            if close > open_ and not again[open_ + 1:close].strip():
                out = out[:open_] + "{}" + out[close + 1:]
    return out


def jsonc_edit(text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
               where: str = "settings") -> str:
    """``text`` with dotted ``sets`` and ``deletes``, everything else kept as written."""
    sets, deletes = sets or {}, deletes or []
    raw = text if text.strip() else "{}\n"
    doc = jsonc_load(raw, where)
    expected = copy.deepcopy(doc)
    out = raw
    try:
        for dotted, value in sets.items():
            out = _set(out, split_path(dotted), value)
            set_path(expected, dotted, value)
        for dotted in deletes:
            out = _delete(out, split_path(dotted))
            delete_path(expected, dotted)
    except ConfigUnreadable as exc:
        raise ConfigUnreadable(f"{where}: {exc}") from exc
    out = keep_crlf(out, raw)
    if not valid_jsonc(out) or jsonc_load(out, where) != expected:
        raise ConfigUnreadable(f"{where} could not be edited without changing other settings")
    return out

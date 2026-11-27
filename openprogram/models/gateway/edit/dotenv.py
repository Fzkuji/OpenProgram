"""dotenv files (``KEY=value`` per line), as Gemini CLI reads ``~/.gemini/.env``.

Ported from yetone/magpie ``internal/edit/env.go`` (MIT License, Copyright
(c) yetone). A key is replaced on its own line and a new one appended after
the last key; every other line is kept byte for byte. A key set twice takes
its last value, as dotenv readers do.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .text import keep_crlf

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _unquote(value: str) -> tuple[str, bool]:
    if not value or value[0] not in "\"'":
        return "", False
    quote = value[0]
    i = 1
    while i < len(value):
        if quote == '"' and value[i] == "\\":
            i += 2
            continue
        if value[i] == quote:
            if quote == '"':
                try:
                    return json.loads(value[:i + 1]), True
                except ValueError:
                    pass
            return value[1:i], True
        i += 1
    return value[1:], True


def _value(raw: str) -> str:
    text, quoted = _unquote(raw)
    if quoted:
        return text
    at = raw.find("#")
    return raw[:at].strip() if at >= 0 else raw


def dotenv_load(text: str, where: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = _value(match.group(2))
    return values


def _literal(value: Any) -> str:
    text = "true" if value is True else "false" if value is False else str(value)
    if any(c in text for c in " #\"'$\r\n"):
        # single quotes keep backslashes literal in Gemini CLI's dotenv
        if not any(c in text for c in "'\r\n"):
            return f"'{text}'"
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def dotenv_edit(text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
                where: str = ".env") -> str:
    lines = text.replace("\r\n", "\n").split("\n") if text else [""]
    drop = set(deletes or [])
    if drop:
        lines = [line for line in lines if not ((m := _LINE.match(line)) and m.group(1) in drop)]
    for key, value in (sets or {}).items():
        new = f"{key}={_literal(value)}"
        last_key = same = -1
        for i, line in enumerate(lines):
            match = _LINE.match(line)
            if not match:
                continue
            if match.group(1) == key:
                same = i
            last_key = i
        if same >= 0:
            lines[same] = new
        else:
            lines.insert(last_key + 1, new)
    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    if not out.strip():
        out = ""
    return keep_crlf(out, text)

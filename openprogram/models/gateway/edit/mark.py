"""Notes over the lines OpenProgram writes into a TOML, YAML or dotenv config.

Ported from yetone/magpie ``internal/edit/mark.go`` and
``internal/agent/mark.go`` (MIT License, Copyright (c) yetone). A line that
points an agent at the gateway gets a comment line of its own just above it
saying what wrote it and how to undo it, for someone reading the file after
OpenProgram is gone. A note goes on a line this write adds or changes, and
stays on one that had it; a line of the user's own left as it is gets none.
JSON has no comments, so nothing is marked there. Should a note change what
the file reads as, none is added.
"""
from __future__ import annotations

import re
from typing import Callable

TAG = "openprogram: "
COMMENT = "# " + TAG

#: ``note(table, key, value)`` → the note for a line, ``""`` for none. Set by
#: the agent registry (``agents.engine``), as magpie's main sets it.
note: Callable[[str, str, str], str] | None = None


def is_note(line: str) -> bool:
    return line.strip().startswith(COMMENT)


def _bare(lines: list[str]) -> list[str]:
    return [line for line in lines if not is_note(line)]


def _noted(orig: str) -> dict[str, bool]:
    seen: dict[str, bool] = {}
    previous = False
    for line in orig.replace("\r\n", "\n").split("\n"):
        if is_note(line):
            previous = True
            continue
        seen[line] = seen.get(line, False) or previous
        previous = False
    return seen


def _with_notes(lines: list[str], orig: str, notes: dict[int, str]) -> list[str]:
    was = _noted(orig)
    out: list[str] = []
    for i, line in enumerate(lines):
        key = line.rstrip("\r\n")
        text = notes.get(i)
        if text and (was.get(key) or key not in was):
            indent = key[:len(key) - len(key.lstrip(" \t"))]
            ending = line[len(key):] or "\n"
            out.append(indent + COMMENT + text + ending)
        out.append(line)
    return out


def _value_of(raw: str) -> str:
    import tomllib
    try:
        value = tomllib.loads(f"x = {raw}")["x"]
    except Exception:  # noqa: BLE001 - not a one-line scalar
        return ""
    return value if isinstance(value, str) else str(value)


_TOML_KEY = re.compile(r'^\s*([A-Za-z0-9_-]+|"[^"]*")\s*=\s*(.*?)\s*$')


def mark_toml(text: str, orig: str) -> str:
    from .toml_text import _header_name, toml_load
    if note is None:
        return text
    lines = _bare(text.splitlines(keepends=True))
    notes: dict[int, str] = {}
    table = ""
    for i, line in enumerate(lines):
        header = _header_name(line)
        if header is not None:
            table = header
            continue
        match = _TOML_KEY.match(line.rstrip("\r\n"))
        if not match:
            continue
        value = _value_of(match.group(2))
        if value:
            text_ = note(table, match.group(1).strip('"'), value)
            if text_:
                notes[i] = text_
    out = "".join(_with_notes(lines, orig, notes))
    try:
        return out if toml_load(out) == toml_load(text) else "".join(lines)
    except Exception:  # noqa: BLE001 - left for the edit's own check to refuse
        return text


def mark_yaml(text: str, orig: str) -> str:
    import yaml
    from .yaml_text import yaml_load
    if note is None:
        return text
    lines = _bare(text.splitlines(keepends=True))
    try:
        root = yaml.compose("".join(lines))
    except yaml.YAMLError:
        return text
    notes: dict[int, str] = {}

    def walk(node, table: str) -> None:
        if isinstance(node, yaml.SequenceNode):
            for child in node.value:
                walk(child, table)
        elif isinstance(node, yaml.MappingNode) and not node.flow_style:
            for key, value in node.value:
                path = f"{table}.{key.value}" if table else str(key.value)
                if not isinstance(value, yaml.ScalarNode):
                    walk(value, path)
                    continue
                at = key.start_mark.line
                if value.start_mark.line != at or value.style in ("|", ">") or not 0 <= at < len(lines):
                    continue
                text_ = note(table, str(key.value), str(value.value))
                if text_:
                    notes[at] = text_

    if root is not None:
        walk(root, "")
    out = "".join(_with_notes(lines, orig, notes))
    try:
        return out if yaml_load(out) == yaml_load(text) else "".join(lines)
    except Exception:  # noqa: BLE001
        return text


def mark_dotenv(text: str, orig: str) -> str:
    from .dotenv import _LINE, _value
    if note is None:
        return text
    lines = _bare(text.splitlines(keepends=True))
    notes: dict[int, str] = {}
    for i, line in enumerate(lines):
        match = _LINE.match(line.rstrip("\r\n"))
        if match:
            text_ = note("", match.group(1), _value(match.group(2)))
            if text_:
                notes[i] = text_
    return "".join(_with_notes(lines, orig, notes))

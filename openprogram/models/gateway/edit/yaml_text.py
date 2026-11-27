"""YAML edits that keep comments, order and layout.

After yetone/magpie ``internal/edit/yaml_top.go`` and ``yaml.go`` (MIT
License, Copyright (c) yetone). Entries are located by the parser (PyYAML's
composed nodes carry each key's line), then edited as lines: a key's line is
replaced where it is, a new key is added after the last entry of its mapping
at that mapping's indentation (missing mappings are added on the way), and a
removed key takes its whole entry with it, its trailing comments and blank
lines left in place. Only block mappings are edited; a flow mapping, a key
that is not a plain scalar on its own line, or a result that parses to
anything but the intended change leaves the file untouched.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import yaml

from .text import ConfigUnreadable, delete_path, keep_crlf, set_path, split_path

_PLAIN = re.compile(r"^[A-Za-z0-9._/-]+$")


def yaml_load(text: str, where: str = "config") -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        docs = [doc for doc in yaml.safe_load_all(text)]
    except yaml.YAMLError as exc:
        raise ConfigUnreadable(f"{where} is not valid YAML: {exc}") from exc
    first = docs[0] if docs else None
    if first is None:
        return {}
    if not isinstance(first, dict):
        raise ConfigUnreadable(f"{where}: top level is not a YAML mapping")
    return first


def literal(value: Any) -> str:
    """A one-line YAML scalar (magpie ``yamlTopLiteral``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    try:
        decoded = yaml.safe_load(text)
    except yaml.YAMLError:
        decoded = None
    if not _PLAIN.match(text) or decoded != text:
        return json.dumps(text, ensure_ascii=False)
    return text


def _block(value: Any, indent: str) -> list[str]:
    """``value`` (a mapping or a list) as block YAML lines at ``indent``."""
    dumped = yaml.safe_dump(value, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return [indent + line for line in dumped.rstrip("\n").split("\n")]


def _root(text: str, where: str):
    try:
        nodes = list(yaml.compose_all(text))
    except yaml.YAMLError as exc:
        raise ConfigUnreadable(f"{where} is not valid YAML: {exc}") from exc
    root = nodes[0] if nodes else None
    if root is None or (isinstance(root, yaml.ScalarNode) and root.tag.endswith(":null") and not root.value):
        return None
    if not isinstance(root, yaml.MappingNode):
        raise ConfigUnreadable(f"{where}: top level is not a YAML mapping")
    return root


def _entries(lines: list[str], mapping: yaml.MappingNode, limit: int, where: str):
    """``[(key, from_line, to_line, value_node)]`` of a block mapping, ``limit`` the line it ends by."""
    if mapping.flow_style:
        raise ConfigUnreadable(f"{where}: line {mapping.start_mark.line + 1} is a flow mapping")
    out = []
    pairs = mapping.value
    for index, (key, value) in enumerate(pairs):
        if not isinstance(key, yaml.ScalarNode):
            raise ConfigUnreadable(f"{where}: line {key.start_mark.line + 1} has a complex key")
        start = key.start_mark.line
        prefix = lines[start][:key.start_mark.column] if start < len(lines) else ""
        if prefix.strip():
            raise ConfigUnreadable(f"{where}: line {start + 1} has its key after other text")
        end = pairs[index + 1][0].start_mark.line if index + 1 < len(pairs) else limit
        # trailing comments and blank lines stay where they are
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        out.append((key.value, start, end, value))
    return out


def _end_line(lines: list[str], node) -> int:
    """The line after the last line holding part of ``node``."""
    end = node.end_mark.line + (1 if node.end_mark.column > 0 else 0)
    return min(max(end, node.start_mark.line + 1), len(lines))


def _set(lines: list[str], parts: list[str], value: Any, where: str) -> list[str]:
    root = _root("".join(lines), where)
    if root is None:
        body = [line for line in lines]
        while body and not body[-1].strip():
            body.pop()
        return [*body, *_new_lines(parts, value, "")]
    mapping, limit, indent = root, len(lines), " " * root.start_mark.column
    for depth, part in enumerate(parts):
        entries = _entries(lines, mapping, limit, where)
        found = next((e for e in entries if e[0] == part), None)
        last = depth == len(parts) - 1
        if found is None:
            at = entries[-1][2] if entries else mapping.start_mark.line
            child = indent if entries else indent
            return lines[:at] + _new_lines(parts[depth:], value, child) + lines[at:]
        key, start, end, node = found
        if last or not isinstance(node, yaml.MappingNode) or node.flow_style:
            replacement = _new_lines(parts[depth:depth + 1], value if last else _nest(parts[depth + 1:], value), indent)
            return lines[:start] + replacement + lines[end:]
        children = node.value
        if not children:
            return lines[:start] + _new_lines(parts[depth:], value, indent) + lines[end:]
        mapping, limit = node, end
        indent = " " * children[0][0].start_mark.column
    return lines


def _nest(parts: list[str], value: Any) -> Any:
    for part in reversed(parts):
        value = {part: value}
    return value


def _new_lines(parts: list[str], value: Any, indent: str) -> list[str]:
    value = _nest(parts[1:], value)
    key = literal(parts[0])
    if isinstance(value, (dict, list)) and value:
        return [f"{indent}{key}:\n", *[line + "\n" for line in _block(value, indent + "  ")]]
    if isinstance(value, dict):
        return [f"{indent}{key}: {{}}\n"]
    if isinstance(value, list):
        return [f"{indent}{key}: []\n"]
    return [f"{indent}{key}: {literal(value)}\n"]


def _delete(lines: list[str], parts: list[str], where: str) -> list[str]:
    root = _root("".join(lines), where)
    if root is None:
        return lines
    mapping, limit = root, len(lines)
    for depth, part in enumerate(parts):
        if not isinstance(mapping, yaml.MappingNode):
            return lines
        entries = _entries(lines, mapping, limit, where)
        found = next((e for e in entries if e[0] == part), None)
        if found is None:
            return lines
        key, start, end, node = found
        if depth == len(parts) - 1:
            out = lines[:start] + lines[end:]
            if len(entries) == 1 and depth > 0:
                # the mapping is left empty: say so, as magpie collapses it
                parent_line = mapping.start_mark.line - 1
                if 0 <= parent_line < len(out) and out[parent_line].rstrip("\r\n").endswith(":"):
                    out[parent_line] = out[parent_line].rstrip("\r\n") + " {}\n"
            return out
        mapping, limit = node, end
    return lines


def yaml_edit(text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
              where: str = "config") -> str:
    """``text`` with dotted ``sets`` and ``deletes``, everything else kept as written."""
    sets, deletes = sets or {}, deletes or []
    doc = yaml_load(text, where)
    expected = copy.deepcopy(doc)
    lines = text.replace("\r\n", "\n").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for dotted in deletes:
        lines = _delete(lines, split_path(dotted), where)
        delete_path(expected, dotted)
    for dotted, value in sets.items():
        lines = _set(lines, split_path(dotted), value, where)
        set_path(expected, dotted, value)
    out = keep_crlf("".join(lines), text)
    if yaml_load(out, where) != expected:
        raise ConfigUnreadable(f"{where} could not be edited without changing other settings")
    return out

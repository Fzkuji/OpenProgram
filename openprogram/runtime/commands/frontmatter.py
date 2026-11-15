"""Frontmatter parsing for command files.

Uses ``yaml.safe_load`` rather than the project's lite helper because
our schema includes nested dicts (``arguments``, ``requires``,
``hooks``) that the wiki-lite parser can't represent. Unknown fields
are kept in ``extras`` so forward-compatibility doesn't depend on
this file being current.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def _parse_fm(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(raw_fm)
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


# Authoritative field set; everything else lands in ``extras``.
_KNOWN: set[str] = {
    "name", "aliases", "description", "when-to-use", "hidden",
    "arguments", "argument-hint",
    "type", "context", "agent", "model", "effort", "allowed-tools",
    "paths", "requires", "hooks", "version",
    "user-invocable", "shell",
}


@dataclass
class ParsedCommand:
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    when_to_use: str = ""
    hidden: bool = False
    arguments: list[dict[str, Any]] = field(default_factory=list)
    argument_hint: str = ""
    type: str = "prompt"          # prompt | local | local-jsx
    context: str = "inline"       # inline | fork
    agent: str = "general-purpose"
    model: str = "inherit"
    effort: str = "inherit"
    allowed_tools: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    requires: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    user_invocable: bool = True
    shell: str = "inherit"
    body: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    return [v]


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "on")
    return default


def parse(text: str, default_name: str) -> ParsedCommand:
    """Parse a markdown command file. ``default_name`` is used when the
    frontmatter omits ``name`` (typically the filename stem).

    Never raises — fields that fail to parse fall back to their dataclass
    default. Use ``extras`` to retain anything we didn't recognise.
    """
    fm, body = _parse_fm(text or "")
    if not isinstance(fm, dict):
        fm = {}

    extras = {k: v for k, v in fm.items() if k not in _KNOWN}

    out = ParsedCommand(
        name=str(fm.get("name") or default_name).strip(),
        aliases=[str(x).strip() for x in _as_list(fm.get("aliases")) if str(x).strip()],
        description=str(fm.get("description") or "").strip(),
        when_to_use=str(fm.get("when-to-use") or "").strip(),
        hidden=_as_bool(fm.get("hidden"), False),
        arguments=_normalise_arguments(fm.get("arguments")),
        argument_hint=str(fm.get("argument-hint") or "").strip(),
        type=str(fm.get("type") or "prompt").strip().lower(),
        context=str(fm.get("context") or "inline").strip().lower(),
        agent=str(fm.get("agent") or "general-purpose").strip(),
        model=str(fm.get("model") or "inherit").strip(),
        effort=str(fm.get("effort") or "inherit").strip(),
        allowed_tools=[str(x).strip() for x in _as_list(fm.get("allowed-tools")) if str(x).strip()],
        paths=[str(x).strip() for x in _as_list(fm.get("paths")) if str(x).strip()],
        requires=fm.get("requires") if isinstance(fm.get("requires"), dict) else {},
        hooks=fm.get("hooks") if isinstance(fm.get("hooks"), dict) else {},
        version=str(fm.get("version") or "").strip(),
        user_invocable=_as_bool(fm.get("user-invocable"), True),
        shell=str(fm.get("shell") or "inherit").strip(),
        body=body.lstrip("\n"),
        extras=extras,
    )
    if out.type not in ("prompt", "local", "local-jsx"):
        out.type = "prompt"
    if out.context not in ("inline", "fork"):
        out.context = "inline"
    return out


def _normalise_arguments(raw: Any) -> list[dict[str, Any]]:
    """``arguments:`` may be a list of strings (just names) or a list of
    dicts (with description/required). Normalise both to dicts.
    Numeric names (``"0"``, ``"1"``) are rejected — they collide with
    the ``$0..$9`` positional substitution syntax.
    """
    items = _as_list(raw)
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if not name or name.isdigit():
                continue
            out.append({"name": name, "description": "", "required": False})
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name or name.isdigit():
                continue
            out.append({
                "name": name,
                "description": str(item.get("description") or ""),
                "required": _as_bool(item.get("required"), False),
            })
    return out

"""Surgical edits of an outside agent's own config files, after magpie's ``internal/edit``
(yetone/magpie, MIT License, Copyright (c) yetone).

One reader and one editor per format an agent keeps its settings in:

  toml_text.py   TOML (Codex, and the TOML agents)
  jsonc.py       JSON and JSONC, comments kept (Claude Code, the JSON agents)
  yaml_text.py   YAML, comments kept (Goose, Hermes, oh-my-pi …)
  dotenv.py      KEY=value files (Gemini CLI's .env)
  mark.py        the undo note over each TOML/YAML/dotenv line OpenProgram writes
  text.py        the file as text, written atomically; dotted key paths

Only the keys being set or removed change; everything else stays byte for
byte. A file that does not parse, or an edit whose result would read as
anything but the intended change, is :class:`ConfigUnreadable` and nothing is
written.
"""
from __future__ import annotations

from typing import Any

from .dotenv import dotenv_edit, dotenv_load
from .jsonc import jsonc_edit, jsonc_load, valid_jsonc
from .mark import mark_dotenv, mark_toml, mark_yaml
from .text import (ROW_MARK, ConfigText, ConfigUnreadable, SqliteRowText, delete_path, get_path, row_path, set_path,
                   split_path, target, write_atomic, write_bytes_atomic)
from .toml_text import toml_edit, toml_load, toml_value
from .yaml_text import yaml_edit, yaml_load

#: The formats an agent's config file can be in.
FORMATS = ("toml", "json", "jsonc", "yaml", "dotenv")

__all__ = [
    "FORMATS", "ConfigText", "ConfigUnreadable", "load", "edit", "get_path", "set_path",
    "delete_path", "split_path", "target", "write_atomic", "write_bytes_atomic", "toml_value", "valid_jsonc",
    "ROW_MARK", "SqliteRowText", "row_path",
]


def load(fmt: str, text: str, where: str = "config") -> dict[str, Any]:
    if fmt == "toml":
        return toml_load(text, where)
    if fmt in ("json", "jsonc"):
        return jsonc_load(text, where)
    if fmt == "yaml":
        return yaml_load(text, where)
    if fmt == "dotenv":
        return dotenv_load(text, where)
    raise ValueError(f"unknown config format {fmt!r}")


def edit(fmt: str, text: str, *, sets: dict[str, Any] | None = None, deletes: list[str] | None = None,
         where: str = "config", members: dict[str, Any] | None = None) -> str:
    """``text`` with dotted ``sets`` and ``deletes``; TOML/YAML/dotenv lines marked.
    ``members`` tells OpenProgram's entries of a shared list (TOML's
    ``[[key]]`` blocks of the user's then stay as written)."""
    if fmt == "toml":
        return mark_toml(toml_edit(text, sets=sets, deletes=deletes, where=where, members=members), text)
    if fmt in ("json", "jsonc"):
        return jsonc_edit(text, sets=sets, deletes=deletes, where=where)
    if fmt == "yaml":
        return mark_yaml(yaml_edit(text, sets=sets, deletes=deletes, where=where), text)
    if fmt == "dotenv":
        return mark_dotenv(dotenv_edit(text, sets=sets, deletes=deletes, where=where), text)
    raise ValueError(f"unknown config format {fmt!r}")

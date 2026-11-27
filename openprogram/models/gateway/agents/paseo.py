"""Paseo (``$PASEO_HOME/config.json``, default ``~/.paseo``).

After yetone/magpie ``internal/agent/paseo.go`` (MIT License, Copyright (c)
yetone). Paseo (getpaseo/paseo, an app that runs Claude Code, Codex and
others for you) starts the same Claude Code, which reads
``~/.claude/settings.json``. Paseo lists models itself, though: its own
first-party list plus the settings.json ``model`` and a few
``ANTHROPIC_*_MODEL`` tier variables, never Claude Code's model picker. So
the models the gateway serves besides the main one and the tiers were not in
Paseo's picker. Paseo takes more from its own config.json,
``agents.providers.claude.additionalModels`` (``[{id, label, description}]``),
appended to what it finds, each id passed to Claude Code as written.

So connecting writes OpenProgram's models there, marked ``via OpenProgram``
in their description, after the user's own entries, which keep their place;
the model picked is listed first. Disconnecting takes out only OpenProgram's
entries, the user's staying. Claude Code itself must be connected too for
those ids to be served.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from .base import Agent, Field, Wiring, Write

MARK = "via OpenProgram"
KEY = "agents.providers.claude.additionalModels"


def home() -> Path:
    return Path(os.environ.get("PASEO_HOME") or str(Path.home() / ".paseo")).expanduser()


def config_path() -> Path:
    return home() / "config.json"


def ours(entry: Any) -> bool:
    """An entry of additionalModels is OpenProgram's (magpie ``paseoOurs``)."""
    description = entry.get("description") if isinstance(entry, dict) else None
    return isinstance(description, str) and (description == MARK or description.endswith(" · " + MARK))


def _listed(path: Path) -> tuple[ConfigText, list[Any]]:
    """The file and its additionalModels; a list that can't be read is left as it is."""
    text = ConfigText.read(path)
    doc = edit.load("json", text.text, str(path)) if text.text.strip() else {}
    present, value = edit.get_path(doc, KEY)
    if not present or value is None:
        return text, []
    if not isinstance(value, list):
        raise ConfigUnreadable(f"{path}: {KEY} isn't a list, so OpenProgram leaves it alone")
    return text, value


def models(at: Wiring) -> list[dict[str, Any]]:
    """The gateway's models as Paseo's additionalModels entries, the picked one first."""
    rows = sorted(at.models, key=lambda row: row["id"] != at.model)
    seen: set[str] = set()
    out = []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        note = row.get("provider") or ""
        out.append({"id": row["id"], "label": row.get("name") or row["id"],
                    "description": f"{note} · {MARK}" if note else MARK})
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    _, current = _listed(path)
    user = [entry for entry in current if not ours(entry)]
    mine = {entry.get("id") for entry in user if isinstance(entry, dict)}
    # an id the user listed themselves stays theirs
    out = user + [entry for entry in models(at) if entry["id"] not in mine]
    return [Write(path, "json", sets={KEY: out}, listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    """OpenProgram's entries out, the user's staying (magpie ``paseoWrite(path, false)``)."""
    path = config_path()
    text, current = _listed(path)
    if not text.exists or not any(ours(entry) for entry in current):
        return
    left = [entry for entry in current if not ours(entry)]
    new = edit.edit("json", text.text, sets={KEY: left} if left else None, deletes=None if left else [KEY],
                    where=str(path))
    text.write(new)


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)[Pp]aseo( |$|\.)", r"Paseo\.app/"):
        return ("Paseo reads its model list when its daemon starts — restart Paseo's daemon to see "
                "OpenProgram's models. Claude Code must be connected to OpenProgram too.")
    return ""


def agent() -> Agent:
    return Agent(
        id="paseo", name="Paseo", icon="", aliases=("getpaseo",), kind="app", protocol="anthropic",
        dir=home, path=config_path, fields=[Field("model", "Model")], wire=_wire, unwire=_unwire,
        notice=_notice, loose=(KEY,), detect=lambda: home().is_dir(),
    )

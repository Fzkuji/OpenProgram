"""Retire the per-agent workspace persona files.

Agents used to carry ``<state>/agents/<id>/workspace/`` with AGENTS.md,
SOUL.md, USER.md and TOOLS.md seeded from templates. Instructions now come
from AGENTS.md files layered by directory (``openprogram.runtime.context.agents_md``)
and an agent's own instructions are its ``system_prompt`` setting, so these
files are no longer read.

:func:`retire` runs once per agent at worker startup:

- a file still equal to the template it was seeded with is deleted;
- a file someone edited is appended to the agent's ``system_prompt`` under a
  heading naming it, then moved to ``workspace/.retired/`` so nothing is lost;
- the ``workspace`` directory is removed when that leaves it empty.
"""
from __future__ import annotations

import logging
from pathlib import Path

from openprogram.runtime.agent_registry import manager as _agents
from openprogram.host._platform.filesystem import replace_file

log = logging.getLogger(__name__)

AGENTS_FILE = "AGENTS.md"
SOUL_FILE = "SOUL.md"
USER_FILE = "USER.md"
TOOLS_FILE = "TOOLS.md"
FILES = (AGENTS_FILE, SOUL_FILE, USER_FILE, TOOLS_FILE)

# The templates agents were seeded with; a file equal to one is untouched.
_SEEDS: dict[str, tuple[str, ...]] = {
    AGENTS_FILE: ("""# {name}

## Operating rules
- Answer concisely by default. Expand only when asked or when the
  answer needs justification.
- When the user gives a task, do the task — don't ask clarifying
  questions unless the input is genuinely ambiguous.
- When you change a file or run a command, say what you did and why
  in one sentence, not a narrative.

## Tools
- All tools are read-only unless told otherwise.
- Ask before taking destructive actions (delete, force-push, send
  external messages).

Edit this file to change how the agent behaves.
""",),
    SOUL_FILE: ("""# {name} — persona

Tone: direct, technical, no filler. Plain prose. No headers unless
the content actually branches.

Boundaries: if you don't know something, say so. Never invent APIs,
file paths, or IDs.

Edit this file to give {name} its personality.
""",),
    USER_FILE: ("""# About the user

What agents learn about you from conversations is kept in Memory and
shown to every agent, so this file is not needed for that. Write here
only what you want to state yourself for this agent.
""", """# About the user

(Fill in anything the agent should remember about you — your name,
your projects, how you prefer to be addressed, time zone, etc.)
"""),
}


def workspace_path(agent_id: str) -> Path:
    return _agents.agent_dir(agent_id) / "workspace"


def is_seed(agent_id: str, filename: str, text: str) -> bool:
    """Whether ``text`` is still the template this file was seeded with."""
    if not text.strip():
        return True
    spec = _agents.get(agent_id)
    names = {agent_id, spec.name if spec else agent_id}
    # The agent may have been renamed since: the seed carries the old name.
    first = text.lstrip().splitlines()[0]
    if first.startswith("# "):
        names.add(first[2:].removesuffix(" — persona").strip())
    wanted = " ".join(text.split())
    return any(
        " ".join(template.format(name=name).split()) == wanted
        for template in _SEEDS.get(filename, ()) for name in names
    )


def retire(agent_id: str) -> list[str]:
    """Fold edited persona files into ``system_prompt``; returns their names."""
    ws = workspace_path(agent_id)
    if not ws.is_dir():
        return []
    kept: list[tuple[str, str]] = []
    for filename in FILES:
        path = ws / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if is_seed(agent_id, filename, text):
            path.unlink()
        else:
            kept.append((filename, text.strip()))
    if kept:
        spec = _agents.get(agent_id)
        if spec is not None:
            parts = [spec.system_prompt.strip()] if spec.system_prompt.strip() else []
            parts += [f"## From workspace {name}\n\n{text}" for name, text in kept]
            _agents.update(agent_id, {"system_prompt": "\n\n".join(parts)})
        retired = ws / ".retired"
        retired.mkdir(exist_ok=True)
        for name, _ in kept:
            replace_file(ws / name, retired / name)
        log.info("agent %s: moved %s into system_prompt", agent_id, ", ".join(n for n, _ in kept))
    try:
        ws.rmdir()
    except OSError:
        pass
    return [name for name, _ in kept]


def retire_all() -> None:
    for spec in _agents.list_all():
        try:
            retire(spec.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent %s: retiring workspace files failed: %s", spec.id, exc)

"""Per-agent workspace directory.

Mirrors OpenClaw's agent-workspace concept (see their concepts docs):
each agent has a home folder on disk where it keeps operating
notes, persona files, and any user-created content it references.

Layout:

    <state>/agents/<agent_id>/workspace/
        AGENTS.md     # how the agent should behave (rules, priorities)
        SOUL.md       # persona, tone, boundaries
        USER.md       # who the user is
        TOOLS.md      # local tool notes (optional)

On agent creation we seed the three required files with minimal
placeholders so the user can edit them to taste later. The
``read_*`` helpers are used by the context engine to build the
system-prompt prefix every turn.

We do NOT sandbox — the workspace is just the default cwd and a
well-known location for the files above. Tool sandboxing is a
separate feature we haven't ported yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from openprogram.agents import manager as _agents


# ---------------------------------------------------------------------------
# File map
# ---------------------------------------------------------------------------

AGENTS_FILE = "AGENTS.md"
SOUL_FILE = "SOUL.md"
USER_FILE = "USER.md"
TOOLS_FILE = "TOOLS.md"


_DEFAULT_AGENTS_MD = """# {name}

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
"""

_DEFAULT_SOUL_MD = """# {name} — persona

Tone: direct, technical, no filler. Plain prose. No headers unless
the content actually branches.

Boundaries: if you don't know something, say so. Never invent APIs,
file paths, or IDs.

Edit this file to give {name} its personality.
"""

_DEFAULT_USER_MD = """# About the user

(Fill in anything the agent should remember about you — your name,
your projects, how you prefer to be addressed, time zone, etc.)
"""


_SEED: dict[str, str] = {
    AGENTS_FILE: _DEFAULT_AGENTS_MD,
    SOUL_FILE:   _DEFAULT_SOUL_MD,
    USER_FILE:   _DEFAULT_USER_MD,
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def workspace_path(agent_id: str) -> Path:
    """Return the workspace dir. Does NOT create it — use
    :func:`bootstrap` when you want the seed files to exist.
    """
    return _agents.agent_dir(agent_id) / "workspace"


def bootstrap(agent_id: str) -> Path:
    """Create the workspace dir and seed the persona files if they
    don't exist yet. Idempotent — safe to call on every startup.
    Returns the workspace path.
    """
    spec = _agents.get(agent_id)
    name = spec.name if spec else agent_id
    ws = workspace_path(agent_id)
    ws.mkdir(parents=True, exist_ok=True)
    for filename, template in _SEED.items():
        path = ws / filename
        if not path.exists():
            path.write_text(template.format(name=name), encoding="utf-8")
    return ws


def read_file(agent_id: str, name: str) -> Optional[str]:
    """Read a workspace file by name. Returns None if it doesn't
    exist, the empty string if it exists but is empty (we treat
    those differently in the prompt composer).
    """
    path = workspace_path(agent_id) / name
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_agents_md(agent_id: str) -> Optional[str]:
    return read_file(agent_id, AGENTS_FILE)


def read_soul_md(agent_id: str) -> Optional[str]:
    return read_file(agent_id, SOUL_FILE)


def read_user_md(agent_id: str) -> Optional[str]:
    return read_file(agent_id, USER_FILE)


def read_tools_md(agent_id: str) -> Optional[str]:
    return read_file(agent_id, TOOLS_FILE)

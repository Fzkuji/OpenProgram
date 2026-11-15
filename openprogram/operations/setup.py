"""Which tools and skills the user switched off.

The tool registry, the file-tree route and the CLI banner read these. Both
answers combine the user config (``openprogram.state.config``) with what the
program and agent registries know, so they live above ``state``.
"""

from __future__ import annotations

from openprogram.state import config as user_config


def read_disabled_tools() -> set[str]:
    """Public helper consumed by openprogram.programs to filter list_available.

    Kept in this module so the tools package doesn't import config from
    deeper webui modules and drag in FastAPI at tool-registry import time.

    Also honours ``memory.backend == "none"`` by hiding every memory tool,
    since they have no backing store in that mode. Which tools those are
    is read from the bundle that registers them, so a renamed tool cannot
    survive the switch by no longer matching a name written down here.
    """
    cfg = user_config.read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        from openprogram.programs.tools.knowledge.memory import MEMORY_TOOL_NAMES

        disabled.update(MEMORY_TOOL_NAMES)
    return disabled


def read_disabled_skills() -> set[str]:
    """Skills the default agent opts out of.

    In the multi-agent model, skill enablement is per-agent. Callers
    that still think in global terms (the CLI chat banner, for
    example) read the default agent's list here.
    """
    from openprogram.runtime.agent_registry import manager as _agents

    agent = _agents.get_default()
    if agent is None:
        return set()
    return set((agent.skills or {}).get("disabled") or [])

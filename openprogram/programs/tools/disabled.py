"""Which tools the user switched off.

The tool registry (``openprogram.programs.agent_tools``) and the file-tree
route read this. The answer combines the user config
(``openprogram.state.config``) with the names of the tool bundles that
register the tools, so it lives with them.
"""

from __future__ import annotations

from openprogram.state import config as user_config


def read_disabled_tools() -> set[str]:
    """The tools hidden from every toolset: ``tools.disabled`` in the config.

    Also honours ``memory.backend == "none"`` by hiding every memory tool,
    since they have no backing store in that mode. Which tools those are
    is ``state.memory.tool_names``, the names the memory bundle registers.
    """
    cfg = user_config.read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        from openprogram.state.memory.tool_names import MEMORY_TOOL_NAMES

        disabled.update(MEMORY_TOOL_NAMES)
    return disabled


__all__ = ["read_disabled_tools"]

"""Which skills the user switched off.

The CLI banner reads this; it combines the agent registry with the
default agent's settings. Which tools are switched off is answered by
``openprogram.programs.tools.disabled``, beside the tool bundles whose
names it needs.
"""

from __future__ import annotations


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

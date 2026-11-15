"""What the conversation package adds to the context the model sees.

``openprogram.runtime.context`` sits below conversations and never imports
them. On import, this package registers the pieces only it can supply:

* the ``open_work`` system-prompt section (L1, order 35): what the agent
  still owes the user in the session bound to the current task;
* how a context breakdown loads an agent's saved profile.
"""
from __future__ import annotations

import logging
from typing import Any

from openprogram.runtime.context import session_stats
from openprogram.runtime.context.components import ContextComponent, register

_log = logging.getLogger(__name__)


def _build_open_work(agent: Any) -> str:
    """What the agent still owes the user in this conversation."""
    try:
        from openprogram.runtime.conversation.open_work import prompt_block
        from openprogram.runtime.execution.run_control import get_current_session_id
        return prompt_block(get_current_session_id())
    except Exception:
        _log.debug("open work unavailable", exc_info=True)
        return ""


def _load_agent_profile(agent_id: str) -> dict:
    from openprogram.runtime.conversation import model_tools
    return model_tools.load_agent_profile(agent_id)


register(ContextComponent("open_work", "L1", 35, _build_open_work))
session_stats.set_agent_profile_loader(_load_agent_profile)

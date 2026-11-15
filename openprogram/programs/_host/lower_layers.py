"""What Programs hand down to models and state when they load.

``openprogram.models`` and ``openprogram.state`` sit below Programs and
never import them. The facts and behaviours they need from Programs arrive
through ports they own, filled in here:

* ``state.program_facts`` — the registered web-search backends and Agent
  tools (settings), the Goal tool's marks on transcript messages (the
  session graph);
* ``models.usage.goal_port`` — the Goal tool's lock, load, publish and
  usage refresh, which the usage ledger runs around a provider request.

Each provider imports its Program module when it is called, never here,
so loading this module stays cheap and a test that replaces a Goal
function (``monkeypatch.setattr(goal, "load_goal", ...)`` on the package,
``chat.refresh_usage``…) is what the lower layers see.
"""
from __future__ import annotations

from openprogram.models.usage import goal_port
from openprogram.state import program_facts


def _web_search_provider_names() -> list[str]:
    from openprogram.programs.tools.web.web_search.registry import registry
    import openprogram.programs.tools.web.web_search.providers  # noqa: F401

    return [getattr(provider, "name", "") for provider in registry.all()]


def _agent_tool_names() -> list[str]:
    from openprogram.programs import list_registered_agent_tools

    return list_registered_agent_tools()


def _annotate_messages(session_id: str, messages: list[dict]) -> list[dict]:
    from openprogram.programs.tools.planning.goal.presentation import annotate_messages

    return annotate_messages(session_id, messages)


def _goal_locked(session_id: str):
    from openprogram.programs.tools.planning.goal import chat

    return chat.locked(session_id)


def _goal_publish(session_id: str, goal: dict) -> None:
    from openprogram.programs.tools.planning.goal import chat

    chat.publish(session_id, goal)


def _goal_refresh_usage(session_id: str) -> None:
    from openprogram.programs.tools.planning.goal import chat

    chat.refresh_usage(session_id)


def _goal_load(session_id: str):
    import openprogram.programs.tools.planning.goal as goals

    return goals.load_goal(session_id)


def _goal_accumulate_usage(session_id: str, goal: dict) -> None:
    import openprogram.programs.tools.planning.goal as goals

    goals.accumulate_goal_usage(session_id, goal)


def register() -> None:
    program_facts.register(
        web_search_provider_names=_web_search_provider_names,
        agent_tool_names=_agent_tool_names,
        annotate_messages=_annotate_messages,
    )
    goal_port.register(
        locked=_goal_locked,
        publish=_goal_publish,
        refresh_usage=_goal_refresh_usage,
        load_goal=_goal_load,
        accumulate_goal_usage=_goal_accumulate_usage,
    )


__all__ = ["register"]

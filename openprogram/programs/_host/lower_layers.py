"""What Programs hand down to the layers below them when they load.

``openprogram.models``, ``openprogram.state``, ``openprogram.host`` and
``openprogram.runtime`` sit below Programs and never import them. The facts and behaviours they need from Programs arrive
through ports they own, filled in here:

* ``state.program_facts`` — the registered web-search backends and Agent
  tools (settings), the Goal tool's marks on transcript messages (the
  session graph);
* ``models.usage.goal_port`` — the Goal tool's lock, load, publish and
  usage refresh, which the usage ledger runs around a provider request;
* ``host.machines.kinds`` — the connection methods that live with
  ``computer_use`` (``osworld``; its providers register it);
* ``runtime.program_host`` — the Program host itself: the tool registry
  and its policy chain, the call wrappers, the shared services;
* ``host.runtime_facts`` — the tool names the turn's policy allows;
  ``host.system_access`` — the operating-system access a ``computer_use``
  call needs, which its target's provider answers;
* the runtime's moments a Program joins: the agent loop
  (``runtime.agent.loop.hooks`` — the tool guides, the task tools'
  ``task_progress``), a compaction's restored state (the open task), a
  prompt's capabilities (what ``computer_use`` can operate) and the shared
  slash commands (``/goal``).

Inside the Programs, the report helpers (``_authoring.reports.tool_port``)
get the tools they work through here too: ``read_conversation``,
``list_agents``, the checked ``write``, the tool policy chain and macOS's
running applications.

Importing ``openprogram.extensions`` here fills the runtime's ports for
skills and the plugin and skill commands (``extensions.lower_layers``), so
loading the Programs loads them.
* ``runtime.maintenance`` — self-update's maintenance gate
  (``operations.self_update.runtime_gate``), so every process that runs
  turns and Jobs has it once Programs load; the worker registers it too.

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


def _load_connection_methods() -> None:
    import openprogram.programs.tools.computer_use.computer_use.providers  # noqa: F401


# -- the runtime's ports and moments ---------------------------------------


def _allowed_tool_names():
    from openprogram.programs._host.runtime import _allowed_tool_names

    return _allowed_tool_names.get()


def _system_access_operation(tool_name: str, args: dict):
    if tool_name != "computer_use":
        return None
    from openprogram.programs.tools.computer_use.computer_use import tool as screen

    return screen.system_access(args)


def _guides_begin():
    from openprogram.programs._authoring import guides

    return guides.begin_loop()


def _guides_end(token) -> None:
    from openprogram.programs._authoring import guides

    guides.end_loop(token)


def _guides_observe(messages) -> None:
    from openprogram.programs._authoring import guides

    guides.observe_request(messages)


def _guides_attach(tool_name: str, args, result):
    from openprogram.programs._authoring import guides

    return guides.attach(tool_name, args, result)


def _progress_annotate(provider_tools: list, offered_tools) -> list:
    from openprogram.programs.tools.planning.todo import progress

    return progress.annotate(provider_tools, offered_tools)


def _progress_take(tool, args, offered_tools):
    from openprogram.programs.tools.planning.todo import progress

    return progress.take(tool, args, offered_tools)


def _progress_apply(value, occurrence_id: str):
    from openprogram.programs.tools.planning.todo import progress

    return progress.apply(value, occurrence_id)


def _open_task_lines(session_id: str) -> list[str]:
    from openprogram.programs.tools.planning.todo import shared

    return shared.open_task_lines(session_id)


def _devices_text() -> str:
    from openprogram.programs.tools.computer_use.computer_use.devices import devices_text

    return devices_text()


def _goal_command(session_ctx: dict, raw_args: str):
    from openprogram.programs.tools.planning.goal import goal_builtin_handler

    return goal_builtin_handler(session_ctx, raw_args)


def _register_runtime_moments() -> None:
    from openprogram.host import runtime_facts, system_access
    from openprogram.runtime.agent.loop import hooks as loop_hooks
    from openprogram.runtime.commands import registry as commands
    from openprogram.runtime.context.compaction import restore
    from openprogram.runtime.context.prompt_assembly import components

    runtime_facts.register(allowed_tool_names=_allowed_tool_names)
    system_access.register_operation_source(_system_access_operation)
    loop_hooks.register(
        loop_scope=_guides_scope(),
        request_observer=_guides_observe,
        result_attachment=_guides_attach,
        call_argument=_task_progress_argument(),
    )
    restore.register_section("Open task", _open_task_lines)
    components.register_tool_section("computer_use", _devices_text)
    commands.register_shared_builtin(
        "goal",
        handler=_goal_command,
        description=(
            "run the Goal Workflow with current session context "
            "(or inspect/clear its state)"),
        argument_hint="[prompt | clear]",
    )
    import openprogram.extensions  # noqa: F401 -- fills the runtime's ports for skills and commands


def _task_progress_argument():
    from openprogram.runtime.agent.loop.hooks import CallArgument

    # The key is the task tools' (``todo.progress.KEY``), spelled here so
    # registering does not import them.
    return CallArgument(key="task_progress", annotate=_progress_annotate,
                        take=_progress_take, apply=_progress_apply)


def _guides_scope():
    from openprogram.runtime.agent.loop.hooks import LoopScope

    return LoopScope(begin=_guides_begin, end=_guides_end)



def _report_apply_tool_policy(tools, **policy):
    from openprogram.programs import apply_tool_policy

    return apply_tool_policy(tools, **policy)


def _report_read_conversation():
    from openprogram.programs.tools.knowledge.read_conversation import read_conversation

    return read_conversation


def _report_conversation_session():
    from openprogram.programs.tools.knowledge.read_conversation import _current_session

    return _current_session()


def _report_list_agents():
    from openprogram.programs.tools.agents.send_message.list_agents import list_agents

    return list_agents


def _report_write_file(path: str, content: str):
    from openprogram.programs.tools.files.write import execute

    return execute(path, content)


def _report_running_applications(appkit):
    from openprogram.programs.tools.computer_use.computer_use.desktop.macos.processes import (
        running_applications,
    )

    return running_applications(appkit)

class _ProgramHost:
    """The Program host the runtime reaches through ``runtime.program_host``.

    Each name resolves to its Program module when it is read, never here,
    so a test that replaces a registry function is what the runtime sees.
    """

    MEMBERS: dict[str, tuple[str, str | None]] = {
        "get": ("openprogram.programs._host.runtime", "get"),
        "get_agent_tool": ("openprogram.programs", "get_agent_tool"),
        "as_resident": ("openprogram.programs._host.runtime", "as_resident"),
        "unavailable_tool": ("openprogram.programs._host.runtime", "unavailable_tool"),
        "agent_tools": ("openprogram.programs", "agent_tools"),
        "apply_tool_policy": ("openprogram.programs", "apply_tool_policy"),
        "unsafe_in_for": ("openprogram.programs", "_unsafe_in_for"),
        "split_tools_for_dispatch": ("openprogram.programs", "split_tools_for_dispatch"),
        "loaded_tools": ("openprogram.programs", "loaded_tools"),
        "note_request_loaded": ("openprogram.programs", "note_request_loaded"),
        "install_turn_tools": ("openprogram.programs", "install_turn_tools"),
        "deferred_catalog": ("openprogram.programs", "deferred_catalog"),
        "deferred_catalog_text": ("openprogram.programs", "deferred_catalog_text"),
        "catalog_note": ("openprogram.programs", "catalog_note"),
        "DEFAULT_TOOLS": ("openprogram.programs", "DEFAULT_TOOLS"),
        "is_verifier_read_tool": ("openprogram.programs", "_is_verifier_read_tool"),
        "allowed_tool_names": ("openprogram.programs._host.runtime", "_allowed_tool_names"),
        "build_and_register_tool": ("openprogram.programs._host.runtime", "_build_and_register_tool"),
        "schema_hint": ("openprogram.programs._host.runtime", "schema_hint"),
        "call_problem": ("openprogram.programs._authoring.prechecks", "call_problem"),
        "CallProblem": ("openprogram.programs._authoring.prechecks", "CallProblem"),
        "services": ("openprogram.programs._host.services", None),
        "open_application_ids": ("openprogram.programs._host.ui.bindings", "open_application_ids"),
        "load_functions_meta": ("openprogram.programs._host.meta_storage", "load_functions_meta"),
        "original_owner": ("openprogram.programs._authoring.reports.sources", "original_owner"),
        # Extensions (MCP tools, plugins).
        "unregister": ("openprogram.programs._host.runtime", "unregister"),
        "all_tools": ("openprogram.programs._host.runtime", "all_tools"),
        "tool_owner": ("openprogram.programs._host.runtime", "tool_owner"),
        "tool_tier": ("openprogram.programs._host.runtime", "tool_tier"),
        "ProgramContext": ("openprogram.programs._host.context", "ProgramContext"),
        # The conversation runtime.
        "install_open_applications": ("openprogram.programs._host.runtime", "install_open_applications"),
        "reset_open_applications": ("openprogram.programs._host.runtime", "reset_open_applications"),
    }

    def __getattr__(self, name: str):
        try:
            module_name, attribute = self.MEMBERS[name]
        except KeyError:
            raise AttributeError(name) from None
        import importlib

        module = importlib.import_module(module_name)
        return module if attribute is None else getattr(module, attribute)


def register() -> None:
    from openprogram.host.machines import kinds as machine_kinds
    from openprogram.programs._authoring.reports import tool_port as report_tools
    from openprogram.operations.self_update import runtime_gate
    from openprogram.runtime import program_host

    program_host.register(_ProgramHost(), _ProgramHost.MEMBERS)
    report_tools.register(
        apply_tool_policy=_report_apply_tool_policy,
        read_conversation=_report_read_conversation,
        conversation_session=_report_conversation_session,
        list_agents=_report_list_agents,
        write_file=_report_write_file,
        running_applications=_report_running_applications,
    )
    _register_runtime_moments()
    runtime_gate.register()

    if _load_connection_methods not in machine_kinds.loaders:
        machine_kinds.loaders.append(_load_connection_methods)
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

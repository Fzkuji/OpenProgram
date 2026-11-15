"""Public entries for configured Agents and task-local Context.

Imports are lazy. Importing openprogram does not create a Runtime, session,
provider, tool registration, or user interface.
"""

__all__ = ["Runtime", "decision", "Session", "Agent", "ChatAgent", "TurnRequest", "TurnResult", "Context", "agent", "agent_async"]

_LAZY = {
    "ChatAgent": ("openprogram.runtime.conversation.chat", "ChatAgent"),
    "TurnRequest": ("openprogram.runtime.agent.turn_request", "TurnRequest"),
    "TurnResult": ("openprogram.runtime.conversation.turn_runtime.types", "TurnResult"),
    "Agent": ("openprogram.runtime.agent.agent_class", "Agent"),
    "Context": ("openprogram.runtime.context", "Context"),
    "agent": ("openprogram.runtime.agent.agent_function", "agent"),
    "agent_async": ("openprogram.runtime.agent.agent_function", "agent_async"),
    "Runtime": ("openprogram.runtime.agent.runtime", "Runtime"),
    "Session": ("openprogram.runtime.agent.follow_up_session", "Session"),
    "decision": ("openprogram.runtime.agent.decision", None),
}


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module_path, attr = target
    module = importlib.import_module(module_path)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value  # cache: later lookups skip __getattr__
    return value


def __dir__():
    return sorted(list(globals()) + __all__)

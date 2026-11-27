"""Public entries for configured Agents and task-local Context.

Imports are lazy. Importing openprogram does not create a Runtime, session,
provider, tool registration, or user interface.

This package is also the composition root of the layers (see
``tests/contracts/repository/test_layer_order.py``): every layer below
Programs that needs one reaches it through a port, and the port asks
``host.runtime_facts.load_programs`` to load the Programs layer, which is
installed here and nowhere else. The host and models layers fill the ports
of the layers below them here too (``host.lower_layers``,
``models.lower_layers``).
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


def _load_programs() -> None:
    """Load the Programs layer once; a caller without it degrades."""
    import importlib
    import logging

    try:
        importlib.import_module("openprogram.programs")
    except Exception as exc:  # noqa: BLE001 — a caller without Programs degrades
        logging.getLogger(__name__).warning("Programs did not load: %s", exc)


def _install_layers() -> None:
    from openprogram.host import lower_layers as host_lower_layers
    from openprogram.host import runtime_facts
    from openprogram.models import lower_layers as models_lower_layers

    runtime_facts.register(load_programs=_load_programs)
    # The host and models layers fill the ports of the layers below them
    # here, before any module of those layers runs, so a process that only
    # reads a setting or a project still has their checks and facts.
    host_lower_layers.register()
    models_lower_layers.register()


_install_layers()


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

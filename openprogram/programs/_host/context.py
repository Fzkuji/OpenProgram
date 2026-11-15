"""The context every Program and plugin registers through.

An application package (built-in, installed, a user package, an external
harness) and a plugin each get one :class:`ProgramContext`. Every
registration made through it records how to undo itself. Unloading,
disabling or reloading runs those disposers newest first, so the host
returns to exactly the state it had before the package loaded: its tools
leave the registry, its event handlers unsubscribe, its prompt sections,
services, resource providers, MCP servers and provider roots go away.

An application's ``setup`` writes::

    def setup(ctx):
        @ctx.tool
        def word_count(text: str) -> int:
            '''Count the words in a text.'''
            return len(text.split())

        ctx.on("tool.after", lambda event: ctx.log.info(event.payload["tool"]))
        ctx.context("house-rules", lambda agent: "Answer in British English.")

A plugin runs no code: the plugin loader mounts its manifest contents
(skills, commands, hooks, MCP servers, provider directories) through the
same methods.

The registrations reuse the host's own registries — the tool registry
(``PROGRAM_ENTRIES`` rules), the event bus, the system-prompt components,
the skill roots, the MCP registry and the provider directories — so a
registered tool or prompt section behaves exactly like a built-in one.
An entry registered through an application's context starts deferred
(``Tier.DEFERRED``).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import types
from pathlib import Path
from typing import Any, Callable, Optional

from openprogram.runtime.events import Event, ToolGateDenied, get_event_bus
from openprogram.runtime.events.registry import EVENTS

#: The worker's event loop, set at startup; MCP servers start on it.
_host_loop: Optional[asyncio.AbstractEventLoop] = None


def set_host_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _host_loop
    _host_loop = loop


def _schedule(coro_factory: Callable[[], Any]) -> bool:
    """Run a coroutine on the worker loop without waiting for it."""
    loop = _host_loop
    if loop is None or loop.is_closed():
        return False
    asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    return True


class ProgramContext:
    """Registrations of one loaded Program or plugin, each paired with its undo.

    ``owner`` is the tool owner recorded on every tool it registers; a
    plugin's is ``plugin:<name>``.
    """

    def __init__(self, name: str, root: str | Path = "", options: Optional[dict] = None,
                 *, owner: Optional[str] = None):
        self.name = name
        self.root = Path(root) if root else Path.cwd()
        self.options: dict[str, Any] = dict(options or {})
        self.owner = owner or f"plugin:{name}"
        kind = "plugin" if owner is None else "program"
        self.log = logging.getLogger(f"openprogram.{kind}.{name}")
        self._disposers: list[tuple[str, Callable[[], Any]]] = []
        #: What this context added, by kind, for listings and inspection.
        self.contributions: dict[str, list[str]] = {}
        #: Slash commands, read by the command registry's plugin adapter.
        self.commands: list[dict[str, str]] = []
        self.disposed = False

    # bookkeeping

    def effect(self, dispose: Callable[[], Any], label: str = "effect") -> Callable[[], Any]:
        """Register an undo for something the package did by itself."""
        if not callable(dispose):
            raise TypeError("effect() takes the function that undoes the change")
        self._disposers.append((label, dispose))
        return dispose

    def _record(self, kind: str, name: str) -> None:
        self.contributions.setdefault(kind, []).append(name)

    def path(self, relative: str | Path) -> Path:
        """A path inside the package, refused when it leaves its root."""
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValueError(f"{relative} is outside the plugin directory")
        return target

    def dispose(self) -> list[str]:
        """Undo every registration, newest first. Returns the errors."""
        errors: list[str] = []
        while self._disposers:
            label, fn = self._disposers.pop()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — undo the rest regardless
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
                self.log.warning("undo %s failed: %s", label, exc)
        self.contributions.clear()
        self.commands.clear()
        self.disposed = True
        return errors

    # tools

    def tool(self, fn: Optional[Callable] = None, /, **options: Any):
        """Register a function as an Agent tool (decorator or call).

        The function follows the ``PROGRAM_ENTRIES`` rules: a docstring is
        the tool description and every parameter has a type annotation.
        ``options`` are the ``ENTRY_OPTIONS`` keys (``name``,
        ``requires_approval``, ``run_in_worker`` …).
        """
        def register(func: Callable) -> Callable:
            func_name = getattr(inspect.unwrap(func), "__name__", "tool")
            module = types.ModuleType(f"openprogram_plugin_tools.{self.name}.{func_name}")
            module.PROGRAM_ENTRIES = [func]
            module.ENTRY_OPTIONS = {func_name: dict(options)} if options else {}
            self.tools_from(module)
            return module.PROGRAM_ENTRIES[0]

        return register(fn) if fn is not None else register

    def tools_from(self, module: Any) -> list[str]:
        """Register a module's ``PROGRAM_ENTRIES`` as tools."""
        from openprogram.programs._host.packages import register_entries
        names = register_entries(module, owner=self.owner)
        self._adopt_tools(names)
        return names

    def _adopt_tools(self, names: list[str]) -> None:
        """Own tools that are already registered: dispose unregisters them.

        A name this context already owns is skipped, so loading the same
        package again does not record a second undo.
        """
        from openprogram.programs._host.runtime import (
            Tier, application_of, get, owner_kind, set_tier, tool_tier, unregister,
        )
        owned = set(self.contributions.get("tools", ()))
        for tool_name in names:
            tool = get(tool_name)
            if tool is not None:
                tool._program_owner = self.owner
                # An application's entries start deferred (program-model §5).
                if owner_kind(self.owner) == "application" and tool_tier(tool) is not Tier.HIDDEN:
                    set_tier(tool, Tier.DEFERRED)
            if tool_name in owned:
                continue
            owned.add(tool_name)
            self._record("tools", tool_name)
            reason = (f"its application '{application_of(self.owner)}' was closed"
                      if application_of(self.owner) else f"'{self.owner}' was unloaded")
            self.effect(lambda n=tool_name, why=reason: unregister(n, reason=why), f"tool {tool_name}")

    # events

    def _spec(self, event_type: str, kind: str) -> None:
        spec = EVENTS.get(event_type)
        if spec is None:
            raise ValueError(f"unknown event {event_type!r}; see the plugin tool's inspect action")
        if spec.kind != kind:
            method = {"notify": "on", "gate": "gate", "transform": "transform"}[spec.kind]
            raise ValueError(f"{event_type} is a {spec.kind} event; use ctx.{method}()")

    def on(self, event_type: str, handler: Callable[[Event], Any]) -> Callable[[], None]:
        """Observe a notify event. The handler's return value is ignored."""
        self._spec(event_type, "notify")

        def observe(event: Event) -> None:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 — a plugin never breaks the emitter
                self.log.warning("handler for %s raised %s: %s", event_type, type(exc).__name__, exc)

        dispose = get_event_bus().subscribe(observe, types={event_type})
        self._record("events", event_type)
        return self.effect(dispose, f"on {event_type}")

    def gate(self, event_type: str, handler: Callable[[Event], Any]) -> Callable[[], None]:
        """Take part in a gate event: return a reason to deny, else ``None``."""
        self._spec(event_type, "gate")

        def decide(event: Event) -> Optional[str]:
            try:
                verdict = handler(event)
            except ToolGateDenied as exc:
                return str(exc) or f"denied by plugin {self.name}"
            except Exception as exc:  # noqa: BLE001 — fail open, like every gate
                self.log.warning("gate for %s raised %s: %s", event_type, type(exc).__name__, exc)
                return None
            return str(verdict) if verdict else None

        dispose = get_event_bus().subscribe_gate(event_type, decide)
        self._record("gates", event_type)
        return self.effect(dispose, f"gate {event_type}")

    def transform(self, event_type: str, rewrite: Callable[[Event, Any], Any]) -> Callable[[], None]:
        """Rewrite a transform event's value: return the new value or ``None``."""
        self._spec(event_type, "transform")
        dispose = get_event_bus().subscribe_transform(event_type, rewrite)
        self._record("transforms", event_type)
        return self.effect(dispose, f"transform {event_type}")

    # prompt

    def context(self, name: str, build: Callable[[Any], Optional[str]], *,
                layer: str = "L1", order: int = 500,
                condition: Optional[Callable[[Any], bool]] = None) -> Callable[[], None]:
        """Add a system-prompt section. ``build(agent)`` returns its text.

        ``layer`` is ``L0`` (constant), ``L1`` (session) or ``L2`` (this
        call); ``order`` sorts it within the layer.
        """
        from openprogram.runtime.context import components
        if layer not in ("L0", "L1", "L2"):
            raise ValueError("layer must be L0, L1 or L2")
        full = f"{self.owner}:{name}"
        components.register(components.ContextComponent(
            name=full, layer=layer, order=order, build=build,
            condition=condition or (lambda agent: True),
        ))
        self._record("context", name)
        return self.effect(lambda: components.unregister(full, layer), f"context {name}")

    # services

    def provide(self, service: str, value: Any) -> Callable[[], None]:
        """Offer ``value`` to other plugins as ``ctx.service(service)``."""
        from . import services
        dispose = services.provide(self.owner, service, value)
        self._record("services", service)
        return self.effect(dispose, f"service {service}")

    def service(self, name: str) -> Any:
        """A host or plugin service by name (``tools``, ``events`` …)."""
        from . import services
        return services.get(name)

    # resources

    def resource(self, provider: Any) -> Callable[[], None]:
        """Register a resource provider (``resource`` tool); dispose removes it.

        An application's own provider is named ``application.<id>``; that
        namespace is reserved to the application with that id.
        """
        from openprogram.host.resources import registry
        registry.register(provider, owner=self.owner)
        self._record("resources", provider.name)
        return self.effect(lambda: registry.unregister(provider.name), f"resource {provider.name}")

    # commands, skills

    def command(self, name: str, prompt: str, description: str = "") -> None:
        """Add a slash command whose body is ``prompt``."""
        self.commands.append({"name": name, "description": description, "prompt": prompt})
        self._record("commands", name)

    def skills(self, directory: str | Path) -> Callable[[], None]:
        """Add a directory of ``SKILL.md`` skills."""
        from openprogram.extensions.skills.loader import register_plugin_skills, unregister_plugin_skills
        register_plugin_skills(self.name, self.path(directory))
        self._record("skills", str(directory))
        return self.effect(lambda: unregister_plugin_skills(self.name), "skills")

    # MCP, providers

    def mcp_server(self, name: str, config: dict[str, Any]) -> Callable[[], None]:
        """Start an MCP server (``command``/``args``/``env`` or ``url``).

        It runs as ``<plugin>_<name>`` beside the servers in
        ``mcp_servers.json`` and is not written there: it lives exactly as
        long as the plugin is enabled.
        """
        from openprogram.extensions.mcp import registry as mcp_registry
        from openprogram.extensions.mcp.config import parse_entry
        server = f"{self.name}_{name}".replace(".", "_")
        cfg = parse_entry(server, normalize_mcp_entry(config))
        if cfg is None:
            raise ValueError(f"MCP server {name!r} has no command or url")
        if not _schedule(lambda: mcp_registry.add_server(cfg)):
            self.log.info("MCP server %s starts when the worker runs this plugin", server)
        self._record("mcp", server)
        return self.effect(lambda: _schedule(lambda: mcp_registry.remove_server(server)),
                           f"mcp {server}")

    def provider_root(self, directory: str | Path) -> Callable[[], None]:
        """Add model providers: each child directory holds a ``provider.json``."""
        from openprogram.models.providers.catalog.plugin_dirs import add_root
        root = self.path(directory)
        dispose = add_root(f"plugin:{self.name}", root)
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            if (child / "provider.json").is_file():
                self._record("providers", child.name)
        return self.effect(dispose, "providers")

    def api_provider(self, api: str, provider: Any, structured_output: Any = None) -> Callable[[], None]:
        """Register a model wire implementation under a new ``api`` id."""
        from openprogram.models.providers.catalog import api_registry
        if api_registry._registry.get(api) is not None:
            raise ValueError(f"API {api!r} is already implemented")
        api_registry.register_api_provider(api, provider, structured_output)
        self._record("apis", api)
        return self.effect(lambda: api_registry.unregister_api_provider(api), f"api {api}")


def normalize_mcp_entry(config: dict[str, Any]) -> dict[str, Any]:
    """Accept the Claude Code / ``.mcp.json`` shape as well as ours.

    ``{"command": "npx", "args": [...]}`` becomes the command list we use;
    ``{"url": ...}`` without a type is a streamable HTTP server.
    """
    entry = dict(config)
    command = entry.get("command")
    if isinstance(command, str):
        entry["command"] = [command, *[str(a) for a in entry.pop("args", []) or []]]
    if "type" not in entry:
        entry["type"] = "local" if entry.get("command") else "http"
    elif entry["type"] in ("stdio",):
        entry["type"] = "local"
    elif entry["type"] in ("streamable-http", "streamableHttp"):
        entry["type"] = "http"
    return entry


def call_setup(setup: Callable[[ProgramContext], Any], ctx: ProgramContext) -> None:
    """Run an application's ``setup``; a returned callable becomes an undo."""
    result = setup(ctx)
    if inspect.iscoroutine(result):
        result.close()
        raise TypeError("setup(ctx) must be synchronous")
    if callable(result):
        ctx.effect(result, "setup")


__all__ = ["ProgramContext", "call_setup", "set_host_loop"]

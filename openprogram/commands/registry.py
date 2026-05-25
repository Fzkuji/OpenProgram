"""Process-wide merge of every command source.

Phase-1 wires only the user/project file loaders plus the existing
plugin command list (adapter). Skills + MCP land in later phases —
the registry shape is already general enough; just call
:func:`register_external` with new ``source`` values when they come
online.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from .frontmatter import ParsedCommand
from .loader import LoadedCommand, load_project, load_user


# Source priority (low → high). Later entries override earlier on
# name collisions; aliases follow the same rule.
SOURCE_ORDER: list[str] = ["builtin", "plugin", "mcp", "skill", "user", "project"]


@dataclass
class CommandSpec:
    """One concrete, resolved command entry — what the dispatcher and
    the UI see. ``raw`` is the parsed frontmatter / body; ``source``
    plus ``source_label`` keep provenance visible."""
    name: str
    source: str
    source_label: str
    path: str = ""                       # file path if any; "" for in-memory
    raw: Optional[ParsedCommand] = None
    plugin_name: str = ""                # set when source == "plugin"
    builtin_handler: Any = None          # callable for source == "builtin"

    @property
    def description(self) -> str:
        return self.raw.description if self.raw else ""

    @property
    def hidden(self) -> bool:
        return bool(self.raw.hidden) if self.raw else False

    @property
    def user_invocable(self) -> bool:
        return bool(self.raw.user_invocable) if self.raw else True

    def to_view(self) -> dict[str, Any]:
        r = self.raw
        return {
            "name": self.name,
            "source": self.source,
            "source_label": self.source_label,
            "plugin": self.plugin_name,
            "path": self.path,
            "description": (r.description if r else ""),
            "when_to_use": (r.when_to_use if r else ""),
            "argument_hint": (r.argument_hint if r else ""),
            "arguments": (r.arguments if r else []),
            "type": (r.type if r else "prompt"),
            "context": (r.context if r else "inline"),
            "agent": (r.agent if r else "general-purpose"),
            "model": (r.model if r else "inherit"),
            "effort": (r.effort if r else "inherit"),
            "allowed_tools": (r.allowed_tools if r else []),
            "paths": (r.paths if r else []),
            "requires": (r.requires if r else {}),
            "version": (r.version if r else ""),
            "hidden": (r.hidden if r else False),
            "user_invocable": (r.user_invocable if r else True),
            "aliases": (r.aliases if r else []),
        }


# ---------------------------------------------------------------------------
# Internal state — single registry per process
# ---------------------------------------------------------------------------

_lock = RLock()
# Per-source bucket; each bucket holds ``{name: CommandSpec}``.
_buckets: dict[str, dict[str, CommandSpec]] = {s: {} for s in SOURCE_ORDER}
# Aliases — separate index, same override rules. value is the spec name.
_aliases: dict[str, dict[str, str]] = {s: {} for s in SOURCE_ORDER}
# True once a reload has populated the file-backed layers.
_loaded = False


def _put(spec: CommandSpec) -> None:
    bucket = _buckets.setdefault(spec.source, {})
    bucket[spec.name] = spec
    if spec.raw and spec.raw.aliases:
        ab = _aliases.setdefault(spec.source, {})
        for alias in spec.raw.aliases:
            if alias and alias != spec.name:
                ab[alias] = spec.name


def register_builtin(
    name: str, *,
    handler: Any,
    description: str = "",
    argument_hint: str = "",
) -> None:
    """Register a built-in (type: local) command from host code.

    Built-ins skip frontmatter — they're invoked via ``handler`` which
    the dispatcher calls with ``(session_ctx, raw_args)`` and which
    returns a ``LocalCommandResult``-shaped dict (or None for no-op).
    """
    from .frontmatter import ParsedCommand
    raw = ParsedCommand(
        name=name,
        description=description,
        argument_hint=argument_hint,
        type="local",
    )
    spec = CommandSpec(
        name=name, source="builtin", source_label="(builtin)",
        raw=raw, builtin_handler=handler,
    )
    with _lock:
        _put(spec)


def register_external(
    name: str, *,
    source: str,
    raw: ParsedCommand,
    source_label: str = "",
    path: str = "",
    plugin_name: str = "",
) -> None:
    """Hook for adapters (plugins / mcp / skill) to inject commands
    without going through the file scanner."""
    if source not in SOURCE_ORDER:
        raise ValueError(f"unknown source: {source!r}")
    spec = CommandSpec(
        name=name, source=source,
        source_label=source_label or f"({source})",
        raw=raw, path=path, plugin_name=plugin_name,
    )
    with _lock:
        _put(spec)


def _ingest_loaded(items: list[LoadedCommand]) -> None:
    for it in items:
        spec = CommandSpec(
            name=it.spec.name,
            source=it.source,
            source_label=it.source_label,
            path=it.path,
            raw=it.spec,
        )
        _put(spec)


def reload(*, cwd: Path | None = None) -> None:
    """Re-scan file-backed layers (user, project) and re-ingest the
    plugin adapter. Built-ins stay put — host code registers them at
    startup and they don't need re-scanning.

    Other source layers (mcp, skill) get their own reload triggers
    elsewhere; this function does not touch their buckets."""
    global _loaded
    with _lock:
        # Wipe file-backed + sync adapter layers. The MCP bucket has
        # its own async lifecycle (servers connect/disconnect) so it
        # is refreshed via ``sync_mcp_prompts()`` instead.
        for s in ("user", "project", "plugin", "skill"):
            _buckets[s] = {}
            _aliases[s] = {}

    _ingest_loaded(load_user())
    _ingest_loaded(load_project(cwd))

    # Adapters are best-effort: a broken upstream shouldn't poison
    # the rest of the registry.
    for mod_name in ("_plugin_adapter", "_skill_adapter"):
        try:
            mod = __import__(
                f"openprogram.commands.{mod_name}", fromlist=["sync_into_registry"]
            )
            mod.sync_into_registry()
        except Exception:
            pass

    _loaded = True


async def sync_mcp_prompts() -> None:
    """Re-snapshot MCP prompts. Runs in an event loop so it can await
    ``client.list_prompts()`` against live sessions. Called from the
    MCP registry after a server reaches ready state, and from
    ``/api/commands?refresh=mcp`` for explicit re-sync."""
    try:
        from openprogram.mcp.registry import list_clients
    except Exception:
        return
    from .frontmatter import ParsedCommand

    with _lock:
        _buckets["mcp"] = {}
        _aliases["mcp"] = {}

    for client in list_clients():
        if not getattr(client, "is_ready", False):
            continue
        server_name = client.config.name
        try:
            prompts = await client.list_prompts()
        except Exception:
            prompts = []
        for p in prompts or []:
            if not isinstance(p, dict):
                continue
            remote_name = str(p.get("name") or "").strip()
            if not remote_name:
                continue
            cmd_name = f"{server_name}:{remote_name}"
            args_decl: list[dict[str, Any]] = []
            for arg in (p.get("arguments") or []):
                if not isinstance(arg, dict):
                    continue
                n = str(arg.get("name") or "").strip()
                if not n or n.isdigit():
                    continue
                args_decl.append({
                    "name": n,
                    "description": str(arg.get("description") or ""),
                    "required": bool(arg.get("required", False)),
                })
            raw = ParsedCommand(
                name=cmd_name,
                description=str(p.get("description") or ""),
                arguments=args_decl,
                extras={
                    "_mcp_server": server_name,
                    "_mcp_prompt": remote_name,
                },
            )
            register_external(
                cmd_name, source="mcp",
                source_label=f"(mcp:{server_name})",
                raw=raw,
            )


def _ensure_loaded() -> None:
    if not _loaded:
        reload()


def list_all(*, include_hidden: bool = False) -> list[CommandSpec]:
    """Flatten every source bucket into one list, with override
    semantics applied. The first occurrence in iteration order wins,
    so we walk ``SOURCE_ORDER`` from highest priority back to lowest
    and skip names already seen."""
    _ensure_loaded()
    seen: set[str] = set()
    out: list[CommandSpec] = []
    with _lock:
        for source in reversed(SOURCE_ORDER):
            bucket = _buckets.get(source, {})
            for name, spec in bucket.items():
                if name in seen:
                    continue
                if spec.hidden and not include_hidden:
                    continue
                seen.add(name)
                out.append(spec)
    out.sort(key=lambda s: (SOURCE_ORDER.index(s.source), s.name))
    return out


def get(name: str) -> Optional[CommandSpec]:
    """Resolve a literal name (no alias / no namespace prefix)."""
    _ensure_loaded()
    with _lock:
        for source in reversed(SOURCE_ORDER):
            bucket = _buckets.get(source, {})
            if name in bucket:
                return bucket[name]
            alias_map = _aliases.get(source, {})
            if name in alias_map:
                target = alias_map[name]
                if target in bucket:
                    return bucket[target]
    return None


def resolve(text: str) -> Optional[CommandSpec]:
    """Resolve a user-typed command string. Accepts:

      * ``name`` — bare name; pick the winner per the override table
      * ``(source)name`` or ``(source:sub)name`` — pin to a specific
        source. Examples: ``(user)review``, ``(plugin)foo``,
        ``(mcp:linear)create_issue``.
    """
    _ensure_loaded()
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("/"):
        s = s[1:]
    if s.startswith("("):
        end = s.find(")")
        if end > 1:
            tag = s[1:end]
            rest = s[end + 1:]
            source = tag.split(":", 1)[0]
            with _lock:
                bucket = _buckets.get(source, {})
                if rest in bucket:
                    return bucket[rest]
                alias_map = _aliases.get(source, {})
                if rest in alias_map:
                    return bucket.get(alias_map[rest])
            return None
    return get(s)


def conflicts() -> list[dict[str, Any]]:
    """Names that appear in more than one bucket. Used by the UI to
    show the disambiguation hint and by ``/commands diff`` for
    debugging."""
    _ensure_loaded()
    by_name: dict[str, list[str]] = {}
    with _lock:
        for source in SOURCE_ORDER:
            for name in _buckets.get(source, {}):
                by_name.setdefault(name, []).append(source)
    return [
        {"name": n, "sources": srcs}
        for n, srcs in by_name.items()
        if len(srcs) > 1
    ]


def clear_all() -> None:
    """Test helper. Wipes every bucket so the next ``reload`` starts
    from a known state."""
    global _loaded
    with _lock:
        for s in SOURCE_ORDER:
            _buckets[s] = {}
            _aliases[s] = {}
        _loaded = False

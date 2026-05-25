"""Bridge the existing plugin loader's ``contrib._commands`` list into
the unified command registry.

Plugins continue to declare commands through their manifest exactly
as before (``entrypoints.commands`` either inline or as a markdown
dir). The adapter materialises one :class:`CommandSpec` per item so
the rest of the system can see plugin commands and user/project
commands through the same API.
"""
from __future__ import annotations

from .frontmatter import ParsedCommand
from . import registry as _reg


def sync_into_registry() -> None:
    """Re-snapshot every enabled plugin's command list into the
    ``plugin`` source bucket. Called from ``registry.reload`` — safe
    to call repeatedly."""
    try:
        from openprogram.plugins.loader import list_plugins
    except Exception:
        return

    for p in list_plugins():
        if not getattr(p, "enabled", False) or not getattr(p, "loaded", False):
            continue
        cmds = (getattr(p, "contrib", {}) or {}).get("_commands") or []
        for c in cmds:
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            raw = ParsedCommand(
                name=name,
                description=str(c.get("description") or ""),
                body=str(c.get("prompt") or ""),
            )
            _reg.register_external(
                name, source="plugin",
                source_label=f"(plugin:{p.name})",
                raw=raw,
                plugin_name=p.name,
            )

"""The ``plugin`` tool: write, install and hot-reload declarative OpenProgram plugins."""
from __future__ import annotations

from openprogram.programs._host.runtime import function

_DESC = (
    "Extend OpenProgram with a plugin instead of editing its source. A plugin is "
    "declarative: skills, slash commands, agents, hooks (shell commands on events), "
    "MCP servers and provider directories named by its manifest; a manifest that "
    "names code is refused (code that adds tools or a UI is an application, installed "
    "with `openprogram programs install`). guide returns the authoring guide (manifest "
    "keys, hook events, exposure tiers); inspect returns the same as JSON with every "
    "registered entry and its tier. Write the plugin directory with the file tools, "
    "then install it by absolute path: it is linked, trusted and enabled, and takes "
    "effect at once without a restart. After editing its files, reload applies the "
    "change. list shows installed plugins with what each registered and its error; "
    "enable, disable and uninstall take a name (uninstall of a linked plugin removes "
    "only the link). Everything a plugin adds is removed on disable or reload."
)


def _row(p) -> dict:
    return {
        "name": p.name, "version": p.manifest.version, "source": p.manifest.source_kind,
        "root": p.manifest.root, "enabled": p.enabled, "loaded": p.loaded,
        "contributions": dict(p.ctx.contributions) if p.ctx is not None else {},
        "error": (p.error or "").strip(),
    }


@function(name="plugin", description=_DESC, toolset=["core"],
          unsafe_in=["plan"], requires_approval=True)
def plugin(action: str = "guide", name: str = "", path: str = "") -> dict:
    """Manage OpenProgram plugins.

    Args:
        action: guide, inspect, list, install, enable, disable, reload or uninstall.
        name: The plugin's name, for enable, disable, reload and uninstall.
        path: Absolute directory of the plugin, for install.
    """
    from openprogram.extensions.plugins import inspect as plugin_inspect, installer, loader, trust
    from openprogram.programs import ToolReturn

    def fail(message: str) -> ToolReturn:
        return ToolReturn(json_data={"ok": False, "error": message}, is_error=True)

    if action == "guide":
        return {"ok": True, "guide": plugin_inspect.guide()}
    if action == "inspect":
        return {"ok": True, **plugin_inspect.describe()}
    if action == "list":
        return {"ok": True, "plugins": [_row(p) for p in loader.list_plugins()],
                "errors": loader.all_errors()}
    try:
        if action == "install":
            if not path:
                return fail("install needs the plugin directory as path")
            result = installer.install("path", path)
            if not result.get("success"):
                return fail(result.get("log") or "install failed")
            installed = result["name"]
            loader.rescan()
            # The owner approved this call: the linked plugin is theirs.
            trust.set_trust(installed, "community")
            p = loader.load_plugin(installed)
            return {"ok": True, "log": result.get("log", ""), "plugin": _row(p)}
        if not name:
            return fail(f"{action} needs the plugin name")
        if action == "enable":
            return {"ok": True, "plugin": _row(loader.load_plugin(name))}
        if action == "disable":
            return {"ok": True, "plugin": _row(loader.unload_plugin(name))}
        if action == "reload":
            return {"ok": True, "plugin": _row(loader.reload_plugin(name))}
        if action == "uninstall":
            if (p := loader.get_plugin(name)) is not None and p.loaded:
                loader.unload_plugin(name)
            result = installer.uninstall(name)
            loader.rescan()
            if not result.get("success"):
                return fail(result.get("log") or "uninstall failed")
            return {"ok": True, "log": result.get("log", "")}
    except (KeyError, PermissionError, RuntimeError, ValueError, OSError, ImportError, TypeError) as exc:
        p = loader.get_plugin(name) if name else None
        detail = (p.error if p is not None and p.error else "") or str(exc)
        return fail(detail.strip())
    return fail(f"unknown action {action!r}")


# Plugins register into the worker's own registries.
setattr(plugin, "_run_in_worker", True)

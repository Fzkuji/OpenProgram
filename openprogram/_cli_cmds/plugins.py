"""``openprogram plugins`` handlers.

Mirrors the new ``skills`` CLI shape (search / install / update / remove)
but targets the plugin loader + four-source installer instead. The
search verb is best-effort: most plugin packages live in pip/npm
registries that have their own search APIs — for now we just list
known marketplaces.
"""
from __future__ import annotations

import sys


def _cmd_plugins_list(as_json: bool = False) -> int:
    from openprogram.plugins.loader import list_plugins

    plugins = list_plugins()
    if as_json:
        import json as _json
        print(_json.dumps([{
            "name": p.name,
            "version": p.manifest.version,
            "enabled": p.enabled,
            "loaded": p.loaded,
            "trust": getattr(p, "trust", None),
            "source": p.source,
            "error": p.error,
        } for p in plugins], indent=2))
        return 0

    if not plugins:
        print("(no plugins installed)")
        return 0
    print(f"Installed plugins ({len(plugins)}):\n")
    for p in plugins:
        flags = []
        if p.enabled:
            flags.append("enabled")
        else:
            flags.append("disabled")
        if p.error:
            flags.append("ERROR")
        flag_str = " ".join(flags)
        version = p.manifest.version or "?"
        desc = (p.manifest.description or "").strip()[:60]
        print(f"  {p.name:32s} v{version:8s}  [{flag_str}]  {desc}")
        if p.error:
            print(f"    error: {p.error.splitlines()[0]}")
    return 0


def _cmd_plugins_search(query: str) -> int:
    """Plugins don't have a unified search registry yet. List the
    configured marketplaces so the user knows where to look."""
    from openprogram.plugins.marketplace import list_marketplaces, fetch_index

    markets = list_marketplaces()
    if not markets:
        print("(no marketplaces configured)")
        print("Add one with:  openprogram plugins marketplace add <url>")
        return 0
    q = query.lower()
    any_hits = False
    for m in markets:
        try:
            entries = fetch_index(m["id"])
        except Exception as e:
            print(f"  [{m['name']}] fetch failed: {e}", file=sys.stderr)
            continue
        hits = [
            e for e in entries
            if q in (e.get("name", "")).lower() or q in (e.get("description", "")).lower()
        ]
        if not hits:
            continue
        any_hits = True
        print(f"\n[{m['name']}]")
        for e in hits:
            desc = (e.get("description") or "").strip()[:80]
            print(f"  {e.get('name', '?'):32s} {desc}")
    if not any_hits:
        print("(no matches)")
    return 0


def _cmd_plugins_install(source: str, spec: str, ref: str | None = None) -> int:
    """``source`` ∈ {pip, npm, git, path}. ``spec`` is the package name / URL /
    absolute path. ``ref`` is the optional git ref."""
    from openprogram.plugins.installer import install

    result = install(source, spec, ref=ref)
    log = result.get("log", "")
    if log:
        print(log)
    if not result.get("success"):
        print(f"Error: install failed", file=sys.stderr)
        return 1
    name = result.get("name") or spec
    print(f"Installed: {name}")
    return 0


def _cmd_plugins_uninstall(name: str) -> int:
    from openprogram.plugins.installer import uninstall

    result = uninstall(name)
    if result.get("log"):
        print(result["log"])
    if not result.get("success"):
        return 1
    print(f"Removed: {name}")
    return 0


def _cmd_plugins_update(all_flag: bool, name: str | None) -> int:
    """Re-install (= upgrade) a plugin. With ``--all`` re-installs every
    pip-source plugin via ``pip install --upgrade``."""
    from openprogram.plugins.loader import list_plugins
    from openprogram.plugins.installer import install

    targets: list = []
    if all_flag:
        targets = list_plugins()
    elif name:
        targets = [p for p in list_plugins() if p.name == name]
        if not targets:
            print(f"Error: plugin not installed: {name}", file=sys.stderr)
            return 1
    else:
        print("Error: pass --all or a plugin name", file=sys.stderr)
        return 2

    updated = 0
    for p in targets:
        src = getattr(p, "source", "") or ""
        # We only know how to re-pull pip and npm sources idempotently.
        if src not in ("pip", "npm"):
            print(f"  skipped {p.name}: source {src!r} cannot be auto-updated")
            continue
        try:
            r = install(src, p.name)
            if r.get("success"):
                print(f"  updated {p.name}")
                updated += 1
            else:
                print(f"  fail   {p.name}: {r.get('log', '').splitlines()[-1] if r.get('log') else ''}")
        except Exception as e:
            print(f"  fail   {p.name}: {e}")
    print(f"\nUpdated {updated} plugin(s)")
    return 0


def _cmd_plugins_enable(name: str) -> int:
    from openprogram.plugins.loader import load_plugin

    try:
        p = load_plugin(name)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Enabled: {p.name}")
    return 0


def _cmd_plugins_disable(name: str) -> int:
    from openprogram.plugins.loader import unload_plugin

    try:
        p = unload_plugin(name)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Disabled: {p.name}")
    return 0

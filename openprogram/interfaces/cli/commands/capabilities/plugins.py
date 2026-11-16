"""``openprogram plugins`` handlers.

Mirrors the ``skills`` CLI shape (search / install / update / remove) but
targets the plugin loader and installer. A plugin is declarative; one
whose manifest names code is refused. The search verb is best-effort: it
lists the entries of the configured marketplaces.
"""
from __future__ import annotations

import asyncio
import sys


def _cmd_plugins_list(as_json: bool = False) -> int:
    from openprogram.extensions.plugins.loader import list_plugins
    from openprogram.extensions.plugins import trust as _trust

    plugins = list_plugins()
    if as_json:
        import json as _json
        print(_json.dumps([{
            "name": p.name,
            "version": p.manifest.version,
            "enabled": p.enabled,
            "loaded": p.loaded,
            "trust": _trust.get_trust(p.name),
            "source": p.manifest.source_kind,
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
    from openprogram.extensions.plugins.marketplace import list_marketplaces, fetch_index

    markets = list_marketplaces()
    if not markets:
        print("(no marketplaces configured)")
        print("Add one with:  openprogram plugins marketplace add <url>")
        return 0
    q = query.lower()
    any_hits = False
    for m in markets:
        try:
            # fetch_index is async; the CLI has no running loop, so drive it.
            entries = asyncio.run(fetch_index(m["id"]))
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
    """``source`` ∈ {npm, git, path}. ``spec`` is the package name / URL /
    absolute path. ``ref`` is the optional git ref."""
    answer = _worker("/api/plugins/install", {"source": source, "spec": spec, "ref": ref})
    if answer is not None:
        answer.pop("_status", None)
        result = answer
    else:
        from openprogram.extensions.plugins.installer import install
        result = install(source, spec, ref=ref)
    log = result.get("log", "")
    if log:
        print(log)
    if not result.get("success"):
        print("Error: install failed", file=sys.stderr)
        return 1
    name = result.get("name") or spec
    print(f"Installed: {name}")
    return 0


def _cmd_plugins_uninstall(name: str) -> int:
    answer = _worker(f"/api/plugins/{name}/uninstall")
    if answer is not None:
        answer.pop("_status", None)
        result = answer
    else:
        from openprogram.extensions.plugins.installer import uninstall
        result = uninstall(name)
    if result.get("log"):
        print(result["log"])
    if not result.get("success"):
        return 1
    print(f"Removed: {name}")
    return 0


def _cmd_plugins_update(all_flag: bool, name: str | None) -> int:
    """Re-install (= upgrade) a plugin. With ``--all`` re-installs every
    npm-source plugin."""
    from openprogram.extensions.plugins.loader import list_plugins
    from openprogram.extensions.plugins.installer import install

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
        src = p.manifest.source_kind or ""
        # Only npm sources can be re-pulled idempotently.
        if src != "npm":
            print(f"  skipped {p.name}: source {src!r} cannot be auto-updated")
            continue
        try:
            r = install(src, p.name, upgrade=True)
            if r.get("success"):
                print(f"  updated {p.name}")
                updated += 1
            else:
                print(f"  fail   {p.name}: {r.get('log', '').splitlines()[-1] if r.get('log') else ''}")
        except Exception as e:
            print(f"  fail   {p.name}: {e}")
    print(f"\nUpdated {updated} plugin(s)")
    return 0


def _worker(path: str, body: dict | None = None) -> dict | None:
    """Ask the running worker, so a change applies to it at once.

    Returns its JSON answer (with ``_status``), or ``None`` when no worker
    is running here; the caller then changes the profile files directly
    and the change applies on the next worker start.
    """
    try:
        from openprogram.host.security.backend_endpoint import resolve_backend_endpoint
        import httpx
        endpoint = resolve_backend_endpoint()
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
            response = client.post(endpoint.base_url + path, json=body or {}, headers={
                "Authorization": endpoint.authorization_header, "Origin": endpoint.origin})
    except Exception:
        return None
    try:
        value = response.json()
    except ValueError:
        value = {"error": response.text[:200]}
    if not isinstance(value, dict):
        value = {"result": value}
    value["_status"] = response.status_code
    return value


def _report(answer: dict, done: str) -> int:
    if answer["_status"] >= 400 or answer.get("error"):
        print(f"Error: {answer.get('error') or answer}", file=sys.stderr)
        return 1
    print(done)
    return 0


def _cmd_plugins_enable(name: str) -> int:
    answer = _worker(f"/api/plugins/{name}/toggle", {"enabled": True})
    if answer is not None:
        return _report(answer, f"Enabled: {name}")
    from openprogram.extensions.plugins.loader import load_plugin

    try:
        p = load_plugin(name)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Enabled: {p.name} (applies when the worker starts)")
    return 0


def _cmd_plugins_disable(name: str) -> int:
    answer = _worker(f"/api/plugins/{name}/toggle", {"enabled": False})
    if answer is not None:
        return _report(answer, f"Disabled: {name}")
    from openprogram.extensions.plugins.loader import unload_plugin

    try:
        p = unload_plugin(name)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Disabled: {p.name}")
    return 0


def _cmd_plugins_reload(name: str) -> int:
    """Re-read a plugin's manifest and files and load it again."""
    answer = _worker(f"/api/plugins/{name}/reload")
    if answer is not None:
        return _report(answer, f"Reloaded: {name}")
    print("Error: no worker is running; the plugin loads fresh when it starts",
          file=sys.stderr)
    return 1


def _cmd_plugins_inspect(as_json: bool = False) -> int:
    """Print what a plugin may contain (manifest keys, hook events), what
    is refused, and every registered entry with its exposure tier."""
    import json as _json
    from openprogram.extensions.plugins.inspect import describe, guide

    if as_json:
        print(_json.dumps(describe(), indent=2, ensure_ascii=False))
    else:
        print(guide())
    return 0

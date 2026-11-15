"""Plugin discovery and loading.

A plugin is declarative (see ``manifest``): its skills, slash commands,
agents, hooks, MCP servers and provider directories mount through one
:class:`ProgramContext` owned by ``plugin:<name>``, and disposing the
context takes all of them out again. No plugin code runs in the worker; a
manifest that names code is refused with a pointer to applications.

Sources, later ones winning a name clash: npm
(``~/.openprogram/plugins/node_modules/<name>/``), path
(``~/.openprogram/plugins/<name>/``, often a link) and project-pinned.
"""
from __future__ import annotations

import logging
import importlib.metadata as md
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from . import hooks as _hooks, paths, registry, trust as _trust
from openprogram.programs._host.context import ProgramContext
from .manifest import (
    PluginContainsCode,
    PluginManifest,
    check_compatibility,
    code_refusal,
    parse_manifest_dir,
)

_log = logging.getLogger(__name__)


# ---------- 数据 ----------

@dataclass
class Plugin:
    manifest: PluginManifest
    enabled: bool = False
    loaded: bool = False
    error: str = ""
    contrib: dict[str, Any] = field(default_factory=dict)
    ctx: Optional[ProgramContext] = None  # registrations of the running plugin

    @property
    def name(self) -> str:
        return self.manifest.name


_lock = RLock()
_plugins: dict[str, Plugin] = {}
_errors: dict[str, str] = {}  # name → error 字符串 (扫描期失败也记)


# ---------- 启用持久化 ----------

def _enabled_file() -> Path:
    return paths.root() / "plugins-enabled.json"


def _load_enabled() -> set[str]:
    f = _enabled_file()
    if not f.exists():
        return set()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    except (OSError, ValueError):
        _log.warning("enabled-plugins file %s unreadable; treating as empty",
                     f, exc_info=True)
    return set()


def _save_enabled(s: set[str]) -> None:
    _enabled_file().write_text(json.dumps(sorted(s), indent=2), encoding="utf-8")


# ---------- 扫描 ----------

def _scan_npm() -> list[PluginManifest]:
    out: list[PluginManifest] = []
    mods = paths.npm_modules_dir()
    if not mods.is_dir():
        return out
    for child in sorted(mods.iterdir()):
        if not child.is_dir():
            continue
        # 跳 scoped 索引
        if child.name.startswith("@"):
            for sub in sorted(child.iterdir()):
                if sub.is_dir():
                    mf = parse_manifest_dir(sub)
                    if mf:
                        mf.source_kind = "npm"
                        out.append(mf)
            continue
        mf = parse_manifest_dir(child)
        if mf:
            mf.source_kind = "npm"
            out.append(mf)
    return out


def _scan_local() -> list[PluginManifest]:
    out: list[PluginManifest] = []
    base = paths.plugins_dir()
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name == "node_modules":
            continue
        mf = parse_manifest_dir(child)
        if mf:
            mf.source_kind = "link" if child.is_symlink() else "path"
            out.append(mf)
    return out


def _scan_project(cwd: Path | None = None) -> list[PluginManifest]:
    out: list[PluginManifest] = []
    pin = paths.project_pins(cwd)
    if not pin.is_file():
        return out
    try:
        data = json.loads(pin.read_text(encoding="utf-8"))
    except Exception as e:
        _errors["__project_pins__"] = f"parse {pin}: {e}"
        return out
    entries = data.get("plugins") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path_s = entry.get("path") or entry.get("source")
        if not path_s:
            continue
        p = Path(path_s).expanduser()
        if not p.is_absolute():
            p = (pin.parent / p).resolve()
        mf = parse_manifest_dir(p)
        if mf:
            mf.source_kind = "project"
            out.append(mf)
        else:
            _errors[str(p)] = "no manifest found"
    return out


def rescan(cwd: Path | None = None) -> dict[str, Plugin]:
    """重新扫描所有来源。来源优先级越后越高，覆盖之前同名。

    A plugin that is running keeps its runtime state across a rescan when
    it still resolves to the same directory; one that disappeared or moved
    is unloaded first, so nothing it registered outlives it.
    """
    global _plugins
    with _lock:
        enabled_persist = _load_enabled()
        merged: dict[str, Plugin] = {}
        for mf in _scan_npm() + _scan_local() + _scan_project(cwd):
            merged[mf.name] = Plugin(manifest=mf, enabled=mf.name in enabled_persist,
                                     error=code_refusal(mf))
        for name, old in list(_plugins.items()):
            if not old.loaded:
                continue
            new = merged.get(name)
            if new is not None and new.manifest.root == old.manifest.root:
                new.loaded, new.enabled, new.ctx = True, True, old.ctx
                new.contrib, new.error = old.contrib, old.error
            else:
                _deactivate(old)
        _plugins = merged
        return dict(_plugins)


# ---------- 加载 ----------

def _host_version() -> str:
    """真实 host 版本，用于 plugin compatibility 门控。

    openprogram 没有模块级 ``__version__``，但安装后 dist metadata 有；
    读不到时退回 ``0.0.0`` 让 compatibility 检查保守通过。"""
    try:
        return md.version("openprogram")
    except Exception:
        return "0.0.0"


_HOST_VERSION = _host_version()


def _options(name: str) -> dict[str, Any]:
    f = paths.options_dir() / f"{name}.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _rel(plugin: Plugin, value: str) -> Path:
    base = Path(plugin.manifest.root) if plugin.manifest.root else Path.cwd()
    path = (base / value).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"{value} is outside the plugin directory")
    return path


def _md_commands(directory: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for md_file in sorted(directory.rglob("*.md")) if directory.is_dir() else []:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _split_frontmatter(text)
        out.append({"name": fm.get("name", md_file.stem),
                    "description": fm.get("description", ""), "prompt": body.strip()})
    return out


def _agent_definitions(plugin: Plugin, value: Any) -> list[dict[str, Any]]:
    """Agents as a list of objects or a directory of Claude-style ``.md``."""
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, str):
        return []
    rows = []
    directory = _rel(plugin, value)
    for md_file in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        fm, body = _split_frontmatter(md_file.read_text(encoding="utf-8"))
        rows.append({"name": fm.get("name", md_file.stem), "description": fm.get("description", ""),
                     "prompt": body.strip(), "tools": fm.get("tools", "")})
    return rows


def _install_agents(ctx: ProgramContext, plugin: Plugin, value: Any) -> None:
    """Create the plugin's Agents that do not exist yet.

    Agents are user records with their own sessions, so they stay when the
    plugin is disabled; enabling again reuses them untouched.
    """
    import re
    from openprogram.runtime.agent_registry import manager
    for definition in _agent_definitions(plugin, value):
        label = str(definition.get("name") or "").strip()
        if not label:
            continue
        agent_id = re.sub(r"[^a-z0-9_-]+", "-", f"{plugin.name}-{label}".lower()).strip("-")[:40]
        if not agent_id[:1].isalpha():
            agent_id = f"p-{agent_id}"[:40]
        if manager.get(agent_id) is None:
            manager.create(agent_id, name=label, system_prompt=str(definition.get("prompt") or ""))
            patch: dict[str, Any] = {"description": str(definition.get("description") or "")}
            tools = definition.get("tools")
            if isinstance(tools, str) and tools.strip():
                tools = [t.strip().lower() for t in tools.split(",") if t.strip()]
            if isinstance(tools, list) and tools:
                patch["tools"] = {"mode": "selected", "allowed": [str(t) for t in tools]}
            elif isinstance(tools, dict):
                patch["tools"] = tools
            manager.update(agent_id, patch, replace_tool_policy="tools" in patch)
        ctx._record("agents", agent_id)


def _mcp_entries(plugin: Plugin, value: Any) -> dict[str, dict[str, Any]]:
    """``mcpServers`` inline or as a ``.mcp.json``-style file."""
    if isinstance(value, str):
        value = json.loads(_rel(plugin, value).read_text(encoding="utf-8"))
    if isinstance(value, dict) and isinstance(value.get("mcpServers"), dict):
        value = value["mcpServers"]
    if not isinstance(value, dict):
        return {}
    return {str(k): dict(v) for k, v in value.items() if isinstance(v, dict)}


def _activate(plugin: Plugin) -> None:
    """Mount the plugin's manifest contents through its context.

    Everything goes through one :class:`ProgramContext`; a failure part way
    undoes what already registered, so a broken plugin leaves no trace.
    """
    mf = plugin.manifest
    refusal = code_refusal(mf)
    if refusal:
        raise PluginContainsCode(refusal)
    ctx = ProgramContext(mf.name, mf.root, _options(mf.name))
    plugin.ctx = ctx
    ep = dict(mf.entrypoints)
    contrib: dict[str, Any] = {}
    try:
        if isinstance(ep.get("skills"), str):
            ctx.skills(ep["skills"])
            contrib["skills"] = str(_rel(plugin, ep["skills"]))
        cmds = ep.get("commands")
        if isinstance(cmds, dict):
            for cname, cdef in cmds.items():
                if isinstance(cdef, dict):
                    ctx.command(str(cname), str(cdef.get("prompt", "")), str(cdef.get("description", "")))
        elif isinstance(cmds, str) and "*" not in cmds:
            for c in _md_commands(_rel(plugin, cmds)):
                ctx.command(c["name"], c["prompt"], c["description"])
        hooks_ep = ep.get("hooks")
        if isinstance(hooks_ep, str):
            hooks_ep = json.loads(_rel(plugin, hooks_ep).read_text(encoding="utf-8"))
        for hook in _hooks.command_hooks(hooks_ep, mf.root):
            _hooks.mount(ctx, hook)
        for server, entry in _mcp_entries(plugin, ep.get("mcpServers")).items():
            ctx.mcp_server(server, entry)
        if isinstance(ep.get("providers"), str) and _rel(plugin, ep["providers"]).is_dir():
            ctx.provider_root(ep["providers"])
        if ep.get("agents"):
            _install_agents(ctx, plugin, ep["agents"])
    except BaseException:
        ctx.dispose()
        plugin.ctx = None
        raise
    if ctx.commands:
        contrib["_commands"] = ctx.commands
    plugin.contrib = contrib
    registry.set_plugin_contrib(mf.name, contrib)


def _deactivate(plugin: Plugin) -> list[str]:
    """Undo everything the plugin registered. Returns undo errors."""
    from openprogram.runtime.events import emit_safe
    emit_safe("plugin.disable", "system", {"plugin": plugin.name})
    errors = plugin.ctx.dispose() if plugin.ctx is not None else []
    registry.clear_plugin_contrib(plugin.name)
    plugin.ctx = None
    plugin.loaded = False
    plugin.contrib = {}
    return errors


def _announce(name: str) -> None:
    """Refresh what reads plugin contributions and tell the Web page."""
    try:
        from openprogram.runtime.commands import registry as commands
        commands.reload()
    except Exception:
        # The Web page is still told; the command list catches up on the
        # next reload.
        _log.warning("slash command reload after plugin %s change failed",
                     name, exc_info=True)
    from openprogram.runtime.events import emit_ws_frame
    emit_ws_frame({"type": "plugins:changed", "data": {"plugin": name}})


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Very small YAML-ish frontmatter parser — same shape as the
    skills loader's parser. Returns ``(dict, body)``."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if len(lines) < 2:
        return {}, text
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return {}, text
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip("\"'")
    return fm, "\n".join(lines[end + 1:])


def _check(p: Plugin) -> None:
    """Refuse a plugin that names code, or whose trust, compatibility or
    dependencies fail."""
    name = p.name
    refusal = code_refusal(p.manifest)
    if refusal:
        p.error = refusal
        raise PluginContainsCode(refusal)
    level = _trust.get_trust(name)
    if level == "untrusted":
        p.error = "trust=untrusted; please elevate trust before enabling"
        raise PermissionError(p.error)
    ok, reason = check_compatibility(p.manifest.compatibility, _HOST_VERSION)
    if not ok:
        p.error = f"incompatible: {reason}"
        raise RuntimeError(p.error)
    # Dependency resolution — every plugin in ``requires`` must be
    # installed AND loaded. We don't auto-enable upstream deps;
    # that would be too magical. We just report the gap.
    missing: list[str] = []
    not_enabled: list[str] = []
    for dep in p.manifest.requires:
        dep_p = _plugins.get(dep)
        if dep_p is None:
            missing.append(dep)
        elif not dep_p.loaded:
            not_enabled.append(dep)
    if missing or not_enabled:
        parts: list[str] = []
        if missing:
            parts.append(f"missing dependencies: {', '.join(missing)}")
        if not_enabled:
            parts.append(f"deps not enabled: {', '.join(not_enabled)}")
        p.error = "; ".join(parts)
        raise RuntimeError(p.error)


def load_plugin(name: str) -> Plugin:
    """加载并启用单个 plugin。trust 不足时拒绝。"""
    paths.sanitize_name(name)
    with _lock:
        p = _plugins.get(name)
        if not p:
            raise KeyError(f"unknown plugin: {name}")
        if p.loaded:
            return p
        p.error = ""
        try:
            _check(p)
            _activate(p)
        except Exception as e:
            p.enabled = False
            p.loaded = False
            if not p.error:
                p.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            _errors[name] = p.error
            raise
        p.loaded = True
        p.enabled = True
        p.error = "deprecated" if p.manifest.deprecated else ""
        _errors.pop(name, None)
        s = _load_enabled()
        s.add(name)
        _save_enabled(s)
        from openprogram.runtime.events import emit_safe
        emit_safe("plugin.enable", "system", {"plugin": name})
    _announce(name)
    return p


def unload_plugin(name: str) -> Plugin:
    paths.sanitize_name(name)
    with _lock:
        p = _plugins.get(name)
        if not p:
            raise KeyError(f"unknown plugin: {name}")
        errors = _deactivate(p) if p.loaded else []
        p.enabled = False
        p.error = "; ".join(errors)
        s = _load_enabled()
        s.discard(name)
        _save_enabled(s)
    _announce(name)
    return p


def reload_plugin(name: str) -> Plugin:
    """Unload, re-read the manifest and its files from disk, load again.

    A plugin that fails to come back stays enabled with its error, so the
    next fix and reload (or restart) brings it back.
    """
    paths.sanitize_name(name)
    with _lock:
        p = _plugins.get(name)
        if not p:
            raise KeyError(f"unknown plugin: {name}")
        if p.loaded:
            _deactivate(p)
        rescan()
        p = _plugins.get(name)
        if p is None:
            raise KeyError(f"plugin {name} is no longer installed")
    try:
        return load_plugin(name)
    except Exception:
        with _lock:
            s = _load_enabled()
            s.add(name)
            _save_enabled(s)
        raise


def load_enabled() -> dict[str, str]:
    """Load every plugin the profile has enabled, dependencies first.

    Called once at worker startup. Returns ``{name: error}`` for the ones
    that could not load; they stay enabled so a fix takes effect on reload.
    """
    with _lock:
        rescan()
        wanted = [p for p in _plugins.values() if p.enabled and not p.loaded]
    failures: dict[str, str] = {}
    pending = {p.name: p for p in wanted}
    while pending:
        ready = [n for n, p in pending.items()
                 if all(dep not in pending for dep in p.manifest.requires)]
        if not ready:  # a dependency cycle: try them anyway, each reports why
            ready = list(pending)
        for name in sorted(ready):
            pending.pop(name)
            try:
                load_plugin(name)
            except Exception as exc:
                failures[name] = str(exc)
                with _lock:
                    s = _load_enabled()
                    s.add(name)
                    _save_enabled(s)
                    if name in _plugins:
                        _plugins[name].enabled = True
    return failures


# ---------- 公共 getter ----------

def list_plugins() -> list[Plugin]:
    with _lock:
        if not _plugins:
            try:
                rescan()
            except Exception as e:
                _errors["__scan__"] = str(e)
        return list(_plugins.values())


def get_plugin(name: str) -> Plugin | None:
    with _lock:
        if not _plugins:
            try:
                rescan()
            except Exception:
                # A lookup must not fail because discovery did; the plugin
                # reads as absent.
                _log.warning("plugin rescan failed while looking up %s", name,
                             exc_info=True)
        return _plugins.get(name)


def all_errors() -> dict[str, str]:
    with _lock:
        out = dict(_errors)
        for p in _plugins.values():
            if p.error:
                out.setdefault(p.name, p.error)
        return out

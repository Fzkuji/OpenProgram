"""四来源 plugin 扫描与加载。

来源优先级：project-pinned > path (~/.openprogram/plugins/<name>/) >
npm (~/.openprogram/plugins/node_modules/<name>/) > pip entry_points。
名字冲突时高优先级覆盖。
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from . import paths, registry, trust as _trust
from .manifest import (
    PluginManifest,
    check_compatibility,
    from_entry_point_metadata,
    parse_manifest_dir,
)


# ---------- 数据 ----------

@dataclass
class Plugin:
    manifest: PluginManifest
    enabled: bool = False
    loaded: bool = False
    error: str = ""
    module: Any = None  # in-process import 拿到的 module/object
    contrib: dict[str, Any] = field(default_factory=dict)

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
    except Exception:
        pass
    return set()


def _save_enabled(s: set[str]) -> None:
    _enabled_file().write_text(json.dumps(sorted(s), indent=2), encoding="utf-8")


# ---------- 扫描 ----------

def _scan_pip() -> list[PluginManifest]:
    out: list[PluginManifest] = []
    try:
        eps = md.entry_points(group="openprogram.plugins")
    except Exception:
        eps = []  # py<3.10 兼容
    for ep in eps:
        name = ep.name
        try:
            dist = ep.dist
            root = ""
            if dist and getattr(dist, "locate_file", None):
                try:
                    root = str(dist.locate_file(""))
                except Exception:
                    root = ""
            meta = {}
            try:
                if dist and dist.metadata:
                    meta = {"version": dist.version, "summary": dist.metadata.get("Summary", "")}
            except Exception:
                pass
            # 试着解析包目录内的 plugin.json / pyproject 等
            mf: Optional[PluginManifest] = None
            if root:
                mf = parse_manifest_dir(Path(root))
            if not mf:
                mf = from_entry_point_metadata(name, meta, root=root)
            mf.source_kind = "pip"
            # 把 entry_point target 存进 entrypoints 供后续 import
            mf.entrypoints.setdefault("_pip_entry", f"{ep.value}")
            out.append(mf)
        except Exception as e:
            _errors[name] = f"pip scan: {e}"
    return out


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
            mf.source_kind = "path"
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
    """重新扫描所有来源。来源优先级越后越高，覆盖之前同名。"""
    global _plugins
    with _lock:
        # 不保留旧 module 引用 (避免误用未 reload 的)
        existing_enabled = {p.name for p in _plugins.values() if p.enabled}
        enabled_persist = _load_enabled()
        merged: dict[str, Plugin] = {}
        for mf in _scan_pip() + _scan_npm() + _scan_local() + _scan_project(cwd):
            merged[mf.name] = Plugin(
                manifest=mf,
                enabled=(mf.name in enabled_persist) or (mf.name in existing_enabled),
            )
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


def _import_entrypoints(plugin: Plugin) -> Plugin:
    """In-process 解析 plugin 入口。返回填充了 module / contrib 的 plugin。"""
    mf = plugin.manifest
    ep = dict(mf.entrypoints)
    contrib: dict[str, Any] = {}

    # 1) pip entry_point 的 target (module:obj)
    target = ep.get("_pip_entry") or ep.get("python") or ep.get("module")
    if target and ":" in str(target):
        mod_name, obj_name = str(target).split(":", 1)
        try:
            module = importlib.import_module(mod_name)
            obj = getattr(module, obj_name, module)
            plugin.module = obj
        except Exception as e:
            raise RuntimeError(f"import {target}: {e}") from e

    # 2) 静态贡献 (skills 目录 / web 目录 / sidebar / mcpServers / commands ...)
    base = Path(mf.root) if mf.root else Path.cwd()

    def _abs(p: str) -> str:
        pp = Path(p)
        if not pp.is_absolute():
            pp = (base / pp).resolve()
        return str(pp)

    if isinstance(ep.get("skills"), str):
        contrib["skills"] = _abs(ep["skills"])
    if isinstance(ep.get("web"), str):
        contrib["web"] = _abs(ep["web"])
    for k in ("commands", "agents", "hooks", "mcpServers", "providers"):
        if k in ep:
            v = ep[k]
            if isinstance(v, str):
                contrib[k] = _abs(v)
            else:
                contrib[k] = v

    if mf.sidebar:
        contrib["sidebar"] = list(mf.sidebar)

    # If the plugin declares ``hooks`` as a ``module:name`` reference, resolve
    # it to the actual callable mapping so registry / dispatch can use it.
    hooks_ep = ep.get("hooks")
    if isinstance(hooks_ep, str) and ":" in hooks_ep:
        try:
            mod_name, obj_name = hooks_ep.split(":", 1)
            mod = importlib.import_module(mod_name)
            obj = getattr(mod, obj_name)
            if callable(obj):
                obj = obj()  # factory style
            if isinstance(obj, dict):
                contrib["_hook_map"] = obj
        except Exception:
            pass

    # Materialise plugin-contributed slash commands so the host doesn't
    # have to re-parse every chat tick. Commands can come from either
    # form:
    #   * a dict {name: {description, prompt}} declared inline in the
    #     manifest
    #   * a directory of ``.md`` files (claude-code style), each with
    #     ``name`` / ``description`` frontmatter and the body as the
    #     prompt template
    cmds = ep.get("commands")
    materialised: list[dict[str, str]] = []
    if isinstance(cmds, dict):
        for cname, cdef in cmds.items():
            if isinstance(cdef, dict):
                materialised.append({
                    "name": str(cname),
                    "description": str(cdef.get("description", "")),
                    "prompt": str(cdef.get("prompt", "")),
                })
    elif isinstance(cmds, str):
        # path → walk .md files
        cmd_dir = Path(_abs(cmds))
        if cmd_dir.is_dir():
            for md in sorted(cmd_dir.rglob("*.md")):
                try:
                    text = md.read_text(encoding="utf-8")
                except OSError:
                    continue
                fm, body = _split_frontmatter(text)
                materialised.append({
                    "name": fm.get("name", md.stem),
                    "description": fm.get("description", ""),
                    "prompt": body.strip(),
                })
    if materialised:
        contrib["_commands"] = materialised

    plugin.contrib = contrib
    return plugin


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


def load_plugin(name: str) -> Plugin:
    """加载并启用单个 plugin。trust 不足时拒绝。"""
    paths.sanitize_name(name)
    with _lock:
        p = _plugins.get(name)
        if not p:
            raise KeyError(f"unknown plugin: {name}")

        # trust 检查
        level = _trust.get_trust(name)
        if level == "untrusted":
            p.enabled = False
            p.loaded = False
            p.error = "trust=untrusted; please elevate trust before enabling"
            raise PermissionError(p.error)

        # compatibility
        ok, reason = check_compatibility(p.manifest.compatibility, _HOST_VERSION)
        if not ok:
            p.enabled = False
            p.loaded = False
            p.error = f"incompatible: {reason}"
            raise RuntimeError(p.error)

        # Dependency resolution — every plugin in ``requires`` must be
        # installed AND enabled. We don't auto-enable upstream deps;
        # that would be too magical. We just report the gap.
        missing: list[str] = []
        not_enabled: list[str] = []
        for dep in p.manifest.requires:
            dep_p = _plugins.get(dep)
            if dep_p is None:
                missing.append(dep)
            elif not dep_p.enabled:
                not_enabled.append(dep)
        if missing or not_enabled:
            parts: list[str] = []
            if missing:
                parts.append(f"missing dependencies: {', '.join(missing)}")
            if not_enabled:
                parts.append(f"deps not enabled: {', '.join(not_enabled)}")
            p.enabled = False
            p.loaded = False
            p.error = "; ".join(parts)
            raise RuntimeError(p.error)

        if p.manifest.deprecated:
            # 不阻止加载，但记一行日志/错误位
            p.error = "deprecated"

        try:
            _import_entrypoints(p)
            p.loaded = True
            p.enabled = True
            registry.set_plugin_contrib(name, p.contrib)
            # skills 对接
            if p.contrib.get("skills"):
                registry.try_register_skills(name, p.contrib["skills"])
            # 注册 lifecycle hooks
            hook_map = p.contrib.get("_hook_map") or {}
            if hook_map:
                from . import hooks as _hooks
                _hooks.register_plugin_hooks(name, hook_map)
            # 持久化 enabled
            s = _load_enabled()
            s.add(name)
            _save_enabled(s)
            # 通知所有 plugin: 这个 plugin 上线了
            try:
                from . import hooks as _hooks
                _hooks.dispatch_hook(
                    _hooks.HookEvent.PLUGIN_ENABLE, {"plugin": name},
                )
            except Exception:
                pass
        except Exception as e:
            p.loaded = False
            p.enabled = False
            p.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            _errors[name] = p.error
            raise
        return p


def unload_plugin(name: str) -> Plugin:
    paths.sanitize_name(name)
    with _lock:
        p = _plugins.get(name)
        if not p:
            raise KeyError(f"unknown plugin: {name}")
        # Fire plugin.disable BEFORE we drop registrations so handlers
        # can still see neighbour state.
        try:
            from . import hooks as _hooks
            _hooks.dispatch_hook(
                _hooks.HookEvent.PLUGIN_DISABLE, {"plugin": name},
            )
            _hooks.unregister_plugin_hooks(name)
        except Exception:
            pass
        registry.try_unregister_skills(name)
        registry.clear_plugin_contrib(name)
        # 不主动 del sys.modules：用户可能仍有引用。Reload 时再处理。
        p.loaded = False
        p.enabled = False
        p.module = None
        s = _load_enabled()
        s.discard(name)
        _save_enabled(s)
        return p


def reload_plugin(name: str) -> Plugin:
    paths.sanitize_name(name)
    with _lock:
        unload_plugin(name)
        # 真重新 import：清除子模块缓存
        prefix = f"openprogram_plugin_{name}"
        for mod in list(sys.modules):
            if mod.startswith(prefix):
                sys.modules.pop(mod, None)
        rescan()
        return load_plugin(name)


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
                pass
        return _plugins.get(name)


def all_errors() -> dict[str, str]:
    with _lock:
        out = dict(_errors)
        for p in _plugins.values():
            if p.error:
                out.setdefault(p.name, p.error)
        return out


def clear_error(name: str) -> None:
    with _lock:
        _errors.pop(name, None)

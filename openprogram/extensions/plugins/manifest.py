"""Plugin manifests, read compatibly from the external formats.

A plugin is declarative: skills, slash commands, agents, hooks (shell
commands), MCP servers and model provider directories, described by its
manifest. Any of these forms parses into the same ``PluginManifest``:

1. ``.claude-plugin/plugin.json`` (Claude Code), ``.codex-plugin/plugin.json``
   (Codex) or ``plugin.json`` at the root
2. ``[tool.openprogram.plugin]`` in ``pyproject.toml``
3. the ``openprogram`` / ``opencode`` / ``hermes`` field of ``package.json``
4. ``plugin.yaml`` / ``plugin.yml`` at the root (Hermes)

The first form found wins. Claude Code writes commands / skills / agents /
hooks / mcpServers at the top level of its manifest; they are lifted into
``entrypoints``, so a package needs no edits to be recognised.

A manifest that names code — a Python setup or tool module, a Web module,
a page or sidebar of its own, a Python hook mapping, or a Hermes Python
package — parses, but carries the offending keys in ``code``: installing
and enabling it are refused (:func:`code_refusal`). Code that adds tools or
a UI is an application, installed with ``openprogram programs install``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    deprecated: bool = False
    compatibility: str = ""
    trust: str = "community"
    entrypoints: dict[str, Any] = field(default_factory=dict)
    sidebar: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)
    source_kind: str = ""
    root: str = ""
    manifest_form: str = ""
    #: Keys that name code; a manifest with any is refused (see ``code_refusal``).
    code: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: What a plugin may contain: every key is data the host mounts itself.
DECLARATIVE_KEYS = (
    "commands", "skills", "agents", "hooks", "mcpServers",
    "providers", "lspServers", "outputStyles",
)

#: Keys that name code a plugin would run, or a UI it would serve.
CODE_KEYS = ("python", "module", "setup", "main", "tools", "client", "web")

_CONTRIB_KEYS = DECLARATIVE_KEYS + CODE_KEYS


def code_entries(entrypoints: dict[str, Any], *, sidebar: Any = None, root: str = "",
                 form: str = "") -> list[str]:
    """The manifest keys of a plugin that name code, in a stable order."""
    found = [key for key in CODE_KEYS if entrypoints.get(key) not in (None, "", [], {})]
    hooks = entrypoints.get("hooks")
    if isinstance(hooks, str) and ":" in hooks and not hooks.endswith(".json"):
        found.append("hooks (a Python mapping)")
    if sidebar:
        found.append("sidebar")
    if form == "plugin.yaml" and root and (Path(root) / "__init__.py").is_file():
        found.append("__init__.py (a Hermes Python plugin)")
    return found


class PluginContainsCode(PermissionError):
    """Raised when enabling a plugin whose manifest names code."""


def code_refusal(manifest: "PluginManifest") -> str:
    """Why ``manifest`` cannot be a plugin, or ``""`` when it is declarative."""
    if not manifest.code:
        return ""
    where = manifest.root or "<plugin directory>"
    return (f"plugin {manifest.name} contains code ({', '.join(manifest.code)}); plugins carry "
            "only skills, commands, agents, hooks, MCP servers and provider directories. "
            "Package the code as an application: add an application.json to the directory "
            f"and run `openprogram programs install {where}`.")


def _lift_entrypoints(data: dict[str, Any]) -> dict[str, Any]:
    """Copy top-level contribution fields into entrypoints without clobbering."""
    eps = dict(data.get("entrypoints") or {})
    for key in _CONTRIB_KEYS:
        if key in data and key not in eps and data[key] not in (None, "", [], {}):
            eps[key] = data[key]
    data["entrypoints"] = eps
    return data


def _read_json_file(f: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _from_plugin_json(p: Path) -> dict[str, Any] | None:
    for rel, form in (
        (".claude-plugin/plugin.json", "claude-plugin.json"),
        (".codex-plugin/plugin.json", "codex-plugin.json"),
        ("plugin.json", "plugin.json"),
    ):
        f = p / rel
        if not f.is_file():
            continue
        data = _read_json_file(f)
        if not data:
            continue
        if not (data.get("name") or "").strip():
            data["name"] = p.name
        data["__form__"] = form
        return _lift_entrypoints(data)
    return None


def _from_pyproject(p: Path) -> dict[str, Any] | None:
    f = p / "pyproject.toml"
    if not f.is_file():
        return None
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    section = (data.get("tool", {}) or {}).get("openprogram", {}) or {}
    plug = section.get("plugin") if "plugin" in section else section
    if not isinstance(plug, dict) or not plug:
        return None
    if "name" not in plug:
        proj = data.get("project", {}) or {}
        if isinstance(proj, dict) and proj.get("name"):
            plug["name"] = proj["name"]
            plug.setdefault("version", proj.get("version", "0.0.0"))
            plug.setdefault("description", proj.get("description", ""))
    plug["__form__"] = "pyproject.toml"
    return _lift_entrypoints(plug)


def _from_package_json(p: Path) -> dict[str, Any] | None:
    f = p / "package.json"
    if not f.is_file():
        return None
    data = _read_json_file(f)
    if not data:
        return None
    plug = None
    form = "package.json"
    for key in ("openprogram", "opencode", "hermes"):
        cand = data.get(key)
        if isinstance(cand, dict) and cand:
            plug = dict(cand)
            form = f"package.json#{key}"
            break
    if plug is None:
        return None
    plug.setdefault("name", data.get("name", "") or p.name)
    plug.setdefault("version", data.get("version", "0.0.0"))
    plug.setdefault("description", data.get("description", ""))
    plug["__form__"] = form
    return _lift_entrypoints(plug)


def _from_plugin_yaml(p: Path) -> dict[str, Any] | None:
    f = None
    for name in ("plugin.yaml", "plugin.yml"):
        cand = p / name
        if cand.is_file():
            f = cand
            break
    if f is None:
        return None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not (data.get("name") or "").strip():
        data["name"] = p.name
    data["__form__"] = "plugin.yaml"
    return _lift_entrypoints(data)


def parse_manifest_dir(directory: Path) -> PluginManifest | None:
    if not directory or not Path(directory).is_dir():
        return None
    d = Path(directory)
    for fn in (_from_plugin_json, _from_pyproject, _from_package_json, _from_plugin_yaml):
        data = fn(d)
        if data:
            return _from_dict(data, root=str(d.resolve()))
    return None


def _from_dict(data: dict[str, Any], root: str = "", source_kind: str = "") -> PluginManifest | None:
    name = (data.get("name") or "").strip()
    if not name:
        return None
    raw_req = data.get("requires") or data.get("dependencies") or []
    if isinstance(raw_req, str):
        raw_req = [raw_req]
    requires = [str(x).strip() for x in raw_req if isinstance(x, str) and str(x).strip()]
    desc = data.get("description", "")
    if isinstance(desc, dict):
        desc = desc.get("name") or desc.get("text") or ""
    form = str(data.pop("__form__", ""))
    entrypoints = dict(data.get("entrypoints", {}) or {})
    sidebar = list(data.get("sidebar", []) or [])
    return PluginManifest(
        name=name,
        version=str(data.get("version", "0.0.0")),
        description=str(desc or ""),
        deprecated=bool(data.get("deprecated", False)),
        compatibility=str(data.get("compatibility", "")),
        trust=str(data.get("trust", "community")),
        entrypoints=entrypoints,
        sidebar=sidebar,
        options=dict(data.get("options", {}) or {}),
        requires=requires,
        source_kind=source_kind,
        root=root,
        manifest_form=form,
        code=code_entries(entrypoints, sidebar=sidebar, root=root, form=form),
    )


def check_compatibility(compat: str, current: str) -> tuple[bool, str]:
    if not compat:
        return True, ""
    try:
        op = ""
        ver = compat.strip()
        for cand in (">=", "<=", "==", ">", "<"):
            if ver.startswith(cand):
                op = cand
                ver = ver[len(cand):].strip()
                break
        op = op or ">="

        def tup(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        a = tup(current)
        b = tup(ver)
        cmp = (a > b) - (a < b)
        ok = {
            ">=": cmp >= 0, "<=": cmp <= 0, "==": cmp == 0, ">": cmp > 0, "<": cmp < 0,
        }[op]
        return ok, f"current={current} op={op} required={ver}"
    except Exception as e:
        return False, f"parse error: {e}"

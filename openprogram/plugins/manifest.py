"""统一 manifest 解析。

支持这些声明，任一形式解析成同一份 ``PluginManifest``：
1. ``.claude-plugin/plugin.json`` (Claude Code 现行路径) 或根目录 ``plugin.json``
2. ``pyproject.toml`` 中 ``[tool.openprogram.plugin]``
3. ``package.json`` 中 ``openprogram`` / ``opencode`` / ``hermes`` 字段
4. 根目录 ``plugin.yaml`` / ``plugin.yml`` (Hermes)

解析顺序按上面往下，第一个成功的胜出。

Claude Code 把 commands/skills/agents/hooks/mcpServers 写在清单顶层，
这里会提升进 ``entrypoints``，所以别人的包不用改清单就能被认出来。
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CONTRIB_KEYS = (
    "commands", "skills", "agents", "hooks", "mcpServers",
    "providers", "web", "lspServers", "outputStyles",
)


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
    return PluginManifest(
        name=name,
        version=str(data.get("version", "0.0.0")),
        description=str(desc or ""),
        deprecated=bool(data.get("deprecated", False)),
        compatibility=str(data.get("compatibility", "")),
        trust=str(data.get("trust", "community")),
        entrypoints=dict(data.get("entrypoints", {}) or {}),
        sidebar=list(data.get("sidebar", []) or []),
        options=dict(data.get("options", {}) or {}),
        requires=requires,
        source_kind=source_kind,
        root=root,
        manifest_form=str(data.pop("__form__", "")),
    )


def from_entry_point_metadata(name: str, dist_meta: dict[str, Any], root: str = "") -> PluginManifest:
    return PluginManifest(
        name=name,
        version=str(dist_meta.get("version", "0.0.0")),
        description=str(dist_meta.get("summary", "")),
        source_kind="pip",
        root=root,
        manifest_form="entry_points",
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

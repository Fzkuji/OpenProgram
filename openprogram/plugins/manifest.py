"""统一 manifest 解析。

支持三种声明，任一形式解析成同一份 ``PluginManifest``：
1. ``plugin.json`` (顶级，claude-code / hermes 风格)
2. ``pyproject.toml`` 中 ``[tool.openprogram.plugin]``
3. ``package.json`` 中 ``"openprogram"`` 字段 (opencode 风格)

解析顺序：plugin.json > pyproject.toml > package.json。第一个解析成功的胜出。
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
    trust: str = "community"  # community | verified；untrusted 由 trust.py 控制
    entrypoints: dict[str, Any] = field(default_factory=dict)
    sidebar: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    # 元信息
    source_kind: str = ""    # pip | npm | path | project
    root: str = ""           # 解析时的目录绝对路径
    manifest_form: str = ""  # plugin.json | pyproject.toml | package.json | entry_points

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _from_plugin_json(p: Path) -> dict[str, Any] | None:
    f = p / "plugin.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        data["__form__"] = "plugin.json"
        return data
    except Exception:
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
    # 若 name 缺，尝试 [project].name
    if "name" not in plug:
        proj = data.get("project", {}) or {}
        if isinstance(proj, dict) and proj.get("name"):
            plug["name"] = proj["name"]
            plug.setdefault("version", proj.get("version", "0.0.0"))
            plug.setdefault("description", proj.get("description", ""))
    plug["__form__"] = "pyproject.toml"
    return plug


def _from_package_json(p: Path) -> dict[str, Any] | None:
    f = p / "package.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    plug = data.get("openprogram")
    if not isinstance(plug, dict):
        return None
    plug.setdefault("name", data.get("name", ""))
    plug.setdefault("version", data.get("version", "0.0.0"))
    plug.setdefault("description", data.get("description", ""))
    plug["__form__"] = "package.json"
    return plug


def parse_manifest_dir(directory: Path) -> PluginManifest | None:
    """从目录解析 manifest。三种形式按优先级尝试。"""
    if not directory or not Path(directory).is_dir():
        return None
    d = Path(directory)
    for fn in (_from_plugin_json, _from_pyproject, _from_package_json):
        data = fn(d)
        if data:
            return _from_dict(data, root=str(d.resolve()))
    return None


def _from_dict(data: dict[str, Any], root: str = "", source_kind: str = "") -> PluginManifest | None:
    name = (data.get("name") or "").strip()
    if not name:
        return None
    return PluginManifest(
        name=name,
        version=str(data.get("version", "0.0.0")),
        description=str(data.get("description", "")),
        deprecated=bool(data.get("deprecated", False)),
        compatibility=str(data.get("compatibility", "")),
        trust=str(data.get("trust", "community")),
        entrypoints=dict(data.get("entrypoints", {}) or {}),
        sidebar=list(data.get("sidebar", []) or []),
        options=dict(data.get("options", {}) or {}),
        source_kind=source_kind,
        root=root,
        manifest_form=str(data.pop("__form__", "")),
    )


def from_entry_point_metadata(name: str, dist_meta: dict[str, Any], root: str = "") -> PluginManifest:
    """pip entry_points 来源的兜底 manifest：基本字段从 distribution metadata 填。"""
    return PluginManifest(
        name=name,
        version=str(dist_meta.get("version", "0.0.0")),
        description=str(dist_meta.get("summary", "")),
        source_kind="pip",
        root=root,
        manifest_form="entry_points",
    )


def check_compatibility(compat: str, current: str) -> tuple[bool, str]:
    """简单版本比较：支持 ``>=x.y.z`` / ``==x.y.z`` / 空串 (兼容)。"""
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

"""Marketplace 管理。

持久化 ``~/.openprogram/marketplaces.json``，schema 兼容 claude-code：
list 元素 ``{name, description, source, version, ...}``。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import paths


def _load() -> list[dict[str, Any]]:
    f = paths.marketplaces_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception:
        pass
    return []


def _save(lst: list[dict[str, Any]]) -> None:
    paths.marketplaces_file().write_text(
        json.dumps(lst, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _make_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# Built-in curated plugin catalog. Each entry is a plugin the user can
# install with one click via the existing /api/plugins/install endpoint.
# ``source`` values map to the four installer modes (pip / npm / git / path).
# Stored inline rather than fetched so the marketplace works offline; we
# will swap to a hosted index once one exists.
BUILTIN_PLUGINS: list[dict[str, Any]] = [
    {
        "name": "openprogram-example-dashboard",
        "displayName": "Example Dashboard",
        "description": "Reference plugin that contributes a sidebar entry + standalone web panel. Useful template when authoring your own UI plugin.",
        "source": "git",
        "spec": "https://github.com/openprogram/example-dashboard-plugin",
        "tags": ["template", "ui"],
        "official": True,
    },
    {
        "name": "openprogram-memory-honcho",
        "displayName": "Honcho Memory Adapter",
        "description": "Persist long-term memory across sessions via Honcho. Drop-in alternative to the local wiki memory.",
        "source": "pip",
        "spec": "openprogram-memory-honcho",
        "tags": ["memory", "storage"],
    },
    {
        "name": "openprogram-image-gen",
        "displayName": "Image Generation",
        "description": "Adds image-generation tools (Imagen / Flux / SDXL) callable from any chat session. Ships with API key prompts.",
        "source": "pip",
        "spec": "openprogram-image-gen",
        "tags": ["images", "media"],
    },
    {
        "name": "openprogram-disk-cleanup",
        "displayName": "Disk Cleanup",
        "description": "Sweep the OpenProgram cache + remote-cache + worker logs. Surfaces what's eating disk before deletion.",
        "source": "pip",
        "spec": "openprogram-disk-cleanup",
        "tags": ["maintenance"],
    },
    {
        "name": "openprogram-context-engine",
        "displayName": "Context Engine",
        "description": "Smarter context window: summarises older turns, deduplicates tool outputs, keeps prompts under the model limit.",
        "source": "pip",
        "spec": "openprogram-context-engine",
        "tags": ["context", "compaction"],
    },
]


def builtin_plugins() -> list[dict[str, Any]]:
    """Curated built-in plugin catalog (copy)."""
    return [dict(p) for p in BUILTIN_PLUGINS]


def list_marketplaces() -> list[dict[str, Any]]:
    return _load()


def add_marketplace(url: str, name: str = "") -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise ValueError("empty url")
    existing = _load()
    mid = _make_id(url)
    for m in existing:
        if m.get("id") == mid:
            return m
    entry = {"id": mid, "name": name or url, "url": url}
    existing.append(entry)
    _save(existing)
    return entry


def remove_marketplace(mid: str) -> bool:
    existing = _load()
    kept = [m for m in existing if m.get("id") != mid]
    changed = len(kept) != len(existing)
    if changed:
        _save(kept)
    return changed


def get_marketplace(mid: str) -> dict[str, Any] | None:
    for m in _load():
        if m.get("id") == mid:
            return m
    return None


async def fetch_index(mid: str) -> list[dict[str, Any]]:
    """GET marketplace.url，返回 plugin entry 列表。

    兼容 claude-code marketplace schema：顶级可以是 list 或 ``{plugins: [...]}``。
    """
    m = get_marketplace(mid)
    if not m:
        raise KeyError(mid)
    import httpx
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(m["url"])
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and isinstance(data.get("plugins"), list):
        data = data["plugins"]
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]

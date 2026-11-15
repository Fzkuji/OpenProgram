"""Marketplace 管理。

持久化 ``~/.openprogram/marketplaces.json``，schema 兼容 claude-code：
list 元素 ``{name, description, source, version, ...}``。
"""
from __future__ import annotations

import logging
import hashlib
import json
from typing import Any

from . import paths

_log = logging.getLogger(__name__)


def _load() -> list[dict[str, Any]]:
    f = paths.marketplaces_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except (OSError, ValueError):
        _log.warning("marketplaces file %s unreadable; treating as empty", f,
                     exc_info=True)
    return []


def _save(lst: list[dict[str, Any]]) -> None:
    paths.marketplaces_file().write_text(
        json.dumps(lst, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _make_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# Built-in curated plugin catalog: entries the user can install with one
# click through /api/plugins/install (``source`` is ``npm``, ``git`` or
# ``path``). Stored inline so the marketplace works offline. Empty until a
# declarative plugin is curated; code that adds tools or a UI ships as an
# application instead.
BUILTIN_PLUGINS: list[dict[str, Any]] = []


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
    from openprogram.host.security import safe_http
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

    consumer = "plugins.marketplace"
    async with safe_http.configured_safe_async_client(
        consumer,
        m["url"],
        owner_exception=OwnerURLException(
            consumer=consumer, origin=normalize_origin(m["url"])
        ),
    ) as client:
        r = await client.get(m["url"])
        safe_http.raise_for_status_sanitized(r)
        safe_http.require_json_mime(r)
        data = r.json()
    if isinstance(data, dict) and isinstance(data.get("plugins"), list):
        data = data["plugins"]
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]

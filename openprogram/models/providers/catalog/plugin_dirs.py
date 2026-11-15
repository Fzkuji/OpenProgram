"""Where provider directories live: ``adapters/``, then plugin roots.

A provider is a directory holding ``provider.json``. The shipped ones sit
in ``adapters/`` beside this module; a plugin adds a root of its own
(``entrypoints.providers``) whose child directories are read the same way.
Shipped directories win a name clash, so a plugin can add providers but
never replace one.
"""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Callable, Iterator
import logging

_log = logging.getLogger(__name__)

BUILTIN_ROOT = Path(__file__).parent.parent / "adapters"

_lock = RLock()
_roots: dict[str, Path] = {}   # owner → root holding <provider>/provider.json


def add_root(owner: str, root: str | Path) -> Callable[[], None]:
    """Read providers from ``root`` too. Returns the function that stops."""
    with _lock:
        _roots[owner] = Path(root)
    _invalidate()

    def remove() -> None:
        with _lock:
            if _roots.get(owner) == Path(root):
                _roots.pop(owner, None)
        _invalidate()

    return remove


def roots() -> list[Path]:
    with _lock:
        return [BUILTIN_ROOT, *_roots.values()]


def find(dir_name: str) -> Path | None:
    """The directory called ``dir_name`` in the first root that has it."""
    for root in roots():
        d = root / dir_name
        if d.is_dir():
            return d
    return None


def iter_dirs() -> Iterator[Path]:
    """Every provider-shaped child directory, shipped first, one per name."""
    seen: set[str] = set()
    for root in roots():
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if d.name in seen or not (d / "provider.json").is_file():
                continue
            seen.add(d.name)
            yield d


def _invalidate() -> None:
    for module, name in (("openprogram.models.providers.settings.thinking_spec", "invalidate_cache"),
                         ("openprogram.models.providers.settings.cache_spec", "invalidate_cache")):
        try:
            import importlib
            getattr(importlib.import_module(module), name)()
        except Exception:
            # One stale spec cache must not stop invalidating the others.
            _log.warning("%s.%s failed; spec cache may be stale", module, name, exc_info=True)

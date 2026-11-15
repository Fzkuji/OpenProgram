"""Bounded, source-only explorer cache with filesystem dependency validation."""
from __future__ import annotations

import ast
from collections import OrderedDict
from contextvars import ContextVar
from functools import lru_cache, wraps
import hashlib
import json
from pathlib import Path
from threading import RLock

_context: ContextVar[tuple[dict, dict] | None] = ContextVar("programs_analysis", default=None)


def stamp(path: Path) -> tuple | None:
    try:
        info = path.stat()
        return (str(path.resolve()), info.st_dev, info.st_ino, info.st_mode,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except OSError:
        return None


def watch(path: Path) -> None:
    context = _context.get()
    if context is not None and path not in context[0]:
        context[0][path] = stamp(path)


def request_memo(fn):
    """Reuse catalog discovery within one request, never across catalog owners."""
    @wraps(fn)
    def wrapped(*args):
        context = _context.get()
        if context is None:
            return fn(*args)
        key = (fn, args)
        if key not in context[1]:
            context[1][key] = fn(*args)
        return context[1][key]
    return wrapped


@lru_cache(maxsize=512)
def _parse(path: Path, signature: tuple | None) -> ast.Module:
    # Signature participates in the key; parse failures are intentionally uncached.
    return ast.parse(path.read_text(encoding="utf-8"))


def parsed_source(path: Path) -> ast.Module:
    watch(path)
    return _parse(path, stamp(path))


class CatalogCache:
    def __init__(self):
        self._entries: OrderedDict[tuple, tuple[dict, dict]] = OrderedDict()
        self._lock = RLock()

    def get(self, kind: str, path: str, revision: str, identity, build) -> dict:
        # FastAPI sync endpoints may execute concurrently. Publish only a complete
        # response; serialize discovery to avoid duplicate cold parsing.
        with self._lock:
            dependencies: dict = {}
            token = _context.set((dependencies, {}))
            try:
                key = (identity(), kind, path)
                cached = self._entries.get(key)
                if cached is not None and all(stamp(p) == value for p, value in cached[1].items()):
                    result = cached[0]
                    self._entries.move_to_end(key)
                else:
                    result = build(path)
                    digest = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
                    result = {**result, "revision": digest}
                    # Dependencies sampled before a read must still match after
                    # analysis. An in-flight edit gets rechecked on the next poll.
                    if all(stamp(p) == value for p, value in dependencies.items()):
                        self._entries[key] = (result, dependencies)
                        self._entries.move_to_end(key)
                        while len(self._entries) > 128:
                            self._entries.popitem(last=False)
                if revision == result["revision"]:
                    return {"revision": revision, "unchanged": True}
                return result
            finally:
                _context.reset(token)

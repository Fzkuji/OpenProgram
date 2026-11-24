"""Say what a running Worker's memory holds, when asked to.

A Worker that has grown cannot be measured from outside: the system reports
one number for the process. An empty file ``logs/sample-memory`` under the
state folder asks for a sample. The Worker then counts every object the
collector tracks and writes ``logs/memory-sample.txt``: the process size,
the object types that hold the most, the module-level containers that hold
the most (the caches and registries), and the largest strings with what
holds them. Nothing of a string's content is written, only its length.
"""
from __future__ import annotations

import collections
import gc
import sys
import types
from typing import Any

REQUEST_NAME = "sample-memory"
RESULT_NAME = "memory-sample.txt"
_TYPES = 25
_HOLDERS = 40
_STRINGS = 25
#: Objects followed from one module-level name before its size is cut short.
_WALK_LIMIT = 400_000
_CONTAINERS = (dict, list, tuple, set, frozenset, collections.deque)
_OWN_PACKAGES = ("openprogram",)


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:8.1f} MB"


def process_size() -> int:
    """The process's resident size in bytes, 0 when it cannot be told."""
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001 - psutil is optional
        pass
    try:
        import subprocess
        import os
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.strip()) * 1024
    except Exception:  # noqa: BLE001 - no ps on this system
        return 0


def _children(obj: Any):
    if isinstance(obj, dict):
        yield from obj.keys()
        yield from obj.values()
    elif isinstance(obj, _CONTAINERS):
        yield from obj
    elif type(obj).__module__.partition(".")[0] in _OWN_PACKAGES:
        # Only the product's own objects are opened: a connection or a
        # client object reaches the server, the event loop and from there
        # everything, which says nothing about who holds what.
        fields = getattr(obj, "__dict__", None)
        if isinstance(fields, dict):
            yield fields
        for name in getattr(type(obj), "__slots__", ()) or ():
            if isinstance(name, str) and hasattr(obj, name):
                yield getattr(obj, name)


def deep_size(root: Any, seen: set[int], limit: int = _WALK_LIMIT) -> int:
    """Bytes reachable from ``root`` that ``seen`` has not counted yet.

    Modules, classes and functions are not followed: a container that names
    one does not hold it.
    """
    skip = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType)
    total, stack, walked = 0, [root], 0
    while stack and walked < limit:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, skip):
            continue
        seen.add(id(obj))
        walked += 1
        try:
            total += sys.getsizeof(obj)
        except TypeError:
            continue
        if not isinstance(obj, (str, bytes, bytearray, int, float, bool, type(None))):
            stack.extend(_children(obj))
    return total


def _by_type(objects: list) -> list[str]:
    sizes: collections.Counter[str] = collections.Counter()
    counts: collections.Counter[str] = collections.Counter()
    for obj in objects:
        name = f"{type(obj).__module__}.{type(obj).__qualname__}"
        counts[name] += 1
        try:
            sizes[name] += sys.getsizeof(obj)
        except TypeError:
            pass
    return [f"{_megabytes(size)}  {counts[name]:9d}  {name}" for name, size in sizes.most_common(_TYPES)]


def _by_holder(prefix: str) -> list[str]:
    """Module-level names under ``prefix`` by what they reach, largest first.

    Each name is measured on its own, so two names that reach the same
    objects both count them: a registry that holds a store is as large as
    the store. The second number is what no name before it had reached.
    """
    found = []
    seen: set[int] = set()
    for module_name, module in sorted(sys.modules.items()):
        if not module_name.startswith(prefix) or module is None:
            continue
        for name, value in list(vars(module).items()):
            if name.startswith("__") or not (isinstance(value, _CONTAINERS) or hasattr(value, "__dict__")):
                continue
            if isinstance(value, type) or callable(value) or type(value).__name__ == "module":
                continue
            size = deep_size(value, set())
            if size >= 200_000:
                found.append((size, deep_size(value, seen), f"{module_name}.{name}"))
    found.sort(reverse=True)
    return [f"{_megabytes(size)} {_megabytes(alone)}  {name}" for size, alone, name in found[:_HOLDERS]]


def _describe(holder: Any, held: Any) -> str:
    if isinstance(holder, dict):
        key = next((k for k, v in holder.items() if v is held), None)
        return f"dict[{key!r}]"[:60] if isinstance(key, str) else "dict"
    return f"{type(holder).__module__}.{type(holder).__qualname__}"


def _holder_of(obj: Any, objects_ids: set[int], depth: int = 4) -> list[str]:
    """The chain of tracked objects that holds ``obj``, nearest first."""
    chain = []
    for _ in range(depth):
        holder = next((h for h in gc.get_referrers(obj)
                       if id(h) in objects_ids and not isinstance(h, types.FrameType)), None)
        if holder is None:
            break
        chain.append(_describe(holder, obj))
        obj = holder
    return chain


def _strings(objects: list) -> list[str]:
    """The largest strings and byte strings, by the chain of objects that holds them."""
    # id -> (size, the containers from the tracked one down to the string's own)
    best: dict[int, tuple[int, tuple]] = {}
    pending = [(obj, (obj,)) for obj in objects if isinstance(obj, (dict, list, tuple))]
    while pending:
        holder, path = pending.pop()
        values = holder.values() if isinstance(holder, dict) else holder
        for value in values:
            if isinstance(value, (str, bytes, bytearray)):
                if len(value) >= 50_000:
                    best[id(value)] = (sys.getsizeof(value), (*path, value))
            elif isinstance(value, (dict, list, tuple)) and not gc.is_tracked(value):
                # A container of plain values is not tracked by the
                # collector, so it is reached only through what holds it.
                pending.append((value, (*path, value)))
    total = sum(size for size, _ in best.values())
    ids = {id(obj) for obj in objects}
    chains: collections.Counter[str] = collections.Counter()
    counts: collections.Counter[str] = collections.Counter()
    for size, path in sorted(best.values(), key=lambda item: item[0], reverse=True)[:400]:
        inner = [_describe(path[i], path[i + 1]) for i in range(len(path) - 2, -1, -1)]
        chain = " < ".join([*inner, *_holder_of(path[0], ids)])
        chains[chain] += size
        counts[chain] += 1
    lines = [f"{_megabytes(total)} in {len(best)} strings of 50,000 characters or more, by what holds them:"]
    lines += [f"{_megabytes(size)}  {counts[chain]:5d}  {chain}" for chain, size in chains.most_common(_STRINGS)]
    return lines


def _sessions_in_memory() -> list[str]:
    """Cached sessions by the call that read them in, with their size."""
    try:
        from openprogram.state.store.session.session_store import shared
        store = shared._default_store
        if store is None:
            return ["the session store was never opened"]
        with store._lock:
            held = dict(store._sessions)
            openers = dict(store._opened_by)
    except Exception:  # noqa: BLE001 - a diagnostic never fails the sample
        return ["the session store could not be read"]
    seen: set[int] = set()
    sizes: collections.Counter[str] = collections.Counter()
    counts: collections.Counter[str] = collections.Counter()
    largest = []
    for session_id, pair in held.items():
        size = deep_size(pair, seen)
        opener = openers.get(session_id, "?")
        sizes[opener] += size
        counts[opener] += 1
        largest.append((size, session_id))
    lines = [f"{_megabytes(sum(sizes.values()))} in {len(held)} sessions, by the call that read them in:"]
    lines += [f"{_megabytes(size)}  {counts[opener]:5d}  {opener}" for opener, size in sizes.most_common(20)]
    lines += ["the largest:"] + [f"{_megabytes(size)}  {session_id}"
                                for size, session_id in sorted(largest, reverse=True)[:8]]
    return lines


def _run_tables_in_memory() -> list[str]:
    """Sessions whose run tables are loaded, by the call that read them in."""
    try:
        from openprogram.runtime.execution.store import run_tables
        lines = []
        for tables in list(run_tables._tables_by_root.values()):
            with tables.lock:
                loaded = dict(tables._loaded_by)
            callers = collections.Counter(loaded.values())
            lines.append(f"{len(loaded)} sessions, by the call that read them in:")
            lines += [f"{count:5d}  {caller}" for caller, count in callers.most_common(20)]
        return lines or ["no run tables were opened"]
    except Exception:  # noqa: BLE001 - a diagnostic never fails the sample
        return ["the run tables could not be read"]


def take_sample() -> str:
    """One look at what the process holds."""
    gc.collect()
    objects = gc.get_objects()
    lines = [f"process {_megabytes(process_size()).strip()}; {len(objects)} tracked objects",
             "", "By type (size of the objects themselves, count, type):", *_by_type(objects),
             "", "By module-level name (what it reaches, then what nothing listed alphabetically before it reaches):", *_by_holder("openprogram"),
             "", "Sessions held in memory:", *_sessions_in_memory(),
             "", "Run tables in memory:", *_run_tables_in_memory(),
             "", "Largest strings:", *_strings(objects)]
    return "\n".join(lines) + "\n"

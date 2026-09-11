"""Lazily own a Runtime for standalone model operations."""
from contextlib import contextmanager


@contextmanager
def runtime_scope():
    """Reuse the caller Runtime, or close a newly owned Runtime on every exit."""
    from openprogram.agentic_programming.function import _current_runtime
    from openprogram.providers.registry import create_runtime

    current = _current_runtime.get(None)
    if current is not None:
        yield current
        return
    owned = create_runtime()
    token = _current_runtime.set(owned)
    try:
        yield owned
    finally:
        _current_runtime.reset(token)
        owned.close()

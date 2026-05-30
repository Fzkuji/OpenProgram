"""Session storage — one git repo per conversation.

Group ① of ``store/`` (see ``store/README.md``). The original job of the
package: persist the conversation DAG as a per-session git repo and serve
the ``SessionStore`` query API over it.

Modules:
  * ``session_store``    — SessionStore: the public 22-method API + the
                           process-wide ``default_store()`` singleton.
  * ``git_session``      — GitSession: thin wrapper over the ``git`` CLI
                           for one session repo.
  * ``memory_index``     — SessionMemoryIndex: in-memory DAG index.
  * ``graphstore_shim``  — back-compat shim for the old GraphStore API.
  * ``_msg_adapter``     — message-dict ⇄ Call-node translation.
  * ``search``           — cross-session message search (ripgrep).

Importable straight from this sub-package, e.g.
``from openprogram.store.session import SessionStore``.
"""
from .git_session import GitSession
from .memory_index import SessionMemoryIndex
from .graphstore_shim import GraphStoreShim


def __getattr__(name):
    # Lazy: SessionStore + default_store pull in session_store (and thus
    # the heavier import chain) only when actually referenced.
    if name == "SessionStore":
        from .session_store import SessionStore as _S
        return _S
    if name == "default_store":
        from .session_store import default_store as _ds
        return _ds
    raise AttributeError(name)


__all__ = [
    "GitSession",
    "SessionMemoryIndex",
    "GraphStoreShim",
    "SessionStore",
    "default_store",
]

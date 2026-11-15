"""Session storage — one append-only log per conversation.

Group ① of ``store/`` (see ``store/README.md``). The original job of the
package: persist the conversation DAG as each session's log and serve
the ``SessionStore`` query API over it.

Modules:
  * ``session_store``    — SessionStore: the public 22-method API + the
                           process-wide ``default_store()`` singleton.
  * ``session_log``      — SessionLog: one session's append-only records.
  * ``memory_index``     — SessionMemoryIndex: in-memory DAG index.
  * ``session_node_writer``  — by-node DAG writes onto one session.
  * ``_msg_adapter``     — message-dict ⇄ Call-node translation.
  * ``search``           — cross-session message search (ripgrep).

Importable straight from this sub-package, e.g.
``from openprogram.state.store.session import SessionStore``.
"""
from .session_log import SessionLog
from .memory_index import SessionMemoryIndex
from .session_node_writer import SessionNodeWriter
# Provenance read-layer — the LLM-free seam memory maps from (cheap to
# import: only pulls in the node schema, already loaded by memory_index).
from .provenance import (
    Provenance,
    iter_nodes_since,
    node_provenance,
    project_commits,
    session_project_id,
)


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
    "SessionLog",
    "SessionMemoryIndex",
    "SessionNodeWriter",
    "SessionStore",
    "default_store",
    "Provenance",
    "iter_nodes_since",
    "node_provenance",
    "project_commits",
    "session_project_id",
]

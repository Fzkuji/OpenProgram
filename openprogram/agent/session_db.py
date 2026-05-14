"""session_db — SessionDB facade over the flat-DAG store.

``SessionDB`` is now an alias for :class:`DagSessionDB`. The legacy
hand-rolled SQLite schema (messages / branches / parent_id chains)
was retired; persistence is now a flat DAG (UserMessage / ModelCall
/ FunctionCall nodes linked by ``predecessor``). The public method
surface used by ``dispatcher``, channels, and the WebUI is preserved
by the adapter — see ``context.session_db``.
"""

from __future__ import annotations

import threading
from typing import Optional

from openprogram.context.session_db import DagSessionDB


# Public alias: existing callers do ``from openprogram.agent.session_db
# import SessionDB`` and instantiate ``SessionDB(path)``. DagSessionDB
# accepts the same positional ``db_path`` argument.
SessionDB = DagSessionDB


_default: Optional[DagSessionDB] = None
_default_lock = threading.Lock()


def default_db() -> DagSessionDB:
    """Process-wide singleton. Channels worker + webui server share
    this instance."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = DagSessionDB()
    return _default


__all__ = ["SessionDB", "default_db"]

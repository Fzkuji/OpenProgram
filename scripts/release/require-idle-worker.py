#!/usr/bin/env python3
"""Refuse a local App refresh while a session or DAG node is running."""

from __future__ import annotations

import sys
from typing import Any


def active_run_ids(store: Any) -> list[str]:
    active: list[str] = []
    for session in store.list_sessions(limit=10**9, include_archived=True):
        session_id = str(session.get("id") or "")
        if session.get("status") == "running":
            active.append(f"session:{session_id}")
        for node in store.get_nodes(session_id):
            if (getattr(node, "metadata", None) or {}).get("status") == "running":
                active.append(f"node:{session_id}/{node.id}")
    return active


def main() -> int:
    from openprogram.agent.session_db import default_db

    active = active_run_ids(default_db())
    if not active:
        return 0
    print(
        "OpenProgram App refresh refused: active run(s): " + ", ".join(active),
        file=sys.stderr,
    )
    print("Wait for them to finish or stop them in the App, then retry.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Invariant checker for the function-call DAG model.

Run AFTER one fn-form (manual) @agentic_function call (e.g. gui_agent)
has completed in a session, then point this at that session id. It
reads the persisted SessionStore nodes directly and asserts the
invariants from the REFACTOR-CHARTER (I1..I9). No business code is
touched — pure read-only audit.

Usage:
    openprogram worker restart            # MANDATORY first (spawn/long-lived worker)
    # ... run one fn-form call in the UI, let it finish ...
    python docs/design/runtime/verify_fn_call_invariants.py <session_id>

Exit code 0 = all invariants hold; 1 = a violation was found.
"""
from __future__ import annotations

import sys


def check(session_id: str) -> int:
    from openprogram.agent.session_db import default_db

    db = default_db()
    db.invalidate_cache(session_id)          # read on-disk truth, not stale worker cache
    nodes = db.get_nodes(session_id)         # list[Call]
    sess = db.get_session(session_id) or {}
    head_id = sess.get("head_id")

    by_id = {n.id: n for n in nodes}
    fails: list[str] = []

    # I1: no node carries display=runtime
    rt = [n.id for n in nodes if (n.metadata or {}).get("display") == "runtime"]
    if rt:
        fails.append(f"I1 FAIL: display=runtime nodes present: {rt}")

    # I2: no anchor (role=user + display=runtime) / placeholder
    #     (role=assistant/llm + type=status + display=runtime).
    #     Covered by I1 for display=runtime; also assert no type=status row.
    status_rows = [n.id for n in nodes if (n.metadata or {}).get("type") == "status"]
    if status_rows:
        fails.append(f"I2 FAIL: type=status scaffold rows present: {status_rows}")

    # I3: exactly one ROOT (id=="ROOT", called_by=="")
    roots = [n for n in nodes if n.id == "ROOT"]
    if len(roots) != 1:
        fails.append(f"I3 FAIL: expected exactly one id=='ROOT', got {len(roots)}")
    elif roots[0].called_by != "":
        fails.append(f"I3 FAIL: ROOT.called_by != '' (got {roots[0].called_by!r})")

    # I4/I5/I6: every code node's called_by is either ROOT (manual),
    # an llm node (LLM tool_use), or another code node (nested).
    for n in nodes:
        if not n.is_code():
            continue
        cb = n.called_by
        if cb == "ROOT":
            continue                          # I4 manual fn-form
        parent = by_id.get(cb)
        if parent is None:
            fails.append(f"I5/I6 FAIL: code {n.id} called_by={cb!r} not in graph")
        elif parent.is_llm():
            continue                          # I5 LLM tool_use
        elif parent.is_code():
            continue                          # I6 nested
        else:
            fails.append(
                f"I5/I6 FAIL: code {n.id} called_by points at "
                f"role={parent.role!r} (must be llm or code or ROOT)"
            )

    # I7: every node reachable from ROOT via called_by (single connected root)
    reachable: set[str] = set()
    children: dict[str, list[str]] = {}
    for n in nodes:
        if n.called_by:
            children.setdefault(n.called_by, []).append(n.id)
    stack = ["ROOT"]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(children.get(cur, []))
    unreachable = [n.id for n in nodes if n.id not in reachable]
    if unreachable:
        fails.append(f"I7 FAIL: unreachable from ROOT: {unreachable}")

    # I8: head_id points at a real node (never dangling)
    if head_id and head_id not in by_id:
        fails.append(f"I8 FAIL: head_id={head_id!r} not a real node")

    print(f"session={session_id} nodes={len(nodes)} head_id={head_id}")
    for n in sorted(nodes, key=lambda x: x.seq):
        print(f"  seq={n.seq:>3} id={n.id:<14} role={n.role:<5} "
              f"name={(n.name or '')[:20]:<20} called_by={n.called_by!r}")

    if fails:
        print("\n".join(["", "INVARIANT VIOLATIONS:"] + fails))
        return 1
    print("\nALL INVARIANTS HOLD (I1-I8).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python verify_fn_call_invariants.py <session_id>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(check(sys.argv[1]))

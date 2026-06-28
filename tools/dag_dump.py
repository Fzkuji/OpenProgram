#!/usr/bin/env python3
"""Dump a session's DAG layout to the terminal — verify rendering without
the browser.

Usage:
    python tools/dag_dump.py <session_id>
    python tools/dag_dump.py            # newest session

Shows, per node: id / role / caller / predecessor / lane / tier / depth,
plus an ASCII grid placing each node at (lane+tier, depth) — the same
coordinates the web viewport uses (graph_builder → annotate_graph).
"""
import os
import sys


def _newest_session() -> str | None:
    root = os.path.expanduser("~/.openprogram/sessions")
    if not os.path.isdir(root):
        return None
    cands = [
        d for d in os.listdir(root)
        if d.startswith("local_") and os.path.isdir(os.path.join(root, d))
    ]
    if not cands:
        return None
    cands.sort(key=lambda d: os.path.getmtime(os.path.join(root, d)), reverse=True)
    return cands[0]


def main() -> int:
    sid = sys.argv[1] if len(sys.argv) > 1 else _newest_session()
    if not sid:
        print("no session found", file=sys.stderr)
        return 1

    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui.graph_builder import build_session_graph

    store = SessionStore()
    pair = store._open(sid)
    if pair is None:
        print(f"session not found: {sid}", file=sys.stderr)
        return 1
    _git, idx = pair
    head = idx.head_id

    graph = build_session_graph(sid, head)

    print(f"session: {sid}")
    print(f"head:    {head}")
    print()
    # Field table
    print(f"{'id':16} {'role':10} {'caller':14} {'predecessor':14} {'lane':>4} {'tier':>4} {'depth':>5}")
    print("-" * 74)
    for n in graph:
        print(
            f"{(n.get('id') or '')[:16]:16} "
            f"{(n.get('role') or '')[:10]:10} "
            f"{str(n.get('caller') or '')[:14]:14} "
            f"{str(n.get('predecessor') or '')[:14]:14} "
            f"{n.get('_lane', '?'):>4} "
            f"{n.get('_tier', '?'):>4} "
            f"{n.get('_depth', '?'):>5}"
        )

    # ASCII grid: x = lane-block-col + tier, y = depth. Mirrors the web
    # viewport's pos() so the shape on screen matches this.
    print()
    print("layout grid (x = lane+tier column, y = depth row):")
    # compute lane→col offset like pipeline.ts does
    lane_tiers: dict[int, int] = {}
    for n in graph:
        ln = n.get("_lane", 0) or 0
        t = n.get("_tier", 0) or 0
        lane_tiers[ln] = max(lane_tiers.get(ln, 0), t)
    lane_col: dict[int, int] = {}
    col = 0
    for ln in sorted(lane_tiers):
        lane_col[ln] = col
        col += lane_tiers[ln] + 2  # +1 tier width, +1 gap

    shape = {"user": "○", "assistant": "△", "llm": "△", "tool": "■", "code": "■"}
    rows: dict[int, dict[int, str]] = {}
    width = col + 2
    for n in graph:
        ln = n.get("_lane", 0) or 0
        t = n.get("_tier", 0) or 0
        d = n.get("_depth", 0) or 0
        x = lane_col.get(ln, 0) + t
        disp = (n.get("display") or "")
        sym = "◇" if disp == "root" else shape.get(n.get("role") or "", "?")
        rows.setdefault(d, {})[x] = sym + " " + (n.get("id") or "")[:8]
    for d in sorted(rows):
        line = [" " * 12] * width
        for x, label in rows[d].items():
            if x < width:
                line[x] = f"{label:12}"
        print(f"  d{d:<2} " + "".join(line).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

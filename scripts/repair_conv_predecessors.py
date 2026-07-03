"""One-time repair: back-link continuation user turns whose conv
predecessor was lost (written empty) by the pre-fix webui / dispatcher
write paths.

The bug: webui user turns were persisted with ``caller=ROOT`` but no
``metadata.predecessor`` (the field was never populated), so every
2nd+ user turn became a disconnected pseudo-root — the DAG split into
one tree per turn and the Branches panel showed a fake branch per
turn. This script re-links each such continuation turn to the previous
turn's assistant reply.

It only touches NORMAL continuation turns. Nodes that legitimately have
no conv predecessor are left untouched:
  * ``metadata.display == "root"``            — the session ROOT
  * ``metadata.source == "agent_spawn"``      — a sub-agent's own root
  * a user node whose ``caller`` points at a real node (not "" / ROOT)
    — a spawned / sibling fork root

Idempotent (a node that already has a predecessor is skipped) and
dry-run by default.

Usage::

    python -m scripts.repair_conv_predecessors            # dry-run
    python -m scripts.repair_conv_predecessors --apply    # write

The worker must be stopped (or the session cache invalidated) before
``--apply`` so the in-memory index is rebuilt from the repaired files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _sessions_root() -> Path:
    from openprogram.store.session.session_store import _default_root
    return _default_root()


def _load_history(sdir: Path) -> list[tuple[Path, dict]]:
    hdir = sdir / "history"
    if not hdir.is_dir():
        return []
    out: list[tuple[Path, dict]] = []
    for f in sorted(hdir.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda fp: fp[1].get("seq", 0))
    return out


def _pred(node: dict) -> str:
    meta = node.get("metadata") or {}
    return meta.get("predecessor") or node.get("predecessor") or ""


def _caller(node: dict) -> str:
    return node.get("caller") or ""


def _is_root_level(node: dict) -> bool:
    """True if the node hangs directly off ROOT (a normal turn), not off
    a spawning node (a fork root)."""
    return _caller(node) in ("", "ROOT")


def repair_session(sdir: Path, apply: bool) -> list[str]:
    nodes = _load_history(sdir)
    if not nodes:
        return []
    # Count existing conv children so we can tell "reply already has a
    # continuation / fork" apart from "reply is the natural previous
    # turn to link to".
    conv_children: dict[str, int] = {}
    for _f, n in nodes:
        p = _pred(n)
        if p:
            conv_children[p] = conv_children.get(p, 0) + 1

    ordered = [n for _f, n in nodes]
    fixes: list[str] = []
    for i, (fpath, node) in enumerate(nodes):
        if node.get("role") != "user":
            continue
        if _pred(node):
            continue  # already linked — idempotent
        meta = node.get("metadata") or {}
        if meta.get("display") == "root":
            continue
        if meta.get("source") == "agent_spawn":
            continue
        if not _is_root_level(node):
            continue  # spawn / sibling fork root — caller points elsewhere
        # Nearest prior ROOT-level assistant reply. Only link if that
        # reply has no conv child yet (else it's a real fork point and
        # linking here would cross into another branch).
        target = None
        for j in range(i - 1, -1, -1):
            prev = ordered[j]
            if prev.get("role") in ("assistant", "llm") and _is_root_level(prev):
                if conv_children.get(prev.get("id"), 0) == 0:
                    target = prev
                break  # nearest reply only
        if target is None:
            continue
        tid = target.get("id")
        node.setdefault("metadata", {})["predecessor"] = tid
        conv_children[tid] = conv_children.get(tid, 0) + 1
        fixes.append(f"{sdir.name}: {node.get('id')} (seq={node.get('seq')}) -> predecessor={tid}")
        if apply:
            tmp = fpath.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(node, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(fpath)
    return fixes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run)")
    args = ap.parse_args()
    root = _sessions_root()
    total: list[str] = []
    for sdir in sorted(root.iterdir()):
        if not sdir.is_dir() or not (sdir / "history").is_dir():
            continue
        total.extend(repair_session(sdir, args.apply))
    verb = "APPLIED" if args.apply else "WOULD FIX (dry-run)"
    for line in total:
        print(f"  {line}")
    print(f"{verb}: {len(total)} node(s)")
    if not args.apply and total:
        print("Re-run with --apply to write. Stop the worker first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

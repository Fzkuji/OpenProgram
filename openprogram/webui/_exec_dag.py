"""Execution-DAG: reconstruction, live streaming, run-state repair.

Three concerns, all over the flat DAG that ``@agentic_function`` and
the runtime write into SessionDB as a ``/run`` executes:

  ``build_exec_dag()`` — turn a run's DAG subtree into the TNode dict
      the inline Execution DAG renders (``web/.../execution-dag.tsx``).
  ``live_progress()``  — context manager: while a run executes, poll
      the DAG and push ``tree_update`` + ``branches_list`` envelopes so
      the UI fills in node by node instead of only after the run ends.
  ``reconcile_interrupted_runs()`` — on worker startup, flip nodes left
      frozen at ``status="running"`` (their executing process died) to
      ``error``, so the UI shows a failed run, not an eternal spinner.

This is the WebUI-side replacement for the retired tree-Context event
pipeline (commit "cut over to DAG, drop tree-Context event pipeline").
That pipeline forwarded partial trees live; dropping it left a run
showing nothing but a spinner until completion. The poller restores
the live view by reading the DAG — the single source of truth — rather
than re-introducing an event system.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Optional


# ── Tree reconstruction ──────────────────────────────────────────────

def build_exec_dag(session_id: str, func_name: str,
                    user_turn_id: str) -> Optional[dict]:
    """Reconstruct a run's execution DAG from its DAG nodes.

    Returns a TNode dict rooted at the ``func_name`` call this run
    triggered, with its nested function / LLM calls as children.

    Works both *after* a run (the top ``func_name`` node is persisted)
    and *mid-run*: the ``@agentic_function`` wrapper persists a node
    only on return, so while the top function is still looping its node
    does not exist yet — but its nested calls (``gui_step`` etc.) are
    already persisted, pointing at the top node's allocated-but-
    unwritten id. In that case a synthetic ``running`` root is returned.
    None if the run left no nodes at all.
    """
    try:
        from openprogram.agent.session_db import default_db
        nodes_list = default_db().get_nodes(session_id)
    except Exception:
        return None
    nodes = sorted(nodes_list, key=lambda n: n.seq)
    by_id = {n.id: n for n in nodes}
    kids: dict[str, list] = {}
    for n in nodes:
        if n.called_by:
            kids.setdefault(n.called_by, []).append(n)

    # Root: the func_name code call this run's user turn triggered.
    # Last match wins so a re-run picks the most recent invocation.
    root = None
    for n in nodes:
        if n.is_code() and n.name == func_name and n.called_by == user_turn_id:
            root = n

    def _to_tnode(n) -> dict:
        meta = n.metadata or {}
        status = meta.get("status") or "success"
        tn: dict = {
            "path": n.id,
            "name": n.name or (n.role or "node"),
            "status": status,
        }
        dur = meta.get("duration_seconds")
        if dur is not None:
            try:
                tn["duration_ms"] = int(float(dur) * 1000)
            except (TypeError, ValueError):
                pass
        if status == "error":
            tn["error"] = str(n.output or meta.get("error") or "")
        if n.is_llm():
            # exec rows render params._content (prompt) + raw_reply.
            tn["node_type"] = "exec"
            inp = n.input
            if isinstance(inp, (list, dict)):
                inp = json.dumps(inp, default=str)
            tn["params"] = {"_content": str(inp or "")}
            tn["raw_reply"] = str(n.output or "")
        else:
            if isinstance(n.input, dict):
                tn["params"] = {k: v for k, v in n.input.items()
                                if k not in ("runtime", "callback")}
            out = n.output
            tn["output"] = (out if isinstance(out, str)
                            else json.dumps(out, default=str))
        children = [_to_tnode(c)
                    for c in sorted(kids.get(n.id, []), key=lambda x: x.seq)]
        if children:
            tn["children"] = children
        return tn

    if root is not None:
        return _to_tnode(root)

    # Mid-run: the top func_name node isn't persisted yet. Its direct
    # children already carry its allocated id in ``called_by`` — so they
    # look like orphans (called_by → an id not in the graph). Collect
    # them, but only ones created at/after this run's user turn, so
    # stale orphans from old deleted branches aren't swept in.
    turn = by_id.get(user_turn_id)
    floor = (turn.created_at or 0.0) if turn else 0.0
    orphan_children = [
        n for n in nodes
        if n.called_by and n.called_by not in by_id
        and not n.is_user()
        and (n.created_at or 0.0) >= floor
    ]
    if not orphan_children:
        return None
    children = [_to_tnode(c)
                for c in sorted(orphan_children, key=lambda x: x.seq)]
    return {
        "path": user_turn_id + "_run",
        "name": func_name,
        "status": "running",
        "children": children,
    }


# ── Live progress streaming ──────────────────────────────────────────

def _poll(session_id: str, msg_id: str, func_name: str,
          stop: threading.Event) -> None:
    """Poll the DAG every ~1.2s and push two live streams until
    ``stop`` is set: ``tree_update`` (inline Execution DAG) and
    ``branches_list`` (right-rail History graph). Both are
    signature-deduped so an idle tick sends nothing."""
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.branch import build_branches_payload

    last_tree = None
    last_graph = None
    # streaming-resume: also patch the persisted placeholder reply with
    # the latest tree so a mid-run page refresh sees the in-progress
    # Execution DAG, not an empty ``gui_agent()`` shell. The reply
    # placeholder lives at ``msg_id + "_reply"`` (see _execute/run.py).
    _placeholder_id = msg_id + "_reply"
    _shim = None
    while not stop.wait(1.2):
        try:
            tree = build_exec_dag(session_id, func_name, msg_id)
            if tree is not None:
                sig = json.dumps(tree, default=str, sort_keys=True)
                if sig != last_tree:
                    last_tree = sig
                    _s._broadcast_chat_response(session_id, msg_id, {
                        "type": "tree_update",
                        "tree": tree,
                        "function": func_name,
                    })
                    # Throttled persist: write the latest tree onto the
                    # placeholder so a refresh-after-crash also recovers
                    # the partial view. Cheap — the index is in memory,
                    # write touches one JSON file + git add (commit
                    # happens at turn end).
                    try:
                        if _shim is None:
                            from openprogram.store import GraphStoreShim
                            from openprogram.agent.session_db import default_db
                            _shim = GraphStoreShim(default_db(), session_id)
                        _shim.update(
                            _placeholder_id,
                            metadata={
                                "status": "running",
                                "context_tree": tree,
                                "last_update_at": time.time(),
                            },
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            payload = build_branches_payload(session_id)
            gsig = json.dumps(payload.get("graph"), default=str, sort_keys=True)
            if gsig != last_graph:
                last_graph = gsig
                _s._broadcast(json.dumps(
                    {"type": "branches_list", "data": payload}, default=str))
        except Exception:
            pass


@contextmanager
def live_progress(session_id: str, msg_id: str, func_name: str):
    """Stream a run's progress to the UI for the duration of the block.

    Usage::

        with live_progress(session_id, msg_id, func_name):
            result = loaded_func(**call_kwargs)

    A daemon poller thread starts on enter and stops on exit (success
    or exception), so a long ``@agentic_function`` run shows its
    Execution DAG + History graph filling in live.
    """
    stop = threading.Event()
    thread = threading.Thread(
        target=_poll, args=(session_id, msg_id, func_name, stop),
        daemon=True, name=f"live-progress-{session_id}")
    thread.start()
    try:
        yield
    finally:
        stop.set()


# ── Interrupted-run repair ───────────────────────────────────────────

def reconcile_interrupted_runs() -> int:
    """Flip every DAG node still at ``status="running"`` to ``"interrupted"``.

    Two writers stamp ``status="running"``:
      * ``@agentic_function`` on entry (FunctionCall sub-call nodes),
        flipping to ``success`` / ``error`` in its ``finally``.
      * The chat dispatcher on assistant placeholder insert (step 3b),
        flipping to ``completed`` / ``error`` / ``cancelled`` at turn
        end.
    If the worker process dies before either path's terminal flip runs
    (SIGKILL, crash, restart), the node is frozen at ``running`` and
    the UI spins forever waiting on a terminal event nobody will fire.

    Call once on worker startup: a fresh worker has nothing running,
    so any ``running`` node is a zombie from a dead process. We tag
    these as ``interrupted`` rather than ``error`` so the UI can
    distinguish "the model / network blew up" (red) from "the worker
    got restarted mid-turn" (amber). We also stuff a short marker
    into ``output`` so an otherwise-empty assistant bubble doesn't
    render as a silent ghost — the user sees explicitly what
    happened.

    Returns the count fixed.
    """
    from openprogram.agent.session_db import default_db
    from openprogram.store import GraphStoreShim

    store = default_db()
    fixed = 0
    # Walk every session's nodes; the in-memory index already knows
    # them. For any node whose metadata.status == "running", flip to
    # "interrupted" via GraphStoreShim.update which rewrites the
    # on-disk JSON.
    for sess in store.list_sessions(limit=10**9):
        sid = sess["id"]
        shim = GraphStoreShim(store, sid)
        for node in store.get_nodes(sid):
            meta = node.metadata or {}
            if meta.get("status") != "running":
                continue
            new_meta = dict(meta)
            new_meta["status"] = "interrupted"
            new_meta.setdefault(
                "error", "Worker restarted before this turn finished",
            )
            new_meta["interrupted_at"] = time.time()
            output = node.output
            if not output:
                output = "[interrupted] worker restarted mid-turn"
            try:
                shim.update(node.id, output=output, metadata=new_meta)
                fixed += 1
            except Exception:
                continue
    return fixed

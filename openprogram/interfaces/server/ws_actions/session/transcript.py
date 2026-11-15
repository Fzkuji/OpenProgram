"""Shaping a loaded branch into transcript rows: spawn origins, compaction rows, runtime cards."""
from __future__ import annotations

import json

from openprogram.interfaces.server.transcript.legacy import parsed_extra


def _annotate_spawn_origin(graph: list[dict], messages: list[dict] | None = None) -> None:
    """Attach ``spawned_from`` to each ``source=agent_spawn`` user msg
    that's the root of a sub-branch.

    The field is a dict ``{caller_id, caller_branch, caller_session_id,
    label}`` pointing at the main-lane turn that produced the sub
    branch (so the frontend can render a "Spawned from" card with a
    Switch button mirroring the main-lane AttachCard).

    Discovery: scan attach pointer nodes for ``attach_ref`` → sub
    branch tip. Walk predecessor back from the tip to reach the
    sub-branch root. The attach node's own ``predecessor`` (= the main
    LLM reply that ran the task() tool) is the caller id we record.

    A sub-branch root that no pointer reaches this way (a Job spawn's
    pointer names no head, and the graph carries no pointer rows) still
    records its spawning reply as ``caller``; that is the way back. Its
    label comes from the Job's spawn card in ``messages``: the Job's
    admission id ``<job_id>_user`` is the root.
    """
    by_id = {n.get("id"): n for n in graph if n.get("id")}
    # Build conv_children from predecessor
    conv_children: dict[str, list[str]] = {}
    for n in graph:
        p = n.get("predecessor")
        if p:
            conv_children.setdefault(p, []).append(n.get("id") or "")
    for attach in graph:
        if attach.get("function") != "attach":
            continue
        tip = attach.get("attach_ref")
        caller = attach.get("predecessor") or attach.get("caller")
        if not tip or tip not in by_id or not caller:
            continue
        # Walk predecessor up from the tip to the sub-branch's
        # ``source=agent_spawn`` root.
        cur: str | None = tip
        hops = 0
        sub_root = None
        seen: set[str] = set()
        while cur and cur not in seen and hops < 500:
            seen.add(cur)
            hops += 1
            n = by_id.get(cur) or {}
            if (n.get("source") == "agent_spawn"
                    and n.get("role") == "user"):
                sub_root = n
                break
            p = n.get("predecessor")
            if not p:
                break
            cur = p
        if not sub_root:
            continue
        sub_root["spawned_from"] = {
            "caller_id": caller,
            "label": (attach.get("attach_label") or "").strip() or None,
        }
    labels: dict[str, str] = {}
    if messages:
        from openprogram.state.store.session.graph.branches import (
            _extract_attach_job_id,
            _extract_attach_label,
        )
        for m in messages:
            job_id = _extract_attach_job_id(m)
            label = _extract_attach_label(m)
            if job_id and label:
                labels[f"{job_id}_user"] = label
    for n in graph:
        if (n.get("spawned_from") or n.get("source") != "agent_spawn"
                or n.get("role") != "user" or n.get("predecessor")):
            continue
        caller = n.get("caller")
        if caller and caller in by_id:
            n["spawned_from"] = {
                "caller_id": caller,
                "label": labels.get(n.get("id") or "")
                or (n.get("branch_name") or "").strip() or None,
            }


def _spawning_call_description(root: dict, caller: str, messages: list[dict]) -> str | None:
    """The ``description`` of the ``agent`` call that started a branch.

    The attach row naming the branch is written when the call returns; a
    run stopped mid-call never writes one, so the call itself names it.
    The call is the caller's ``agent`` row whose prompt opened the branch,
    or its only one.
    """
    calls = []
    for m in messages:
        if m.get("caller") != caller or m.get("function") != "agent":
            continue
        try:
            arguments = (parsed_extra(m) or {}).get("tool_use", {}).get("arguments")
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if isinstance(arguments, dict):
            calls.append(arguments)
    prompt = str(root.get("content") or "").strip()
    match = next((a for a in calls if str(a.get("prompt") or "").strip() == prompt), None)
    if match is None and len(calls) == 1:
        match = calls[0]
    description = (match or {}).get("description")
    return description.strip() if isinstance(description, str) and description.strip() else None


def _spawned_branch(head: str | None, messages: list[dict]) -> dict | None:
    """Where a sub-agent branch came from, when the head is on one.

    The transcript's way back (the sub-agent banner) needs the branch's
    spawn root and the conversation it was spawned from. A bounded load
    ships no graph, so the server names them: ``back_target`` is the
    newest conversation tip below the spawning reply, the main view the
    user left.
    """
    by_id = {m.get("id"): m for m in messages if m.get("id")}

    def conversational(m: dict) -> bool:
        return (
            m.get("role") in ("user", "assistant")
            and m.get("display") != "root"
            # A runtime user row is a scheduled turn (an agent's notice)
            # the conversation continues through.
            and (m.get("display") != "runtime" or m.get("role") == "user")
            and not m.get("function")
            and not isinstance(m.get("covers_ids"), list)
        )

    root, seen = by_id.get(head), set()
    while root is not None and root.get("predecessor") in by_id and root.get("id") not in seen:
        seen.add(root.get("id"))
        root = by_id[root["predecessor"]]
    caller = (root or {}).get("caller")
    if (
        root is None or root.get("source") != "agent_spawn"
        or root.get("role") != "user" or caller not in by_id
    ):
        return None
    children: dict[str, list[dict]] = {}
    for m in messages:
        parent = m.get("predecessor")
        if parent and conversational(m):
            children.setdefault(parent, []).append(m)

    def stamp(m: dict) -> float:
        value = m.get("created_at") or m.get("timestamp") or 0
        return float(value) if isinstance(value, (int, float)) else 0.0

    back, hops = caller, set()
    while back in children and back not in hops:
        hops.add(back)
        back = max(children[back], key=stamp)["id"]
    first_reply = next(
        (m["id"] for m in children.get(root["id"], []) if m.get("role") == "assistant"),
        None,
    )
    label = None
    from openprogram.state.store.session.graph.branches import (
        _extract_attach_job_id,
        _extract_attach_label,
    )
    for m in messages:
        job_id = _extract_attach_job_id(m)
        if job_id and f"{job_id}_user" == root["id"]:
            label = _extract_attach_label(m)
            break
    if label is None:
        label = _spawning_call_description(root, caller, messages)
    return {
        "root_id": root["id"],
        "caller_id": caller,
        "back_target": back,
        "first_reply": first_reply,
        "label": label or (root.get("branch_name") or "").strip() or None,
    }


def _compaction_msg_ts(m: dict) -> float:
    for key in ("timestamp", "created_at", "compacted_at"):
        v = m.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return 0.0


def _compaction_exec_at(
    shown: list[dict],
    summary_id: str,
    exec_ts: float,
    all_msgs: list[dict],
) -> int:
    """Insert the execution event after the last message that existed
    when the compact ran (then-HEAD). ``compacted_at`` is that clock;
    without it, walk ``all_msgs`` up to the summary row."""
    timed = [
        (i, _compaction_msg_ts(m))
        for i, m in enumerate(shown)
        if _compaction_msg_ts(m)
    ]
    if exec_ts and timed:
        at = 0
        for i, t in timed:
            if t <= exec_ts:
                at = i + 1
        return at
    before: set[str] = set()
    for m in all_msgs:
        mid = m.get("id")
        if mid == summary_id:
            break
        if mid:
            before.add(str(mid))
    if not before:
        return len(shown)
    at = 0
    for i, m in enumerate(shown):
        if m.get("id") in before:
            at = i + 1
    return at


def _ordered_fn_run_siblings(
    by_id_all: dict,
    siblings_by_pred: dict,
    mid_,
    norm_pred,
) -> list:
    """Return fn-run sibling ids in timestamp and source order."""
    try:
        src = by_id_all.get(mid_)
    except TypeError:
        return []
    if not isinstance(src, dict):
        return []
    try:
        sibs = siblings_by_pred.get(norm_pred(src), ())
    except TypeError:
        return []
    ordered = sorted(
        sibs, key=lambda item: (item[0].get("created_at") or 0, item[1])
    )
    return [item[0].get("id") for item in ordered]


def splice_compaction_event_rows(
    shown: list[dict],
    graph: list[dict],
    all_msgs: list[dict] | None = None,
) -> list[dict]:
    """Rebuild UI-only compaction rows from summary nodes.

    Two placements, both off ``conv['messages']`` so they never enter
    LLM context:

    * ``slot=card`` — one row for the *active* summary, after the last
      covered message (the fold / boundary before the kept tail).
    * ``slot=event`` — one row per summary (including superseded), at
      the compact's execution time.

    Only summaries of the shown branch get rows: the ones whose covered
    segment lies wholly on it, the rule ``render_context`` uses to put a
    summary in a branch's context. A branch forked inside the covered
    range, or beside it, was never compacted.
    """
    by_id = {m.get("id"): m for m in (all_msgs or [])}
    index = {m.get("id"): i for i, m in enumerate(shown)}
    on_branch: set = set()
    for m in shown:
        mid = m.get("id")
        while mid and mid not in on_branch:
            on_branch.add(mid)
            mid = (by_id.get(mid) or {}).get("predecessor")
    inserts: list[tuple[int, dict]] = []

    def _stats(n: dict) -> tuple[int, float, dict]:
        src = by_id.get(n.get("id")) or {}
        raw = src.get("covers_ids")
        covers = (
            [str(c) for c in raw if c]
            if isinstance(raw, (list, tuple)) and raw
            else [str(c) for c in (n.get("covers_ids") or []) if c]
        )
        n_cov = src.get("summarised_count")
        if not isinstance(n_cov, int):
            n_cov = n.get("summarised_count")
        if not isinstance(n_cov, int):
            n_cov = len(covers)
        exec_ts = (
            src.get("compacted_at")
            or n.get("compacted_at")
            or 0
        )
        try:
            exec_ts = float(exec_ts or 0)
        except (TypeError, ValueError):
            exec_ts = 0.0
        ts = (
            exec_ts
            or n.get("created_at")
            or src.get("created_at")
            or src.get("timestamp")
        )
        extra = {
            "summarised_count": n_cov,
            "timestamp": ts,
            "covers_ids": covers,
        }
        for key in ("tokens_before", "tokens_after"):
            v = src.get(key)
            if v is None:
                v = n.get(key)
            if isinstance(v, (int, float)):
                extra[key] = int(v)
        extra["_src"] = src
        extra["_exec_ts"] = exec_ts
        return n_cov, exec_ts, extra

    def _event_row(n: dict, extra: dict) -> dict:
        n_cov = extra["summarised_count"]
        tb, ta = extra.get("tokens_before"), extra.get("tokens_after")
        if tb is not None and ta is not None:
            content = (
                f"Context compacted here: covered {n_cov} messages, "
                f"{tb} → {ta} tokens"
            )
        else:
            content = f"Context compacted here: covered {n_cov} messages"
        return {
            "id": f"{n['id']}_ui",
            "role": "system",
            "kind": "compaction",
            "slot": "event",
            "summarised_count": n_cov,
            "content": content,
            "timestamp": extra["timestamp"],
            **({
                "tokens_before": extra["tokens_before"],
                "tokens_after": extra["tokens_after"],
            } if "tokens_before" in extra else {}),
        }

    for n in graph:
        if not n.get("id"):
            continue
        active = (
            isinstance(n.get("covers_ids"), (list, tuple))
            and n["covers_ids"]
            and not n.get("superseded_summary")
        )
        relic = bool(n.get("superseded_summary"))
        if not active and not relic:
            continue
        n_cov, exec_ts, extra = _stats(n)
        src = extra.pop("_src")
        extra.pop("_exec_ts", None)
        segment = extra["covers_ids"]
        if not segment or not all(c in on_branch for c in segment):
            continue
        inserts.append((
            _compaction_exec_at(shown, str(n["id"]), exec_ts, all_msgs or []),
            _event_row(n, extra),
        ))
        if not active:
            continue
        covers = extra["covers_ids"]
        cover_set = set(covers)
        cov_at = [index[cid] for cid in covers if cid in index]
        fold = (max(cov_at) + 1) if cov_at else next(
            (i for i, m in enumerate(shown)
             if m.get("id") and m.get("id") not in cover_set),
            0,
        )
        inserts.append((fold, {
            "id": f"{n['id']}_card",
            "role": "system",
            "kind": "compaction",
            "slot": "card",
            "summarised_count": n_cov,
            "covers_ids": covers,
            "content": src.get("content") or n.get("preview") or "",
            "timestamp": extra["timestamp"],
        }))

    if not inserts:
        return shown
    inserts.sort(key=lambda x: x[0], reverse=True)
    out = list(shown)
    for i, row in inserts:
        out.insert(i, row)
    return out


def _is_top_function_run(m: dict, by_id: dict[str, dict]) -> bool:
    """True if ``m`` is a top-level Agent method ENTRY node — the
    root of one complete run (manual /run, fn-form, welcome button, or a
    retry sibling), NOT an internal sub-call of another function.

    The discriminator is the ``caller`` (sub-call) edge, NOT the
    ``predecessor`` (conversation) edge:

    * an INTERNAL sub-call (gui_step, plan_next_action, a self-recursion)
      has ``caller`` pointing at another code/tool node — it belongs
      inside that function's Execution DAG, never a standalone card.
    * a TOP-LEVEL run has an empty / ROOT caller. Its place on the chain
      comes from ``predecessor`` (a new run chains off the head)
      or a shared fork point (a retry). A code node whose predecessor is
      ANOTHER code node is therefore still a top-level run — the NEXT run
      chained after the previous one — not a sub-call.

    Shared with ``_rebuild_runtime_cards`` so the version switcher counts
    exactly the nodes that render as Function-call cards.
    """
    if m.get("role") not in ("tool", "code"):
        return False
    caller = m.get("caller") or ""
    if caller in ("", "ROOT"):
        return True
    crole = (by_id.get(caller) or {}).get("role")
    # caller is another code/tool node → internal sub-call.
    return crole not in ("tool", "code")


def _runtime_card(m: dict, tree) -> dict:
    """A top-level Agent method's code node as the Function-call card the live runtime shows."""
    # Errored fn-form code nodes persist metadata.status="error", which
    # _node_to_msg surfaces as m["status"] (no is_error key exists on the
    # dict). Read the real landed status first; keep is_error only as a
    # legacy fallback.
    status = m.get("status") or ("error" if m.get("is_error") else "done")
    return {
        **m,
        "role": "assistant",
        "type": "status",
        "display": "runtime",
        "function": m.get("function") or "",
        "content": m.get("content") or "",
        "status": status,
        "context_tree": tree,
        # Top-level card: no assistant parent, so the row stays on the main
        # list instead of folding into an LLM bubble's runtimeChildren.
        "predecessor": "",
    }


def _rebuild_runtime_cards(
    chain: list[dict], all_msgs: list[dict], session_id: str, *, with_trees: bool = True,
) -> list[dict]:
    """Turn a manually-invoked Agent method's code node into the
    same Function-call card the live runtime shows.

    A top-level code node (``predecessor`` is ROOT / an anchor / anything
    that is not an LLM reply) is the root of one Agent method call.
    On refresh it arrives as a bare ``role="tool"`` row whose parent is
    not an assistant, so ``aggregate_tool_messages`` can't fold it — the
    projection would draw it as a ``role="system"`` text blob instead of a
    card. Rebuild it into a ``{role:"assistant", display:"runtime",
    function, content, context_tree}`` row (the shape the projection
    draws as a function run), and drop its nested sub-nodes
    (gui_step / plan_next_action / internal LLM …) so they collapse into
    the card's context_tree instead of spilling into the chat stream.
    """
    by_id = {m.get("id"): m for m in all_msgs}

    def _is_top_code(m: dict) -> bool:
        if m.get("role") != "tool":
            return False
        # Top-level = a run entry, keyed on the CALLER (sub-call) edge:
        # every top-level run (fresh, chained, or retry) has an empty /
        # ROOT caller and carries its chain position in
        # the predecessor field. Internal sub-calls (gui_step, conclusion,
        # plan_next_action, self-recursion) have caller pointing at
        # another code/tool node — they live in the card's Execution DAG,
        # never a standalone card. See _is_top_function_run (same rule).
        return _is_top_function_run(m, by_id)

    # Collect each top code node's INTERNAL sub-calls (via the CALLER
    # edge) so they don't render as separate rows — they live in the
    # card's context_tree. Sub-calls hang off ``caller`` (a code/tool
    # node), NOT ``predecessor``: a node whose predecessor points at a
    # code node is the NEXT top-level run chained/forked after it (a
    # sibling run), which must keep its OWN card — walking predecessor
    # here would swallow it. Anything that is itself a top-level fn-run
    # is never treated as a descendant.
    by_id_full = {m.get("id"): m for m in all_msgs}
    children_of: dict[str, list[str]] = {}
    for m in all_msgs:
        if m.get("role") == "user":
            continue
        if _is_top_function_run(m, by_id_full):
            continue  # a run entry is never someone's internal sub-call
        c = m.get("caller") or ""
        if c and c != "ROOT":
            children_of.setdefault(c, []).append(m.get("id"))

    def _descendants(root_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(children_of.get(root_id, []))
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            stack.extend(children_of.get(cid, []))
        return seen

    from openprogram.runtime.execution.dag import build_exec_dag_by_id

    drop: set[str] = set()
    out: list[dict] = []
    for m in chain:
        mid = m.get("id")
        if mid in drop:
            continue
        if _is_top_code(m):
            # Anchor the context_tree on this code node's own id, not on
            # (name, caller=ROOT). The latter is "last match wins", so
            # calling one function twice in a session would resolve both
            # cards to the most recent invocation's subtree. By-id gives
            # each card its own subtree.
            card = _runtime_card(m, build_exec_dag_by_id(session_id, mid) if with_trees else None)
            out.append(card)
            drop |= _descendants(mid)
            continue
        out.append(m)
    return out

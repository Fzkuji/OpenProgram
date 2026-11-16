"""The branch list of a session: tips, names and the History graph.

``build_branches_payload`` is the ``branches_list`` data every client shows
next to a conversation. The attach helpers read the attach pointer rows the
graph and the transcript both render.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)


def _attach_info(m: dict) -> tuple[Optional[str], bool, Optional[str]]:
    """Returns ``(source_head_id, manual, source_commit_id)`` for an
    attach pointer row.

    Source-head is the branch tip the pointer references.
    ``manual=True`` means the user wrote the attach via the Branches
    panel; ``manual=False`` means it was written by a /task spawn.
    ``source_commit_id`` (added with the attach-commit-expansion
    refactor) is the ContextCommit id of the source branch at the
    moment the attach was created — used by generator.py to expand
    the attach into items. Missing on legacy attach rows; callers
    must handle None (fallback to legacy single-item attach).
    """
    if m.get("function") != "attach":
        return None, False, None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None, False, None
    if isinstance(raw, dict) and "attach" in raw and isinstance(raw["attach"], dict):
        raw = raw["attach"]
    if not isinstance(raw, dict):
        return None, False, None
    h = raw.get("head_id")
    src = h.strip() if isinstance(h, str) and h.strip() else None
    manual = bool(raw.get("manual"))
    cid = raw.get("source_commit_id")
    source_commit_id = (
        cid.strip() if isinstance(cid, str) and cid.strip() else None
    )
    return src, manual, source_commit_id


def _attach_ref(m: dict) -> Optional[str]:
    """Backward-compat shim. Prefer ``_attach_info`` for new code."""
    src, _manual, _src_commit = _attach_info(m)
    return src


def _extract_attach_session_id(m: dict) -> Optional[str]:
    """Return the session namespace referenced by an attach pointer."""
    if m.get("function") != "attach":
        return None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict) and isinstance(raw.get("attach"), dict):
        raw = raw["attach"]
    if not isinstance(raw, dict):
        return None
    value = raw.get("session_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_merge_temp_attach(m: dict) -> bool:
    """Whether an attach row is transient merge input, not a spawn."""
    if m.get("function") != "attach":
        return False
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
    if isinstance(raw, dict) and isinstance(raw.get("attach"), dict):
        raw = raw["attach"]
    return bool(isinstance(raw, dict) and raw.get("merge_temp"))


def _extract_attach_label(m: dict) -> Optional[str]:
    """``attach.label`` from an attach pointer row, if present."""
    if m.get("function") != "attach":
        return None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict) and "attach" in raw and isinstance(raw["attach"], dict):
        raw = raw["attach"]
    if isinstance(raw, dict):
        v = raw.get("label")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_attach_job_id(m: dict) -> Optional[str]:
    """``attach.job_id`` from an attach pointer row: the Job that ran the
    spawned agent. Its branch root is the Job's admission id
    ``<job_id>_user``, which names the branch while the row has no head."""
    if m.get("function") != "attach":
        return None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict) and "attach" in raw and isinstance(raw["attach"], dict):
        raw = raw["attach"]
    if isinstance(raw, dict):
        v = raw.get("job_id")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_function_name(m: dict) -> Optional[str]:
    """For a tool node, the underlying function name (``bash``, ``task``,
    ``read``, ...). Pulled from the legacy ``function`` field with a
    ``extra.tool_use.name`` fallback. ``None`` for non-tool nodes."""
    if m.get("role") != "tool":
        return None
    name = m.get("function")
    if isinstance(name, str) and name:
        return name
    extra = m.get("extra")
    if isinstance(extra, str) and extra:
        try:
            import json as _json
            parsed = _json.loads(extra)
            n = (parsed.get("tool_use") or {}).get("name")
            if isinstance(n, str) and n:
                return n
        except Exception:
            return None
    return None


def _extract_tool_is_error(m: dict) -> bool:
    """Tool node's ``metadata.is_error`` flag (default False)."""
    if m.get("role") != "tool":
        return False
    return bool(m.get("is_error"))


def _extract_llm_meta(m: dict) -> dict:
    """Compact dict of the LLM call stats we surface on the tooltip:
    ``model`` / ``input_tokens`` / ``output_tokens``. Skips fields that
    are absent so the frontend only renders rows that have data."""
    if (m.get("role") or "") not in ("assistant", "llm"):
        return {}
    out: dict = {}
    for k in ("model", "input_tokens", "output_tokens"):
        v = m.get(k)
        if v is not None and v != "":
            out[k] = v
    return out


def _extract_tool_input(m: dict) -> Optional[str]:
    """Pull ``arguments`` out of a tool/code node's extra blob.

    The DAG tooltip uses this to show "what the LLM called this function
    with" alongside the result. Returns a JSON string (pretty-stable
    enough for hover display) or ``None`` for non-tool nodes / when no
    args were captured.
    """
    if m.get("role") != "tool":
        return None
    extra = m.get("extra")
    args = None
    if isinstance(extra, str) and extra:
        try:
            import json as _json
            parsed = _json.loads(extra)
            args = (parsed.get("tool_use") or {}).get("arguments")
        except Exception:
            return None
    elif isinstance(extra, dict):
        args = (extra.get("tool_use") or {}).get("arguments")
    if args is None:
        return None
    if isinstance(args, str):
        return args
    try:
        import json as _json
        return _json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


def _attach_embed_stats(
    store, session_id: Optional[str], source_commit_id: Optional[str],
) -> tuple[Optional[int], Optional[int]]:
    """Return ``(item_count, total_tokens)`` for the source ContextCommit
    a manual / spawn attach pointer would expand into.

    The frontend uses these to render the embed preview ("EMBEDS N
    messages · M tokens") without a follow-up round trip. Returns
    ``(None, None)`` when the source commit isn't available — frontend
    falls back to the legacy single-message preview.
    """
    if not source_commit_id:
        return None, None
    try:
        from openprogram.state.store.session.context_commit.store import load_commit
        # Same-session lookup is O(1) (direct file read). Don't fall
        # back to a global scan — that walks every session log on
        # disk and freezes the UI when many sessions exist. The
        # frontend just renders the legacy preview when stats can't
        # be resolved cheaply.
        c = load_commit(store, source_commit_id, session_id=session_id)
    except Exception:
        return None, None
    if c is None:
        return None, None
    # Skip summarized items — they don't render and so wouldn't make
    # it into an expanded attach block either.
    visible = [it for it in c.items if it.state != "summarized"]
    count = len(visible)
    tokens = sum(int(it.tokens or 0) for it in visible)
    return count, tokens


def _session_nodes(session_id: str) -> dict:
    from openprogram.state.store.current import current_store
    from openprogram.state.store import SessionNodeWriter
    return SessionNodeWriter(current_store(), session_id).load().nodes


def runtime_started_heads(nodes: dict, heads: list[str], *, kept=()) -> set[str]:
    """Tips of branches the runtime opened and no person ever wrote on.

    A branch's own part runs from its tip up to where it leaves another
    line (a message with more than one message under it). It is the
    runtime's when every prompt in that part was written by the runtime
    (``display: runtime``: a Goal continuation, a background-command
    notice), or it holds no prompt and only reports (``report_…``, posted
    by an always-on Agent or a scheduled task where the head stood, and left
    behind when the reader moved on). A turn that continues its branch no
    longer opens one; these are in conversations saved while it did. They stay in the History
    graph and the Branches panel; lists of the agents a reader works with
    leave them out.

    The shape alone cannot tell which of two lines leaving one message is
    the older: a branch a person forked off a Goal's line looks like the
    Goal's line leaving the person's. ``kept`` names the tips the records
    say are in use (the selected branch's, the Goal's), and those are
    never the runtime's.
    """
    conversation = ("user", "assistant")
    under: dict[str, int] = {}
    for node in nodes.values():
        if node.role in conversation and node.predecessor:
            under[node.predecessor] = under.get(node.predecessor, 0) + 1
    started = set()
    for head in heads:
        if head in kept:
            continue
        key, seen, prompts, forked, reports = head, set(), [], False, True
        while key and key not in seen:
            seen.add(key)
            node = nodes.get(key)
            if node is None or (node.metadata or {}).get("display") == "root":
                break
            if node.role == "user":
                prompts.append((node.metadata or {}).get("display") == "runtime")
            reports = reports and str(key).startswith("report_")
            if under.get(node.predecessor or "", 0) > 1:
                forked = True
                break
            key = node.predecessor
        if forked and (all(prompts) if prompts else reports):
            started.add(head)
    return started


def scheduled_heads(nodes: dict, heads: list[str], *, kept=()) -> set[str]:
    """Tips of lines a schedule wrote: every prompt on the line's own part, from
    the tip up to where it leaves another line, came from the scheduler. The
    reports a task posts there carry no prompt. A part that is only a reply
    (a run cut off before its reply, and continued from its prompt) is judged
    by the prompt it answers. ``kept`` tips are in use (the selected branch's)
    and are never a task's."""
    under: dict[str, int] = {}
    for node in nodes.values():
        if getattr(node, "role", None) in ("user", "llm", "assistant") and node.predecessor:
            under[node.predecessor] = under.get(node.predecessor, 0) + 1
    kept = set(kept)
    found = set()
    for head in heads:
        if head in kept:
            continue
        key, seen, prompts = head, set(), []
        while key and key not in seen:
            seen.add(key)
            node = nodes.get(key)
            metadata = getattr(node, "metadata", None) or {}
            if node is None or metadata.get("display") == "root":
                break
            if node.role == "user":
                prompts.append(metadata.get("source") == "scheduler")
            if under.get(node.predecessor or "", 0) > 1:
                fork = nodes.get(node.predecessor)
                if not prompts and fork is not None and fork.role == "user":
                    prompts.append((getattr(fork, "metadata", None) or {}).get("source") == "scheduler")
                break
            key = node.predecessor
        if prompts and all(prompts):
            found.add(head)
    return found


def _spawned_heads(session_id: str, heads: list[str], nodes: dict | None = None) -> set[str]:
    """Tips whose chain starts at a sub-agent's spawn root.

    Older sessions keep a sub-agent's turns in the parent session: its
    first user node is a spawn root (``spawn_branch_root`` / source
    ``agent_spawn``) and its reply has no caller, so ``list_branches``
    reports the reply as a tip like any branch the user made.
    """
    from openprogram.state.store.session.session_store.shared import _is_spawn_root
    if nodes is None:
        nodes = _session_nodes(session_id)
    spawned = set()
    for head in heads:
        key, seen = head, set()
        while key and key not in seen:
            seen.add(key)
            node = nodes.get(key)
            if node is None:
                break
            if _is_spawn_root(node.metadata or {}):
                spawned.add(head)
                break
            key = node.predecessor
    return spawned


def _branches_in_use(session: dict | None, leaves: list[dict]) -> set[str]:
    """The tips of the selected branch and of the branch a Goal works on."""
    session = session or {}
    extra = session.get("extra_meta") if isinstance(session.get("extra_meta"), dict) else {}
    goal = extra.get("goal") or session.get("goal")
    in_use = {session.get("active_branch_id"), goal.get("branch_id") if isinstance(goal, dict) else None}
    in_use.discard(None)
    kept = {row["head_msg_id"] for row in leaves if row.get("branch_id") in in_use}
    if session.get("head_id"):
        kept.add(session["head_id"])
    return kept


def build_branches_payload(session_id: str | None) -> dict:
    """Build the ``branches_list`` data dict for a session.

    Sync + side-effect-free so any thread can call it — the WS handler
    sends it on request, and the run-path live poller broadcasts it
    while an Agent method is executing (so the History graph
    fills in node by node instead of only after the run ends).
    """
    rows: list[dict] = []
    active_head = None
    graph: list[dict] = []
    if session_id:
        try:
            from openprogram.state.store.current import current_store
            db = current_store()
            sess = db.get_session(session_id)
            active_head = (sess or {}).get("head_id")
            from openprogram.state.store.session.graph.builder import build_session_graph
            graph = build_session_graph(session_id, active_head)
            leaves = db.list_branches(session_id)
            nodes = _session_nodes(session_id)
            heads = [row["head_msg_id"] for row in leaves]
            spawned = _spawned_heads(session_id, heads, nodes)
            in_use = _branches_in_use(sess, leaves)
            runtime_started = runtime_started_heads(nodes, heads, kept=in_use)
            scheduled = scheduled_heads(nodes, heads, kept=in_use)
            for row in leaves:
                # An archived branch is retired from the agent list
                # (agent-collaboration.md §2.6). The panel is the same
                # list in visual form, so it drops them too — same
                # filter list_agents applies. They stay in the History
                # graph below, which is the view that shows what exists.
                if row.get("archived"):
                    continue
                mid = row["head_msg_id"]
                name = row.get("name")
                if not name:
                    # Unnamed branch → fall back to the head msg id's
                    # short hex prefix (git-style). The user can run
                    # auto_name_branch to get an LLM-summarized label
                    # on demand. Pulling chat content as the label was
                    # confusing (the panel filled up with assistant
                    # reply text) and didn't match git mental model.
                    name = mid[:8]
                rows.append({
                    "head_msg_id": mid,
                    **({"branch_id": row["branch_id"]} if row.get("branch_id") else {}),
                    "name": name,
                    "is_named": bool(row.get("name")),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "active": (row["branch_id"] == (sess or {}).get("active_branch_id")) if row.get("branch_id") else (mid == active_head),
                    # A sub-agent's conversation kept in this session: its
                    # tip is addressable, but it is not a branch the user made.
                    "sub_agent": mid in spawned,
                    # A scheduled task's branch: other agents can read it,
                    # its runs show on the task's page, not in branch lists.
                    # A line only the schedule wrote on is one too, the
                    # tips older runs left behind among them.
                    "scheduled_task": bool(row.get("scheduled_task_id")) or mid in scheduled,
                    # Opened by the runtime, never written on by a person:
                    # not a branch the reader made (``runtime_started_heads``).
                    "runtime_started": mid in runtime_started,
                })
        except Exception as e:
            _log.warning("[list_branches] %s: %s", session_id, e)
    # 主干 tip（lane 0 最深的对话节点）：HEAD 在这里时顶栏显示 main，
    # 而不是 detached——学 git，主线不是"游离"状态。
    trunk_head = None
    best_depth = -1.0
    for n in graph:
        if (n.get("_lane") or 0) != 0:
            continue
        if n.get("display") in ("root", "runtime"):
            continue
        if n.get("role") not in ("user", "assistant"):
            continue
        d = n.get("_depth") or 0
        if d > best_depth:
            best_depth = d
            trunk_head = n.get("id")
    return {"session_id": session_id, "branches": rows,
            "active": active_head, "trunk_head": trunk_head,
            "graph": graph}

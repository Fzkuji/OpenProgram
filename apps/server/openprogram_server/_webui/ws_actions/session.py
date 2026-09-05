"""Session lifecycle WS actions: delete / clear / load / search / list / follow_up_answer."""
from __future__ import annotations

import asyncio
import json
import time


# Keep ordinary tool results byte-for-byte compatible while preventing one
# abnormal result from dominating the session_loaded JSON frame. The full
# persisted node remains available through get_full_tool_output.
TOOL_OUTPUT_INLINE_MAX_BYTES = 32 * 1024


def _serialized_string_prefix(text: str) -> str:
    """Longest leading substring whose JSON string stays within the cap."""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        size = len(json.dumps(
            text[:middle], ensure_ascii=False,
        ).encode("utf-8"))
        if size <= TOOL_OUTPUT_INLINE_MAX_BYTES:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _truncate_tool_record(
    record: dict,
    *,
    message_id: str,
    fallback_node_id: str | None = None,
) -> dict:
    result = record.get("result")
    serialized = json.dumps(
        result, ensure_ascii=False, default=str,
    ).encode("utf-8")
    if len(serialized) <= TOOL_OUTPUT_INLINE_MAX_BYTES:
        return record
    node_id = record.get("tool_call_id") or fallback_node_id
    if not node_id:
        return record
    text = result if isinstance(result, str) else serialized.decode("utf-8")
    prefix = _serialized_string_prefix(text)
    return {
        **record,
        "result": prefix,
        "truncated": True,
        "total_bytes": len(serialized),
        "message_id": message_id,
        "node_id": node_id,
    }


def _truncate_tool_blocks(
    blocks: list,
    *,
    message_id: str,
    tool_node_ids: list[str],
) -> tuple[list, bool]:
    changed = False
    tool_index = 0
    output = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool":
            output.append(block)
            continue
        fallback = (
            tool_node_ids[tool_index] if tool_index < len(tool_node_ids) else None
        )
        tool_index += 1
        truncated = _truncate_tool_record(
            block, message_id=message_id, fallback_node_id=fallback,
        )
        changed = changed or truncated is not block
        output.append(truncated)
    return output, changed


def _truncate_tool_outputs_for_wire(messages: list[dict]) -> list[dict]:
    """Copy only messages whose inline tool result crosses the wire cap."""
    output = []
    for message in messages:
        message_id = str(message.get("id") or "")
        tool_calls = message.get("tool_calls")
        tool_node_ids = [
            str(call.get("tool_call_id"))
            for call in (tool_calls or [])
            if isinstance(call, dict) and call.get("tool_call_id")
        ]
        changed = False
        copied = message
        if isinstance(tool_calls, list):
            truncated_calls = [
                _truncate_tool_record(call, message_id=message_id)
                if isinstance(call, dict) else call
                for call in tool_calls
            ]
            if any(a is not b for a, b in zip(truncated_calls, tool_calls)):
                copied = {**copied, "tool_calls": truncated_calls}
                changed = True

        blocks = message.get("blocks")
        if isinstance(blocks, list):
            truncated_blocks, blocks_changed = _truncate_tool_blocks(
                blocks, message_id=message_id, tool_node_ids=tool_node_ids,
            )
            if blocks_changed:
                copied = {**copied, "blocks": truncated_blocks}
                changed = True

        raw_extra = message.get("extra")
        extra = raw_extra
        if isinstance(raw_extra, str):
            try:
                extra = json.loads(raw_extra)
            except (TypeError, json.JSONDecodeError):
                extra = None
        if isinstance(extra, dict) and isinstance(extra.get("blocks"), list):
            extra_blocks, extra_changed = _truncate_tool_blocks(
                extra["blocks"],
                message_id=message_id,
                tool_node_ids=tool_node_ids,
            )
            if extra_changed:
                truncated_extra = {**extra, "blocks": extra_blocks}
                if isinstance(raw_extra, str):
                    truncated_extra = json.dumps(
                        truncated_extra, ensure_ascii=False, default=str,
                    )
                copied = {**copied, "extra": truncated_extra}
                changed = True
        output.append(copied if changed else message)
    return output


def _annotate_spawn_origin(graph: list[dict]) -> None:
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
    """
    by_id = {m.get("id"): m for m in (all_msgs or [])}
    index = {m.get("id"): i for i, m in enumerate(shown)}
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
    """True if ``m`` is a top-level @agentic_function ENTRY node — the
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


def _rebuild_runtime_cards(
    chain: list[dict], all_msgs: list[dict], session_id: str,
) -> list[dict]:
    """Turn a manually-invoked @agentic_function's code node into the
    same Function-call card the live runtime shows.

    A top-level code node (``predecessor`` is ROOT / an anchor / anything
    that is not an LLM reply) is the root of one @agentic_function call.
    On refresh it arrives as a bare ``role="tool"`` row whose parent is
    not an assistant, so ``aggregate_tool_messages`` can't fold it — the
    mapper then renders it as a ``role="system"`` text blob instead of a
    card. Rebuild it into a ``{role:"assistant", display:"runtime",
    function, content, context_tree}`` row (the shape conv-mapper.ts
    already maps to a RuntimeBlock), and drop its nested sub-nodes
    (gui_step / plan_next_action / internal LLM …) so they collapse into
    the card's context_tree instead of spilling into the chat stream.
    """
    by_id = {m.get("id"): m for m in all_msgs}
    role_of = {m.get("id"): m.get("role") for m in all_msgs}

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

    from openprogram.webui._exec_dag import build_exec_dag_by_id

    drop: set[str] = set()
    out: list[dict] = []
    for m in chain:
        mid = m.get("id")
        if mid in drop:
            continue
        if _is_top_code(m):
            name = m.get("function") or ""
            # Anchor the context_tree on this code node's own id, not on
            # (name, caller=ROOT). The latter is "last match wins", so
            # calling one function twice in a session would resolve both
            # cards to the most recent invocation's subtree. By-id gives
            # each card its own subtree.
            tree = build_exec_dag_by_id(session_id, mid)
            # Errored fn-form code nodes persist metadata.status="error",
            # which _node_to_msg surfaces as m["status"] (no is_error key
            # exists on the dict). Read the real landed status first; keep
            # is_error only as a legacy fallback.
            status = m.get("status") or ("error" if m.get("is_error") else "done")
            card = {
                **m,
                "role": "assistant",
                "type": "status",
                "display": "runtime",
                "function": name,
                "content": m.get("content") or "",
                "status": status,
                "context_tree": tree,
                # Top-level card: no assistant parent, so conv-mapper
                # keeps it on the main list instead of folding it into
                # an LLM bubble's runtimeChildren.
                "predecessor": "",
            }
            out.append(card)
            drop |= _descendants(mid)
            continue
        out.append(m)
    return out


async def handle_delete_session(ws, cmd: dict):
    from openprogram.webui import server as _s
    from openprogram.agent.session_db import default_db

    session_id = cmd.get("session_id")
    if not session_id:
        return
    with _s._sessions_lock:
        conv = _s._sessions.pop(session_id, None)
    if conv:
        if conv.get("runtime") and hasattr(conv["runtime"], "close"):
            conv["runtime"].close()
        _s._cleanup_session_resources(session_id, conv)
    try:
        default_db().delete_session(session_id)
    except Exception as e:
        _s._log(f"[delete_session] {session_id}: {e}")
    _s._broadcast(json.dumps({"type": "session_deleted", "session_id": session_id}))


async def handle_rename_session(ws, cmd: dict):
    """Set a conversation's display title.

    When ``title`` is provided: write it directly (manual rename).
    When ``title`` is absent/empty: call LLM to generate a title from
    the conversation history (user-requested regeneration).

    Persists to ``meta.json`` via ``update_session`` AND patches the
    in-memory ``_sessions`` dict (if the conv is hydrated) so the
    next ``list_sessions`` and any live reference both see the new
    title. Echoes ``session_updated`` so every connected client can
    patch the one row without a full re-list.
    """
    from openprogram.webui import server as _s
    from openprogram.agent.session_db import default_db

    session_id = cmd.get("session_id")
    if not session_id:
        return

    title = cmd.get("title")
    if isinstance(title, str):
        title = title.strip()

    if not title:
        title = _llm_rename(session_id)
        if not title:
            return

    is_user_typed = bool(cmd.get("title", "").strip())
    try:
        kw = {"title": title}
        if is_user_typed:
            kw["_user_titled"] = True
        default_db().update_session(session_id, **kw)
    except Exception as e:
        _s._log(f"[rename_session] {session_id}: {e}")
    _s._broadcast(json.dumps({
        "type": "session_updated",
        "data": {"id": session_id, "title": title},
    }, default=str))


def _llm_rename(session_id: str) -> str | None:
    """Generate a title via LLM from the session's conversation history."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.dispatcher.titles import _generate_llm_title

    db = default_db()
    history = db.get_branch(session_id) or []
    user_text = ""
    assistant_text = ""
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and not user_text:
            user_text = content
        elif role == "assistant" and not assistant_text:
            assistant_text = content
        if user_text and assistant_text:
            break
    if not user_text:
        return None
    return _generate_llm_title(user_text, assistant_text)


async def handle_update_session_flags(ws, cmd: dict):
    """Set conversation management flags: pinned / archived / group.

    Only the keys present in ``cmd`` are written, so a pin toggle
    doesn't clobber the group and vice-versa. ``group`` may be an
    empty string to ungroup (``update_session`` drops ``None`` but
    keeps ``""`` / ``False``, so booleans + the ungroup sentinel
    persist correctly). Dual-writes meta.json + the in-memory conv
    and echoes ``session_updated``.
    """
    from openprogram.webui import server as _s
    from openprogram.agent.session_db import default_db

    session_id = cmd.get("session_id")
    if not session_id:
        return
    fields: dict = {}
    if "pinned" in cmd:
        fields["pinned"] = bool(cmd.get("pinned"))
    if "archived" in cmd:
        fields["archived"] = bool(cmd.get("archived"))
    if "group" in cmd:
        g = cmd.get("group")
        fields["group"] = "" if g is None else str(g)
    if not fields:
        return
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
        if conv is not None:
            conv.update(fields)
    try:
        default_db().update_session(session_id, **fields)
    except Exception as e:
        _s._log(f"[update_session_flags] {session_id}: {e}")
    _s._broadcast(json.dumps({
        "type": "session_updated",
        "data": {"id": session_id, **fields},
    }, default=str))


async def handle_clear_sessions(ws, cmd: dict):
    from openprogram.webui import server as _s
    from openprogram.agent.session_db import default_db

    db = default_db()

    # 真相来源是 SessionStore（git repos）。"清空全部" = 把 SessionStore 里
    # 枚举到的每个 session 都 delete_session(sid) 掉，外加任何还在内存但已
    # 不在磁盘的。一律按 session_id 直接 destroy git repo，不碰 agent_id ——
    # 旧逻辑「按 agent_id 删 / 查不到就扫文件系统」会漏掉没有 agent_id 的
    # 空壳会话（forced-tool-call 建的），删了刷新又出现。这里收口到真相来源，
    # 一次删干净。
    ids: set[str] = set()
    try:
        for row in db.list_sessions(limit=10**9, include_archived=True):
            sid = row.get("id")
            if sid:
                ids.add(sid)
    except Exception as e:
        _s._log(f"[clear_sessions] list: {e}")

    # 内存里活着的会话：先关 runtime，再纳入待删集合（覆盖磁盘没枚举到的）。
    with _s._sessions_lock:
        for sid, conv in _s._sessions.items():
            ids.add(sid)
            if conv.get("runtime") and hasattr(conv["runtime"], "close"):
                try:
                    conv["runtime"].close()
                except Exception:
                    pass
        _s._sessions.clear()

    for cid in ids:
        _s._follow_up_queues.pop(cid, None)
        with _s._running_tasks_lock:
            _s._running_tasks.pop(cid, None)
        try:
            db.delete_session(cid)
        except Exception as e:
            _s._log(f"[clear_sessions] delete {cid}: {e}")

    # 删完回发一次真实列表（应为空），让发起方立刻看到清空结果，不靠刷新。
    await handle_list_sessions(ws, {})


async def handle_load_session(ws, cmd: dict):
    """Hydrate a session: linear chain under HEAD + full DAG dump + running-task probe."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    # Record which session this connection is now viewing, so a finishing
    # background run can tell whether to mark *other* sessions unread.
    ws._focused_session_id = session_id
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
    # Cold load (fresh worker, new browser session, or a session created
    # by the fn-form REST path that never went through a WS chat turn):
    # the conv isn't in the in-memory map yet. If it exists on disk,
    # hydrate it from SessionDB so the full aggregation below runs —
    # otherwise we'd fall to the empty-stub ``else`` and the page would
    # render the Welcome screen for a session that actually has history
    # (e.g. a manually-invoked @agentic_function's top-level code node).
    if conv is None and session_id:
        from openprogram.agent.session_db import default_db as _db_probe
        try:
            _exists = _db_probe().get_session(session_id) is not None
        except Exception:
            _exists = False
        if _exists:
            conv = _s._get_or_create_session(session_id)
    if conv:
        from openprogram.context.git import (
            active_branch_chain,
            deepest_leaf,
            head_or_tip,
            sibling_navigation_index,
        )
        from openprogram.agent.session_db import default_db as _db_for_load
        from openprogram.webui.persistence import aggregate_tool_messages
        _db_load = _db_for_load()
        try:
            raw_msgs = _db_load.get_messages(conv["id"]) or []
            hidden_ids = {
                m.get("id") for m in raw_msgs
                if m.get("execution_control") and m.get("id")
            }
            changed = True
            while changed:
                before = len(hidden_ids)
                hidden_ids.update(
                    m.get("id") for m in raw_msgs
                    if m.get("caller") in hidden_ids
                )
                changed = len(hidden_ids) != before
            raw_msgs = [
                m for m in raw_msgs if m.get("id") not in hidden_ids
            ]
            # Fold standalone role="tool" rows into their parent assistant's
            # tool_calls[] so the chat UI sees the same shape on refresh
            # as it does on live WS stream.
            all_msgs = aggregate_tool_messages(raw_msgs)
        except Exception:
            all_msgs = conv.get("messages", []) or []
            raw_msgs = all_msgs
        try:
            _sess_for_load = _db_load.get_session(conv["id"]) or {}
            _persisted_head = _sess_for_load.get("head_id")
        except Exception:
            _persisted_head = None
        head = _persisted_head or head_or_tip(conv, all_msgs)
        # If the persisted head points at a row that aggregation just
        # folded away (e.g. a role="tool" child of an assistant whose
        # turn never reached step 6's ``update_session(head_id=...)``
        # — common after a worker restart mid-turn), walk up the raw
        # predecessor chain until we hit a row that survived
        # aggregation. Without this the get_branch(head) walk below
        # anchors on a folded-away id and returns [], so the page
        # renders the empty Welcome screen despite real history.
        if head:
            agg_ids = {m.get("id") for m in all_msgs}
            if head not in agg_ids:
                raw_by_id = {m.get("id"): m for m in raw_msgs}
                cur = head
                hops = 0
                while cur and cur not in agg_ids and hops < 100:
                    parent = (raw_by_id.get(cur) or {}).get("predecessor")
                    if not parent or parent == cur:
                        cur = None
                        break
                    cur = parent
                    hops += 1
                if cur and cur in agg_ids:
                    head = cur
        # The active branch is authoritative: get_branch(head) walks
        # predecessor + caller + ROOT-seq edges (handling fn-call gaps a
        # pure predecessor walk misses) and yields exactly this branch's
        # node ids. active_branch_chain keeps only those ids out of the
        # full, tool-aggregated all_msgs — so the chat shows one branch,
        # oldest-first, with each assistant's folded tool_calls intact,
        # and sibling turns from OTHER branches never leak in. Falls back
        # to a predecessor walk (or all_msgs) if the head is stale/None.
        try:
            branch_ids = {
                m.get("id") for m in (_db_load.get_branch(session_id, head) or [])
            }
        except Exception:
            branch_ids = set()
        chain = active_branch_chain(all_msgs, branch_ids, head)
        # Splice attach pointer rows (function="attach") into the
        # displayed chain. They hang off a parent message via
        # predecessor — not on the conv chain itself — so
        # linear_history skips them, but the chat needs them inline
        # as standalone AttachCard rows.
        chain_ids = {m.get("id") for m in chain}
        attach_by_parent: dict[str, list[dict]] = {}
        for m in all_msgs:
            if m.get("function") != "attach":
                continue
            # Skip pointers already in the chain — older writes set
            # both predecessor and caller, which made attach pointers
            # appear as conv children too. New writes only set
            # predecessor; this guard keeps old data from doubling up.
            if m.get("id") in chain_ids:
                continue
            parent = m.get("predecessor") or ""
            if parent and parent in chain_ids:
                attach_by_parent.setdefault(parent, []).append(m)
        if attach_by_parent:
            spliced: list[dict] = []
            for m in chain:
                spliced.append(m)
                extras = attach_by_parent.get(m.get("id"), [])
                extras.sort(key=lambda x: x.get("timestamp") or 0)
                spliced.extend(extras)
            chain = spliced
        # Splice runtime-block placeholder rows written by the
        # dispatcher's @agentic_function wrapper. They hang off the
        # assistant reply that called the tool via predecessor but are
        # not on the conv chain itself (the chain's head is the
        # assistant reply, not its runtime child). The chat needs
        # each placeholder as a standalone RuntimeBlock row right
        # after its owning assistant reply.
        chain_ids = {m.get("id") for m in chain}
        runtime_by_parent: dict[str, list[dict]] = {}
        for m in all_msgs:
            if m.get("type") != "status" or m.get("display") != "runtime":
                continue
            if m.get("id") in chain_ids:
                continue
            parent = m.get("predecessor") or ""
            if parent and parent in chain_ids:
                runtime_by_parent.setdefault(parent, []).append(m)
        if runtime_by_parent:
            spliced2: list[dict] = []
            for m in chain:
                spliced2.append(m)
                extras = runtime_by_parent.get(m.get("id"), [])
                extras.sort(key=lambda x: x.get("timestamp") or 0)
                spliced2.extend(extras)
            chain = spliced2
        # Rebuild Function-call cards from top-level @agentic_function
        # code nodes (manual /run, fn-form). Without this a refreshed
        # manual call renders as a bare system-text blob instead of the
        # RuntimeBlock card the live runtime shows; its nested sub-nodes
        # are absorbed into the card's context_tree.
        chain = _rebuild_runtime_cards(chain, all_msgs, conv["id"])
        conv["messages"] = chain
        conv["head_id"] = head
        from openprogram.agent.session_db import default_db as _ddb
        from openprogram.webui.ws_actions.branch import (
            _attach_info as _ainfo, _attach_embed_stats as _astats,
        )
        # Version switcher for a Function-call card must count only the
        # COMPLETE runs that are true alternatives of THIS run — i.e. the
        # other top-level @agentic_function entry nodes sharing this run's
        # conversation predecessor (a retry forks off the original's
        # predecessor; a new run chains off the head, so it has no
        # alternatives). Plain ``sibling_index`` counts every node sharing
        # the parent — for legacy ROOT-anchored runs that lumps together
        # all root-level calls AND their predecessor-less sub-calls (the
        # "1/12" the user saw). Restrict the sibling set to fn-run entry
        # nodes so a fresh, model-anchored session shows exactly 2/2.
        by_id_all = {}
        for mm in all_msgs:
            if not isinstance(mm, dict):
                continue
            try:
                by_id_all[mm.get("id")] = mm
            except TypeError:
                continue

        def _norm_pred(mm):
            """The fork-point id of a run: its conversation predecessor,
            falling back to ``caller`` (a retry expresses the fork via
            caller, a chained new run via the predecessor field — both name
            the same fork parent). ROOT / absent → None (root-level)."""
            p = mm.get("predecessor") or mm.get("caller") or None
            return None if p == "ROOT" else p

        _fn_run_ids = set()
        _fn_run_siblings_by_pred = {}
        for position, mm in enumerate(all_msgs):
            if not isinstance(mm, dict):
                continue
            message_id = mm.get("id")
            try:
                if (
                    mm.get("role") in ("tool", "code")
                    and _is_top_function_run(mm, by_id_all)
                ):
                    _fn_run_ids.add(message_id)
                    _fn_run_siblings_by_pred.setdefault(
                        _norm_pred(mm), []
                    ).append((mm, position))
            except TypeError:
                continue

        def _fn_run_siblings(mid_):
            """Ordered ids of the fn-run entries sharing ``mid_``'s
            predecessor (self included), by created_at then insertion."""
            return _ordered_fn_run_siblings(
                by_id_all, _fn_run_siblings_by_pred, mid_, _norm_pred,
            )

        _chat_navigation = sibling_navigation_index(
            all_msgs,
            target_ids={m.get("id") for m in chain if m.get("id")},
        )
        shown = []
        for m in chain:
            mid = m.get("id")
            # Function-call cards get fn-run-scoped sibling nav; every
            # other row (chat turns) keeps the plain predecessor-share nav.
            if m.get("display") == "runtime" and mid in _fn_run_ids:
                ids = _fn_run_siblings(mid)
                i = ids.index(mid) if mid in ids else -1
                idx = (i + 1) if i >= 0 else 0
                total = len(ids)
                prev_id = next_id = None
                if i > 0:
                    prev_id = deepest_leaf(all_msgs, ids[i - 1])
                if 0 <= i < total - 1:
                    next_id = deepest_leaf(all_msgs, ids[i + 1])
            else:
                idx, total, prev_id, next_id = _chat_navigation.get(
                    mid, (0, 0, None, None),
                )
            # Enrich attach pointer rows with embed stats so the
            # AttachCard can render "EMBEDS N messages · M tokens"
            # without a follow-up round trip. Cost = one O(1)
            # commit-file read per attach pointer.
            enriched = {**m,
                "sibling_index": idx,
                "sibling_total": total,
                "prev_sibling_id": prev_id,
                "next_sibling_id": next_id,
            }
            if m.get("function") == "attach":
                _ref, _man, _src = _ainfo(m)
                if _src:
                    _n, _tok = _astats(_ddb(), conv["id"], _src)
                    attach_dict = dict(m.get("attach") or {})
                    attach_dict.setdefault("source_commit_id", _src)
                    if _n is not None:
                        attach_dict["embed_count"] = _n
                    if _tok is not None:
                        attach_dict["embed_tokens"] = _tok
                    enriched["attach"] = attach_dict
            shown.append(enriched)

        # caller 链执行行：agent 调函数的层级树（gui_agent → gui_step →
        # … → LLM 叶子）不在线性对话链上，靠 caller 挂在链上的回复。把
        # 这些行补进 payload——前端 conv-mapper 把它们折成调用树喂给时
        # 间线渲染，不作为顶层消息显示。spawn 分支根（role=user）和
        # attach 指针（父是 assistant）天然不匹配，不会被带进来。
        _chain_ids = {m.get("id") for m in chain}
        _kids_by_caller: dict = {}
        for _m in all_msgs:
            _c = _m.get("caller")
            if _c and _c != "ROOT":
                _kids_by_caller.setdefault(_c, []).append(_m)

        def _collect_exec_rows(mid, acc):
            parent = by_id_all.get(mid) or {}
            for _k in _kids_by_caller.get(mid, []):
                _is_tool = _k.get("role") == "tool" and _k.get("function")
                _is_leaf = (
                    _k.get("role") == "assistant"
                    and parent.get("role") == "tool"
                )
                if not (_is_tool or _is_leaf):
                    continue
                if _k.get("id") in _chain_ids:
                    continue
                acc.append(_k)
                _collect_exec_rows(_k.get("id"), acc)

        _exec_rows: list = []
        for _m in chain:
            # A top-level runtime card already carries its complete caller
            # subtree in context_tree. Re-appending those descendants here
            # makes conv-mapper see the rebuilt root as role=assistant rather
            # than its original role=tool, so direct LLM children fail the
            # call-tree test and leak out as ordinary assistant chat bubbles.
            if (_m.get("role") == "assistant"
                    and _m.get("display") != "runtime"):
                _collect_exec_rows(_m.get("id"), _exec_rows)
        shown.extend(_exec_rows)

        tree_data = {}  # tree Context retired — execution trace lives in SessionDB DAG nodes
        from openprogram.webui.graph_builder import build_session_graph
        graph = build_session_graph(conv["id"], head)

        # Reverse-link each spawned sub-branch's root user msg back
        # to the main-lane turn that produced it, so the frontend
        # can render a "Spawned from: <branch>" card at the top of
        # the sub branch (mirror of the AttachCard on main).
        _annotate_spawn_origin(graph)
        # Mirror the spawned_from annotation onto ``shown`` (what the
        # chat list renders), keyed by id.
        _spawn_by_id = {
            n["id"]: n["spawned_from"]
            for n in graph if n.get("spawned_from")
        }
        for m in shown:
            sf = _spawn_by_id.get(m.get("id"))
            if sf:
                m["spawned_from"] = sf
        shown = splice_compaction_event_rows(shown, graph, all_msgs)
        shown = _truncate_tool_outputs_for_wire(shown)
        from openprogram.agent.session_config import (
            load_session_run_config,
            permission_from_config,
            project_defaults,
        )
        from openprogram.sandbox import ui_state as _sandbox_ui
        run_cfg = load_session_run_config(conv["id"])
        from openprogram.agent.permissions import permission_state
        _permission_state = permission_state(session_id)
        _effective_permission = permission_from_config(
            run_cfg, default=project_defaults(conv["id"]).get("permission_mode"))
        refresh_context_after_load = not conv.get("_last_context_breakdown")
        _stats = conv.get("_last_context_stats") or {}
        _bd = conv.get("_last_context_breakdown")
        if _bd and "breakdown" not in _stats:
            _stats = {**_stats, "type": "context_stats", "breakdown": _bd}
            conv["_last_context_stats"] = _stats
        _db_sess = _ddb().get_session(session_id) or {}
        await ws.send_text(json.dumps({
            "type": "session_loaded",
            "data": {
                "id": conv["id"],
                "title": _db_sess.get("title", ""),
                "messages": shown,
                "graph": graph,
                "head_id": head,
                "context_tree": tree_data,
                "provider_info": _s._get_provider_info(session_id),
                "context_stats": _stats or None,
                "channel": _db_sess.get("channel"),
                "account_id": _db_sess.get("account_id"),
                "peer": _db_sess.get("peer"),
                "peer_display": _db_sess.get("peer_display"),
                "source": _db_sess.get("source"),
                # Session goal (/goal) — the composer's GoalChip hydrates
                # from this on load; live changes ride goal_update frames.
                "goal": (_db_sess.get("extra_meta") or {}).get("goal"),
                "settings": {
                    "tools_enabled": run_cfg.tools_enabled,
                    "tools_override": run_cfg.tools_override,
                    "thinking_effort": run_cfg.thinking_effort,
                    "permission_mode": _effective_permission,
                    "permission_version": _permission_state["version"],
                    "additional_working_dirs": run_cfg.additional_working_dirs,
                    **_sandbox_ui(run_cfg.sandbox_enabled),
                },
                "run_active": _s._is_run_active(conv["id"]),
                "status": (_ddb().get_session(session_id) or {}).get("status", "idle"),
            },
        }, default=str))
        # Cold context accounting is cache warming, not session hydration.
        # refresh_context_stats broadcasts its own context_stats frame after
        # the computation finishes, so the transcript can render first.
        if refresh_context_after_load:
            await asyncio.to_thread(_s.refresh_context_stats, session_id)
        # _is_run_active above already removed any non-reserved task without
        # a live runtime. The remaining timeout also recovers a reservation
        # whose setup thread died before runtime handoff.
        with _s._running_tasks_lock:
            task_info = _s._running_tasks.get(session_id)
        if task_info:
            _now = time.time()
            _started = task_info.get("started_at", _now)
            _last_evt_ts = task_info.get("last_event_at", _started)
            if (_now - _started > 300) and (_now - _last_evt_ts > 300):
                with _s._running_tasks_lock:
                    _s._running_tasks.pop(session_id, None)
                task_info = None
        if task_info:
            # Live partial-tree dump retired with the tree-Context
            # event system. The DAG nodes the function has produced so
            # far are already queryable via the GraphStore.
            await ws.send_text(json.dumps({
                "type": "running_task",
                "data": {
                    "session_id": session_id,
                    "msg_id": task_info["msg_id"],
                    "func_name": task_info["func_name"],
                    "execution_id": task_info.get("execution_id"),
                    "started_at": task_info["started_at"],
                    "display_params": task_info.get("display_params", ""),
                    "partial_tree": None,
                    "stream_events": task_info.get("stream_events", []),
                },
            }, default=str))
        # Reconnect recovery (user-input-requests.md): a function may be
        # blocked in runtime.ask right now. The live ``question.asked`` frame
        # already fired before this client (re)connected, so its card was
        # never drawn / was lost on refresh — replay any still-pending
        # questions for this session as the SAME frame the live path sends,
        # so the frontend's existing card logic redraws with no extra round trip.
        try:
            from openprogram.execution import default_store
            from openprogram.execution.waits import DurableWaitStore
            for q in DurableWaitStore(default_store()).list_open(session_id=session_id):
                request = dict(q.request)
                execution = default_store().get_execution(q.execution_id)
                await ws.send_text(json.dumps({
                    "type": "question.asked",
                    "data": {
                        "id": q.wait_id, "session_id": session_id, "kind": q.kind,
                        "execution_id": q.execution_id,
                        "wait_generation": q.claim_generation,
                        "expected_version": execution.status_version if execution is not None else 0,
                        "prompt": request.get("prompt", ""), "options": request.get("options", []), "multi": request.get("multi", False),
                        "allow_custom": request.get("allow_custom", True), "detail": request.get("detail", ""),
                        "schema": request.get("schema", {}),
                        "questions": request.get("questions", []),
                        "expires_at": q.expires_at,
                    },
                }, default=str))
        except Exception as e:
            _s._log(f"[load_session] question replay {session_id}: {e}")
    else:
        await ws.send_text(json.dumps({
            "type": "session_loaded",
            "data": {
                "id": session_id,
                "title": "New conversation",
                "context_tree": {},
                "provider_info": _s._get_provider_info(),
                "settings": {},
            },
        }, default=str))


async def handle_get_full_tool_output(ws, cmd: dict) -> None:
    """Return one persisted tool node's untruncated output."""
    from openprogram.agent.session_db import default_db

    session_id = cmd.get("session_id")
    message_id = cmd.get("message_id")
    node_id = cmd.get("node_id")
    request_id = cmd.get("request_id")
    data = {
        "session_id": session_id,
        "message_id": message_id,
        "node_id": node_id,
        "request_id": request_id,
    }
    row = None
    if all(isinstance(value, str) and value for value in (
        session_id, message_id, node_id,
    )):
        row = next(
            (
                message for message in default_db().get_messages(session_id)
                if message.get("id") == node_id
                and message.get("role") == "tool"
                and message.get("caller") == message_id
            ),
            None,
        )
    if row is None:
        data["error"] = "tool output not found"
    else:
        data["result"] = row.get("content", "")
    await ws.send_text(json.dumps({
        "type": "full_tool_output",
        "data": data,
    }, ensure_ascii=False, default=str))


async def handle_get_run_state(ws, cmd: dict):
    """Report run state without changing this socket's focused session."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    await ws.send_text(json.dumps({
        "type": "run_state",
        "data": {
            "session_id": session_id,
            "run_active": bool(session_id and _s._is_run_active(session_id)),
        },
    }))


async def handle_follow_up_answer(ws, cmd: dict):
    """User answered a follow-up question from a running function."""
    from openprogram.webui import server as _s
    fq_session_id = cmd.get("session_id", "")
    answer = cmd.get("answer", "")
    with _s._follow_up_lock:
        fq = _s._follow_up_queues.get(fq_session_id)
    if fq is not None:
        fq.put(answer)


# 权限规则跟着**项目**走（见 permission-model.md §2.3）。请求可直接带
# project_id（Projects 页知道项目）；只带 session_id 时反查项目（composer 路径）。

def _resolve_project_id(cmd: dict) -> str | None:
    pid = (cmd.get("project_id") or "").strip()
    if pid:
        return pid
    sid = (cmd.get("session_id") or "").strip()
    if not sid:
        return None
    try:
        from openprogram.store.project import project_store as _projects
        proj = _projects.project_for_session(sid) or _projects.get_default_project()
        return proj.id if proj else None
    except Exception:
        return None


def _project_rules(project_id: str) -> dict:
    from openprogram.store.project import project_store as _projects
    settings = _projects.load_project_settings(project_id)
    r = settings.get("permission_rules") or {}
    return {"allow": list(r.get("allow") or []),
            "deny": list(r.get("deny") or []),
            "ask": list(r.get("ask") or [])}


def _broadcast_permission_rules(project_id: str) -> None:
    """把某项目当前的权限规则广播给前端（规则面板刷新）。"""
    from openprogram.webui import server as _s
    r = _project_rules(project_id)
    _s._broadcast(json.dumps({"type": "permission_rules", "data": {
        "project_id": project_id, **r,
    }}))


async def handle_list_permission_rules(ws, cmd: dict):
    pid = _resolve_project_id(cmd)
    if pid:
        _broadcast_permission_rules(pid)


def _mutate_project_rule(cmd: dict, *, add: bool) -> None:
    pid = _resolve_project_id(cmd)
    behavior = cmd.get("behavior")     # "allow" | "deny" | "ask"
    rule = (cmd.get("rule") or "").strip()
    if not (pid and behavior in ("allow", "deny", "ask") and rule):
        return
    from openprogram.store.project import project_store as _projects
    settings = _projects.load_project_settings(pid)
    rules = settings.get("permission_rules") or {"allow": [], "deny": [], "ask": []}
    lst = rules.setdefault(behavior, [])
    if add and rule not in lst:
        lst.append(rule)
    elif not add and rule in lst:
        lst.remove(rule)
    settings["permission_rules"] = rules
    _projects.save_project_settings(pid, settings)
    _broadcast_permission_rules(pid)


async def handle_add_permission_rule(ws, cmd: dict):
    """加一条规则到项目层的 allow/deny/ask 列表。"""
    _mutate_project_rule(cmd, add=True)


async def handle_remove_permission_rule(ws, cmd: dict):
    """从项目层移除一条规则。"""
    _mutate_project_rule(cmd, add=False)


# ── 会话额外工作目录（additional-working-directories.md §3.2）──
# 整表替换语义：前端算好增删后发完整列表，幂等、无"重复添加/删不存在"分支。
# 校验失败整帧拒绝（error 帧带原因），不做部分写入。

async def handle_set_working_dirs(ws, cmd: dict):
    """整表替换会话的额外工作目录。dirs 逐条 expanduser + 必须是存在的目录，
    非法条目整帧拒绝，全部合法才落库并广播 working_dirs 帧。"""
    from pathlib import Path
    from openprogram.webui import server as _s
    session_id = (cmd.get("session_id") or "").strip()
    raw_dirs = cmd.get("dirs")
    if not session_id or not isinstance(raw_dirs, list):
        await ws.send_text(json.dumps({
            "type": "error",
            "data": {"message": "set_working_dirs requires session_id and dirs (list)"},
        }))
        return
    dirs: list[str] = []
    for d in raw_dirs:
        expanded = Path(str(d)).expanduser()
        if not expanded.is_dir():
            await ws.send_text(json.dumps({
                "type": "error",
                "data": {"message": f"Not a directory: {d}"},
            }))
            return
        # 存 expanduser 后的绝对路径字符串（不 realpath——用户看到自己选的
        # 路径，归一化交给 check_path_safety 消费端）。
        dirs.append(str(expanded))
    from openprogram.agent.session_config import save_session_run_config
    from openprogram.webui.ws_actions.chat import _db_agent_id
    save_session_run_config(
        session_id,
        agent_id=_db_agent_id(session_id),
        additional_working_dirs=dirs,
    )
    _s._broadcast(json.dumps({"type": "working_dirs", "data": {
        "session_id": session_id, "dirs": dirs,
    }}))


async def handle_set_sandbox(ws, cmd: dict):
    """Read or set the session Sandbox override. Missing sandbox_enabled
    is a read. Response includes backend availability so the Plus menu
    can disable itself when sandbox-exec / bwrap is missing."""
    from openprogram.agent.session_config import (
        load_session_run_config,
        save_session_run_config,
    )
    from openprogram.sandbox import ui_state
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.chat import _db_agent_id

    session_id = (cmd.get("session_id") or "").strip()
    override = None
    requested = cmd.get("sandbox_enabled") if "sandbox_enabled" in cmd else None
    if session_id:
        if "sandbox_enabled" in cmd:
            save_session_run_config(
                session_id,
                agent_id=_db_agent_id(session_id),
                sandbox_enabled=requested,
            )
        override = load_session_run_config(session_id).sandbox_enabled
    # Draft chats have no session row yet, so save is a no-op. Echo the
    # requested override anyway — otherwise ui_state(None) reports the
    # system default (on) and the Plus-menu switch snaps back.
    if override is None and isinstance(requested, bool):
        override = requested
    data = {"session_id": session_id or None, **ui_state(override)}
    frame = json.dumps({"type": "sandbox_changed", "data": data})
    await ws.send_text(frame)
    _s._broadcast(frame)


async def handle_search_messages(ws, cmd: dict):
    """FTS-backed search across past sessions."""
    from openprogram.webui import server as _s
    query = (cmd.get("query") or "").strip()
    agent_id_filter = cmd.get("agent_id") or None
    limit = int(cmd.get("limit") or 50)
    if not query:
        await ws.send_text(json.dumps({
            "type": "search_results",
            "data": {"query": query, "results": [], "total": 0},
        }, default=str))
        return
    try:
        from openprogram.agent.session_db import default_db
        hits = default_db().search_messages(
            query, agent_id=agent_id_filter, limit=limit,
        )
    except Exception as e:
        _s._log(f"[search] failed: {e}")
        hits = []
    results = []
    for h in hits:
        content = h.get("content") or ""
        preview = content.strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "…"
        results.append({
            "session_id": h.get("session_id"),
            "session_title": h.get("session_title"),
            "session_source": h.get("session_source"),
            "message_id": h.get("id"),
            "role": h.get("role"),
            "preview": preview,
            "content": content,
            "timestamp": h.get("timestamp"),
        })
    await ws.send_text(json.dumps({
        "type": "search_results",
        "data": {"query": query, "results": results, "total": len(results)},
    }, default=str))


def _project_name_map() -> tuple[dict[str, str], str]:
    """``(session_id → project name, default/home name)``.

    Lets the sidebar group conversations by project with the home
    folder as the catch-all — every conversation gets a project name, so
    "group by project" never produces an "Ungrouped" bucket. Reads the
    registry once (not per-conversation).
    """
    try:
        from openprogram.store.project import project_store as _ps
        default_name = _ps.get_default_project().name or "Home"
        m: dict[str, str] = {}
        for p in _ps.list_projects():
            if p.is_default:
                continue
            for sid in (p.session_ids or []):
                m[sid] = p.name
        return m, default_name
    except Exception:
        return {}, ""


def build_sessions_list() -> list[dict]:
    """Build the sidebar session list payload from the registry.

    Shared by ``handle_list_sessions`` (per-client reply) and the
    fn-form REST path (broadcast on new-session creation), so both
    produce identical rows.
    """
    from openprogram.agent.session_db import default_db
    _proj_map, _default_proj = _project_name_map()

    rows = default_db().list_sessions(limit=10_000, include_archived=True)
    conv_list: list[dict] = []
    for row in rows:
        sid = row.get("id", "")
        title = (row.get("title") or "").strip()
        preview = (row.get("preview") or "").strip()
        if title in ("", "New conversation", "Untitled") and preview:
            title = preview
        if not title and not preview:
            continue
        conv_list.append({
            "id": sid,
            "title": title or sid,
            "created_at": row.get("created_at") or 0,
            "updated_at": row.get("updated_at") or row.get("created_at") or 0,
            "agent_id": row.get("agent_id"),
            "source": row.get("source"),
            "peer_display": row.get("peer_display"),
            "channel": row.get("channel"),
            "account_id": row.get("account_id"),
            "peer": row.get("peer_id"),
            "preview": preview or None,
            "pinned": bool(row.get("pinned")),
            "archived": bool(row.get("archived")),
            "group": row.get("group") or "",
            "status": row.get("status") or "",
            "unread": bool(row.get("unread")),
            "project": _proj_map.get(sid, _default_proj),
        })
    conv_list.sort(key=lambda c: c.get("updated_at") or 0, reverse=True)
    return conv_list


def broadcast_sessions_list() -> None:
    """Broadcast the current session list to every connected client."""
    from openprogram.webui import server as _s
    _s._broadcast(json.dumps({
        "type": "sessions_list", "data": build_sessions_list(),
    }, default=str))


async def handle_list_sessions(ws, cmd: dict):
    """List sessions from registry (pure memory, no disk I/O)."""
    await ws.send_text(json.dumps({
        "type": "sessions_list", "data": build_sessions_list(),
    }, default=str))


async def handle_mark_session_read(ws, cmd: dict):
    """Mark a conversation as seen + record that this client is viewing it.

    Opening a conversation in the UI is otherwise pure client-side routing,
    so this is the only signal the server gets that a session was looked at.
    Clears the ``unread`` flag (blue dot) and remembers
    ``ws._focused_session_id`` so a later background-run completion knows not
    to re-mark this session unread for a client that's actively on it.
    Broadcasts so every open tab clears the dot together.
    """
    from openprogram.webui import server as _s
    from openprogram.agent.session_db import default_db

    sid = cmd.get("session_id") or None
    ws._focused_session_id = sid
    if not sid:
        return
    with _s._sessions_lock:
        conv = _s._sessions.get(sid)
        if conv is not None:
            conv["unread"] = False
    try:
        default_db().update_session(sid, unread=False)
    except Exception as e:
        _s._log(f"[mark_session_read] {sid}: {e}")
    _s._broadcast(json.dumps({
        "type": "session_updated",
        "data": {"id": sid, "unread": False},
    }, default=str))


ACTIONS = {
    "delete_session": handle_delete_session,
    "mark_session_read": handle_mark_session_read,
    "rename_session": handle_rename_session,
    "update_session_flags": handle_update_session_flags,
    "clear_sessions": handle_clear_sessions,
    "load_session": handle_load_session,
    "get_full_tool_output": handle_get_full_tool_output,
    "get_run_state": handle_get_run_state,
    "follow_up_answer": handle_follow_up_answer,
    "set_working_dirs": handle_set_working_dirs,
    "set_sandbox": handle_set_sandbox,
    "search_messages": handle_search_messages,
    "list_sessions": handle_list_sessions,
    "list_permission_rules": handle_list_permission_rules,
    "add_permission_rule": handle_add_permission_rule,
    "remove_permission_rule": handle_remove_permission_rule,
}

"""Copy a conversation into a NEW session.

``branch_session(session_id, msg_id)`` is the store-level primitive
behind "Copy conversation" in the conversation's ⋯ menu, which copies
up to ``copy_tip`` (HEAD, or the last finished turn while one runs).
It differs from every other fork in the product:

  * Retry / Edit / DAG checkout fork INSIDE the same session (a sibling
    turn under the same predecessor, HEAD moved onto it), so they share
    the session's single run slot and are refused while a turn runs.
  * The ``agent`` tool's ``start_from`` and the ``/task`` inherit path
    open a spawn branch inside the same session too.

This one creates a separate conversation that starts with the history
from the root up to ``msg_id`` and then runs on its own. It only READS
the source session, so it works while the source has a turn in flight:
nothing here consults the source's run state, and the source's nodes,
HEAD and meta are never written.

What is copied, with fresh ids and remapped ``predecessor`` / ``caller``
edges (the same chain the transcript shows for a checkout at
``msg_id``):

  * every chain node from the first turn to ``msg_id``;
  * each chain node's caller subtree (tool calls, nested execution
    nodes, ask-user answers) so every copied reply keeps its tool rows;
  * the nodes a copied compaction summary covers (``covers_ids``), so
    the summary's "original turns" stay expandable;
  * ``context/*`` snapshot nodes anchored on a copied node.

Spawn branches hanging off a copied tool call are not copied (they are
separate conversations of their own). An attach pointer is kept when
what it embeds is copied (it then points into the new session) or is
still readable in its own session; otherwise it is dropped.
"""
from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from typing import Any, Iterable, Optional

from openprogram.state.store.session.nodes import Call, ROLE_USER


_log = logging.getLogger(__name__)

ROOT_ID = "ROOT"

# Session meta carried over so the branch runs with the same agent and
# run configuration: model pin, tool intent, permission mode, thinking
# effort, sandbox switch, working folders and the agent binding. These
# are the fields ``load_session_run_config`` / ``tools_override_from_config``
# and the server's per-session model resolution read.
RUN_CONFIG_FIELDS = (
    "environment_id",
    "provider_override",
    "model_override",
    "provider_name",
    "model",
    "service_tier",
    "tools_override",
    "tools_enabled",
    "web_search",
    "thinking_effort",
    "permission_mode",
    "permission_state",
    "permission_rules",
    "additional_working_dirs",
    "sandbox_enabled",
    "agent_trial",
    "agent_profile_snapshot",
)

# A reply still being produced cannot be the tip of a new conversation:
# its copy would stay "running" forever with nobody writing it.
_UNFINISHED_STATUSES = frozenset({"running", "streaming", "pending", "queued", "in_progress"})


class BranchSessionError(ValueError):
    """A branch request that cannot be served. ``code`` is machine-readable:
    ``unknown_session`` / ``unknown_message`` / ``not_a_turn`` /
    ``reply_in_progress`` / ``broken_chain``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_chain_turn(node: Call) -> bool:
    # Same rule as interfaces/server/routes/chat_branches.is_checkout_target:
    # a node whose caller is another call lives inside an execution subtree.
    return getattr(node, "caller", None) in (None, "", ROOT_ID)


def _is_spawn_root(node: Call) -> bool:
    meta = node.metadata or {}
    return bool(meta.get("spawn_branch_root")) or meta.get("source") == "agent_spawn"


def _attach_info(node: Call) -> tuple[Optional[dict], bool]:
    """``(attach dict, lives_in_extra)`` for an attach-pointer row, else ``(None, False)``."""
    meta = node.metadata or {}
    if meta.get("function") != "attach":
        return None, False
    if isinstance(meta.get("attach"), dict):
        return meta["attach"], False
    extra = meta.get("extra")
    if isinstance(extra, str):
        try:
            decoded = json.loads(extra)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict) and isinstance(decoded.get("attach"), dict):
            return decoded["attach"], True
    elif isinstance(extra, dict) and isinstance(extra.get("attach"), dict):
        return extra["attach"], True
    return {}, False


def _remap_value(value: Any, id_map: dict[str, str]) -> Any:
    """Replace every string equal to a copied node id with its new id."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_remap_value(v, id_map) for v in value]
    if isinstance(value, dict):
        return {k: _remap_value(v, id_map) for k, v in value.items()}
    return value


def _remap_metadata(meta: dict, id_map: dict[str, str], copied: set[str]) -> dict:
    out = _remap_value(copy.deepcopy(meta), id_map)
    extra = out.get("extra")
    if isinstance(extra, str) and extra:
        try:
            decoded = json.loads(extra)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            out["extra"] = json.dumps(_remap_value(decoded, id_map), ensure_ascii=False, default=str)
    # Lineage breadcrumbs that point at a sibling that was not copied.
    for key in ("forked_from", "edit_of"):
        if isinstance(meta.get(key), str) and meta[key] not in copied:
            out.pop(key, None)
    if isinstance(meta.get("covers_ids"), list):
        out["covers_ids"] = [id_map[c] for c in meta["covers_ids"] if c in id_map]
    # Spill stamps name files in the SOURCE session; the writer re-spills.
    out.pop("spilled", None)
    return out


def _fresh_id(old_id: str) -> str:
    """A new id in the same family: ``ctxsp_<hex>`` stays ``ctxsp_…``."""
    head, sep, _tail = old_id.partition("_")
    if sep and head.isalpha() and len(head) <= 12 and len(old_id) <= 32:
        return f"{head}_{uuid.uuid4().hex[:10]}"
    return uuid.uuid4().hex


def _collect(nodes: list[Call], msg_id: str, source_session: str) -> tuple[list[str], set[str]]:
    """``(chain ids root→msg_id, every id to copy)``."""
    by_id = {n.id: n for n in nodes}
    children: dict[str, list[str]] = {}
    for n in nodes:
        if n.caller:
            children.setdefault(n.caller, []).append(n.id)

    chain: list[str] = []
    seen: set[str] = set()
    cur: Optional[str] = msg_id
    while cur and cur != ROOT_ID:
        if cur in seen:
            raise BranchSessionError("broken_chain", f"predecessor cycle at {cur!r}")
        node = by_id.get(cur)
        if node is None:
            raise BranchSessionError(
                "broken_chain",
                f"message {msg_id!r} in {source_session!r} reaches missing node {cur!r}",
            )
        seen.add(cur)
        chain.append(cur)
        if _is_spawn_root(node):
            break  # a sub-agent branch's first node: the chain starts here
        cur = node.predecessor or None
    chain.reverse()

    selected: set[str] = set()
    stack = list(chain)
    while stack:
        nid = stack.pop()
        if nid in selected:
            continue
        node = by_id.get(nid)
        if node is None:
            continue
        selected.add(nid)
        for cid in (node.metadata or {}).get("covers_ids") or []:
            if isinstance(cid, str) and cid not in selected:
                stack.append(cid)
        for kid in children.get(nid, ()):
            kid_node = by_id[kid]
            if _is_spawn_root(kid_node) or kid in selected:
                continue
            stack.append(kid)

    # Context snapshots (system prompt / tool snapshot) anchored on a
    # copied node: the record of what those turns were sent.
    for n in nodes:
        if str(n.name or "").startswith("context/") and n.id not in selected:
            anchor = (n.metadata or {}).get("anchor_head_id")
            if anchor and anchor in selected:
                selected.add(n.id)
    return chain, selected


def _assign_ids(order: Iterable[Call], selected: set[str]) -> dict[str, str]:
    """Fresh ids keeping derived-id conventions (``<user>_reply``,
    ``<reply>_t_<call>``): an id that extends its parent's id extends the
    parent's NEW id the same way."""
    nodes = {n.id: n for n in order}
    id_map: dict[str, str] = {ROOT_ID: ROOT_ID}

    def resolve(old: str, depth: int = 0) -> str:
        if old in id_map:
            return id_map[old]
        node = nodes[old]
        new: Optional[str] = None
        for parent in (node.caller, node.predecessor):
            if (depth < 64 and parent and parent != ROOT_ID and parent in selected
                    and old.startswith(parent) and len(old) > len(parent)):
                new = resolve(parent, depth + 1) + old[len(parent):]
                break
        if new is None:
            new = uuid.uuid4().hex if node.role == ROLE_USER else _fresh_id(old)
        id_map[old] = new
        return new

    for nid in nodes:
        resolve(nid)
    return id_map


def _branch_title(source_title: str, title: Optional[str]) -> str:
    if title and title.strip():
        return title.strip()
    source_title = (source_title or "").strip()
    return f"{source_title} (copy)" if source_title else ""


def copy_tip(session_id: str, *, running: bool, store: Any = None) -> str:
    """The message a copy of the whole conversation ends at.

    That is HEAD, walked up past what is not a finished conversation turn:
    a reply still being written, a function-internal node, and, while a
    turn runs (``running``), the user turn it answers. The caller passes
    the run state; this reads the store only. Raises
    :class:`BranchSessionError` when nothing finished is left to copy.
    """
    if store is None:
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
    session_id = (session_id or "").strip()
    source = store.get_session(session_id) if session_id else None
    if source is None:
        raise BranchSessionError("unknown_session", f"unknown session {session_id!r}")
    by_id = {n.id: n for n in store.get_nodes(session_id)}
    cur: Optional[str] = source.get("head_id")
    seen: set[str] = set()
    while cur and cur != ROOT_ID and cur not in seen:
        seen.add(cur)
        node = by_id.get(cur)
        if node is None or (node.metadata or {}).get("display") == "root":
            break
        unfinished = str((node.metadata or {}).get("status") or "") in _UNFINISHED_STATUSES
        if _is_chain_turn(node) and not unfinished and not (running and node.role == ROLE_USER):
            return cur
        caller = node.caller if node.caller not in (None, "", ROOT_ID) else None
        cur = node.predecessor or caller
    raise BranchSessionError("unknown_message", "this conversation has no finished turn to copy yet")


def _carry_checkpoints(store: Any, source: str, target: str, id_map: dict[str, str]) -> None:
    """Keep "restore code" working on copied messages; a failure only loses that."""
    from openprogram.state.store.snapshot.checkpoint.prompt_checkpoints import carry_checkpoints
    try:
        session_dir = getattr(store, "_session_dir", None)
        if session_dir is None:
            from openprogram.state.store.session.session_store import default_store
            session_dir = default_store()._session_dir
        carry_checkpoints(session_dir(source), session_dir(target), id_map, source_session_id=source)
    except Exception:  # noqa: BLE001 — the conversation copy is already complete
        _log.warning("branch %s: checkpoints not carried", target, exc_info=True)


def branch_session(
    session_id: str,
    msg_id: str,
    *,
    title: Optional[str] = None,
    store: Any = None,
    branch_id: Optional[str] = None,
) -> str:
    """Copy the chain root→``msg_id`` of ``session_id`` into a new session.

    Returns the new session id. The new session is in the source's
    project and environment, bound to the same agent, carries the same
    run configuration, has HEAD on the copy of ``msg_id`` and records
    ``branched_from: {"session_id", "msg_id"}`` in its meta.

    Raises :class:`BranchSessionError` for an unknown session or
    message, a function-internal node, or a reply still being written.
    """
    from openprogram.state.store import SessionNodeWriter

    if store is None:
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
    session_id = (session_id or "").strip()
    msg_id = (msg_id or "").strip()
    if not session_id:
        raise BranchSessionError("unknown_session", "session_id is required")
    if not msg_id:
        raise BranchSessionError("unknown_message", "msg_id is required")
    source = store.get_session(session_id)
    if source is None:
        raise BranchSessionError("unknown_session", f"unknown session {session_id!r}")

    # Point-in-time copies: a turn running in the source keeps mutating
    # its live nodes while this reads them.
    nodes = [copy.deepcopy(n) for n in store.get_nodes(session_id)]
    by_id = {n.id: n for n in nodes}
    hidden = set(source.get("rewind_hidden_nodes") or []) | set(source.get("rewind_hidden_heads") or [])
    target = None if msg_id in hidden else by_id.get(msg_id)
    if target is None or (target.metadata or {}).get("display") == "root":
        raise BranchSessionError("unknown_message", f"unknown message {msg_id!r} in {session_id!r}")
    if not _is_chain_turn(target):
        raise BranchSessionError(
            "not_a_turn",
            "a function-internal node is not a conversation turn and cannot start a branch",
        )
    if str((target.metadata or {}).get("status") or "") in _UNFINISHED_STATUSES:
        raise BranchSessionError(
            "reply_in_progress",
            "this reply is still being written; branch from it once it finishes",
        )

    chain, selected = _collect(nodes, msg_id, session_id)

    # Attach pointers: keep only those whose embedded branch is copied
    # or still readable where it lives.
    dropped: set[str] = set()
    for nid in sorted(selected):
        info, _in_extra = _attach_info(by_id[nid])
        if info is None:
            continue
        ref_session = info.get("session_id") or session_id
        ref_head = info.get("head_id") or ""
        if ref_session == session_id and ref_head in selected:
            continue
        try:
            readable = bool(ref_head) and store.message_exists(ref_session, ref_head)
        except Exception:
            readable = False
        if not readable:
            dropped.add(nid)
    if dropped:
        # Drop the pointer and everything hanging under it.
        frontier = set(dropped)
        while frontier:
            frontier = {n.id for n in nodes if n.id in selected and n.caller in frontier} - dropped
            dropped |= frontier
        selected -= dropped

    ordered = [n for n in nodes if n.id in selected]  # get_nodes is seq-sorted
    id_map = _assign_ids(ordered, selected)
    new_session_id = f"local_{uuid.uuid4()}"

    from openprogram.state.store.session.branch_config import config_for_branch
    refs = source.get("branch_refs") or {}
    candidates = [key for key, ref in refs.items() if ref.get("head_id") == msg_id]
    selected_branch = branch_id
    if selected_branch is not None:
        # Copying a running branch stops at its last finished turn. Its
        # settings still belong to the selected branch whose tip is later.
        cursor = refs.get(selected_branch, {}).get("head_id")
        visited = set()
        while cursor and cursor != msg_id and cursor not in visited:
            visited.add(cursor)
            node = by_id.get(cursor)
            cursor = node.predecessor if node is not None else None
        if cursor != msg_id:
            raise BranchSessionError("unknown_branch", "The message does not belong to the selected branch.")
    if selected_branch is None:
        active = source.get("active_branch_id")
        if active in candidates:
            selected_branch = active
        elif len(candidates) == 1:
            selected_branch = candidates[0]
        elif len(candidates) > 1:
            raise BranchSessionError("branch_selection_required", "Select which branch configuration to copy.")
    if selected_branch is not None:
        config = config_for_branch(source, selected_branch)
        source = {**source, **config, "extra_meta": {**(source.get("extra_meta") or {}), **config}}
    extra_meta = source.get("extra_meta") or {}

    def _source_field(key: str) -> Any:
        if key in extra_meta:
            return extra_meta[key]
        return source.get(key)

    fields: dict[str, Any] = {}
    for key in RUN_CONFIG_FIELDS:
        value = _source_field(key)
        if value is not None:
            fields[key] = copy.deepcopy(value)
    project_id = _source_field("project_id")
    if project_id:
        fields["project_id"] = project_id
    fields["branched_from"] = {"session_id": session_id, "msg_id": msg_id}
    new_title = _branch_title(source.get("title") or "", title)
    if title and title.strip():
        fields["_user_titled"] = True
    elif new_title:
        # The copied history already has a title: keep the turn-1 backfill
        # from overwriting it while the progressive LLM re-titles stay live.
        fields["_auto_titled"] = True
        fields["_title_gen_count"] = max(1, int(extra_meta.get("_title_gen_count") or 0))

    store.create_session(
        new_session_id,
        source.get("agent_id") or "main",
        title=new_title,
        **fields,
    )
    work_dir = _source_field("work_dir")
    if isinstance(work_dir, str) and work_dir.strip():
        # After create: a work_dir passed to create_session would re-resolve
        # the project from the folder instead of keeping the source's.
        store.update_session(new_session_id, work_dir=work_dir)

    writer = SessionNodeWriter(store, new_session_id, advance_head=False)
    root = by_id.get(ROOT_ID)
    writer.append(Call(
        id=ROOT_ID,
        created_at=(root.created_at if root is not None else time.time()),
        role=ROLE_USER, output="", metadata={"display": "root"},
    ))
    first = chain[0] if chain else None
    for node in ordered:
        if node.id == ROOT_ID:
            continue
        meta = _remap_metadata(node.metadata or {}, id_map, selected)
        caller = node.caller or ""
        predecessor = node.predecessor or None
        if node.id == first:
            # The chain's first node opens the new conversation at ROOT,
            # including a sub-agent branch root branched from directly.
            caller = ROOT_ID if node.role == ROLE_USER else ""
            predecessor = ROOT_ID
            meta.pop("spawn_branch_root", None)
            if meta.get("source") == "agent_spawn":
                meta.pop("source", None)
        new_caller = id_map.get(caller, "") if caller else ""
        new_pred = id_map.get(predecessor) if predecessor else None
        info, in_extra = _attach_info(node)
        if info:
            ref_session = info.get("session_id") or session_id
            ref_head = info.get("head_id") or ""
            if ref_session == session_id and ref_head in selected:
                patched = {**info, "session_id": new_session_id, "head_id": id_map[ref_head]}
                patched.pop("source_commit_id", None)  # a commit of the SOURCE session
            else:
                patched = {**info, "session_id": ref_session}
            if in_extra:
                try:
                    decoded = json.loads(meta["extra"]) if isinstance(meta.get("extra"), str) else dict(meta.get("extra") or {})
                except (TypeError, ValueError):
                    decoded = {}
                decoded["attach"] = patched
                meta["extra"] = json.dumps(decoded, ensure_ascii=False, default=str)
            else:
                meta["attach"] = patched
        writer.append(Call(
            id=id_map[node.id],
            created_at=node.created_at,
            role=node.role,
            name=node.name,
            input=copy.deepcopy(node.input),
            output=copy.deepcopy(node.output),
            caller=new_caller,
            predecessor=new_pred,
            reads=[id_map[r] for r in (node.reads or []) if r in id_map],
            metadata=meta,
        ))

    _carry_checkpoints(store, session_id, new_session_id, id_map)
    store.set_head(new_session_id, id_map[msg_id])
    store.update_session(new_session_id, status="idle")
    try:
        store.commit_turn(new_session_id, f"branch from {session_id} at {msg_id}")
    except Exception:  # noqa: BLE001 — the history files are written; a commit is bookkeeping
        _log.warning("branch %s: git commit skipped", new_session_id, exc_info=True)
    return new_session_id


__all__ = ["BranchSessionError", "RUN_CONFIG_FIELDS", "branch_session"]

"""ContextCommit 增量生成 — 从上一个 context commit 复制 + 加新节点 + 跑规则.

核心循环: 每个 turn 跑一次 generate_commit(), 产生新 context commit.

不重算: items 是从 parent context commit 直接复制过来的, 已 locked 的 item
任何规则都不会再动它们。新增的 DAG 节点先以 state=full 加入, 然后
规则可能把新增的 / 边界移动到 aging 区的少数 item 标记成 aged/cleared。

规则 pipeline 是固定顺序写死的列表 (从 rules/__init__.py 拿)。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Optional

from .types import ContextItem, ContextCommit, CURRENT_RULES_VERSION


def _estimate_tokens(text: str | None) -> int:
    """粗略 token 估算: 1 token ≈ 4 字符. 替代成真正的 tokenizer 不影响逻辑."""
    if not text:
        return 0
    return max(4, len(text) // 4)


def _gen_commit_id() -> str:
    return uuid.uuid4().hex[:12]


def generate_commit(
    *,
    store,                              # SessionStore
    session_id: str,
    parent_commit,                  # Optional[ContextCommit]
    new_nodes: list[dict[str, Any]],  # 这轮新增的 DAG 节点 (legacy msg dict 格式)
    head_node_id: str,
    budget_total: int,
    budget_summarize_threshold: int,
    fetch_node: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    llm_summarize: Optional[Callable] = None,
) -> ContextCommit:
    """生成新 context commit 并持久化, 返回它.

    new_nodes 是这一轮真正新加进 DAG 的节点 (legacy msg dict 格式: 含
    id/role/content/parent_id/caller/extra/...). 旧 items 从 parent
    context commit 直接复制, 不需要从 DAG 重读。
    """
    from .store import save_commit
    from ..rules import RULE_PIPELINE
    from ..rules._base import RuleContext

    # Step 1: 起点 — 上一 context commit 的 items 拷一份, 全部沿用
    if parent_commit is not None:
        items: list[ContextItem] = [_copy_item(i) for i in parent_commit.items]
    else:
        items = []

    commit_id = _gen_commit_id()

    # Build the dedup set: any source_commit_id already represented by
    # at least one attached_from item in the parent commit has been
    # expanded before, so a second attach pointer pointing at the same
    # source commit becomes a no-op (no duplicate items).
    already_attached: set[str] = set()
    for it in items:
        if it.attached_from:
            already_attached.add(it.attached_from)

    # Step 2: 追加这轮新增的 DAG 节点为 state="full" 新 item
    for node in new_nodes:
        new_items = _build_items_from_node(
            node, commit_id, already_attached, store, session_id,
        )
        items.extend(new_items)

    # Step 3: 跑规则 pipeline (只动 unlocked, 已 locked 跳过)
    ctx = RuleContext(
        commit_id=commit_id,
        session_id=session_id,
        now=time.time(),
        head_node_id=head_node_id,
        budget_total=budget_total,
        budget_summarize_threshold=budget_summarize_threshold,
        fetch_node=fetch_node,
        llm_summarize=llm_summarize,
    )
    for rule in RULE_PIPELINE:
        rule(items, ctx)

    # Step 4: 计算 token 总和, 写 context commit
    total = sum(i.tokens for i in items if i.state != "summarized")
    commit = ContextCommit(
        id=commit_id,
        session_id=session_id,
        parent_id=parent_commit.id if parent_commit else None,
        created_at=time.time(),
        head_node_id=head_node_id,
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=total,
        items=items,
        summary=_describe_changes(items, parent_commit, commit_id),
    )
    save_commit(store, commit)
    return commit


def _parse_attach_blob(node: dict) -> dict:
    """Pull the ``attach`` dict out of a node's metadata.

    Tolerates both wire formats: ``node["attach"]`` (already-parsed
    dict shipped by the WS layer) and ``node["extra"]`` (JSON string
    with a nested ``{"attach": {...}}``). Returns ``{}`` when neither
    form yields a dict — caller treats that as a malformed attach
    pointer and falls back to the legacy single-item path.
    """
    raw = node.get("attach") or node.get("extra")
    if isinstance(raw, dict):
        if "attach" in raw and isinstance(raw["attach"], dict):
            return raw["attach"]
        return raw
    if isinstance(raw, str) and raw:
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                if "attach" in parsed and isinstance(parsed["attach"], dict):
                    return parsed["attach"]
                return parsed
        except Exception:
            return {}
    return {}


def _build_items_from_node(
    node: dict,
    commit_id: str,
    already_attached: set[str],
    store,
    session_id: str,
) -> list[ContextItem]:
    """legacy msg dict → list[ContextItem].

    Returns:
      * ``[]`` — node should not enter context (system nodes etc, or
        attach pointer already expanded in an ancestor commit).
      * ``[ContextItem, ...]`` — one item for a regular node; for an
        attach pointer with a resolvable ``source_commit_id``, returns
        ``[open_marker, *expanded_items, close_marker]`` so the
        attached block is delimited.
    """
    role = node.get("role")
    if role not in ("user", "assistant", "tool"):
        return []
    content = node.get("content") or ""
    # tool 节点的 content 可能是 dict, 转成字符串
    if not isinstance(content, str):
        try:
            import json as _json
            content = _json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            content = str(content)

    is_attach = node.get("function") == "attach"
    if not is_attach:
        # Tool nodes get the tool name baked into the rendered text so
        # views.py's text rendering of tool items carries enough context
        # for the LLM ("[bash]\n<output>" instead of bare output).
        if role == "tool":
            tool_name = (node.get("name") or "").strip()
            rendered = f"[{tool_name}]\n{content}" if tool_name else content
        else:
            rendered = content
        return [ContextItem(
            source_node_id=node.get("id") or "",
            role=role,
            state="full",
            locked=False,
            rendered=rendered,
            tokens=_estimate_tokens(rendered),
            state_set_at=commit_id,
            reason="new",
        )]

    # ── Attach pointer ─────────────────────────────────────────
    attach = _parse_attach_blob(node)
    label = (attach.get("label") if isinstance(attach, dict) else "") or ""
    label = label.strip()
    source_commit_id = (attach.get("source_commit_id") or "").strip() or None
    # ``is_base`` (set by merge prompt prep) tells the generator to
    # lock the attached items so summarize/aging can't drop the base
    # branch's content out of the merge prompt — see scenario D in
    # docs/design/context-attach-merge.md.
    is_base = bool(attach.get("is_base"))

    # Fallback path: no source commit recorded OR can't load it OR the
    # source commit had no items. Renders as a single user-role item
    # carrying ``content`` — legacy attach behaviour, preserves
    # backward compat with attach rows written before this refactor.
    src_commit = None
    if source_commit_id:
        try:
            from .store import load_commit
            src_commit = load_commit(
                store, source_commit_id, session_id=session_id,
            )
        except Exception:
            src_commit = None
        # Cross-session: source lives in a different session_id than
        # the one we're committing into. Caller didn't pass that
        # session id; ``load_commit`` falls back to a global scan
        # which already works, so no extra plumbing needed.
        if src_commit is None:
            try:
                from .store import load_commit
                src_commit = load_commit(store, source_commit_id)
            except Exception:
                src_commit = None

    if src_commit is None:
        intro = (
            f"[以下是从分支 \"{label}\" 附加进来的内容]"
            if label
            else "[以下是从其它分支附加进来的内容]"
        )
        return [ContextItem(
            source_node_id=node.get("id") or "",
            role="user",
            state="full",
            locked=False,
            rendered=f"{intro}：\n{content}",
            tokens=_estimate_tokens(f"{intro}：\n{content}"),
            state_set_at=commit_id,
            reason="attached_legacy",
            attached_from=None,
        )]

    # Dedup: parent commit already has at least one item with
    # ``attached_from == source_commit_id``. Second attach pointer
    # pointing at the same source commit is a no-op (the items are
    # already there).
    if source_commit_id in already_attached:
        return []
    # Mark this source as expanded so a *second* attach pointer
    # appearing in the same new_nodes list also gets deduped.
    already_attached.add(source_commit_id)

    short_hex = source_commit_id[:8] if source_commit_id else "?"
    open_text = (
        f"[以下是从分支 \"{label}\" — commit {short_hex} 附加进来的内容]："
        if label
        else f"[以下是从分支 — commit {short_hex} 附加进来的内容]："
    )
    close_text = (
        f"[分支 \"{label}\" 附加内容结束]"
        if label
        else "[附加内容结束]"
    )

    out: list[ContextItem] = []
    out.append(ContextItem(
        source_node_id=node.get("id") or "",
        role="user",
        state="full",
        locked=is_base,
        rendered=open_text,
        tokens=_estimate_tokens(open_text),
        state_set_at=commit_id,
        reason="attached_open",
        attached_from=source_commit_id,
    ))
    # IMPORTANT: every item from the source commit gets re-emitted as
    # role="user" with a narrative prefix that says *who* produced it
    # in the sub-branch. Without this the source's assistant items
    # land in the receiving lane's context as the parent agent's OWN
    # assistant lines — the parent LLM then thinks it already said
    # those things and just echoes the last one. Reframing the whole
    # block as user-role narration makes the parent agent treat the
    # attached content as a *report* about what a sub-agent did, not
    # as its own dialogue history. (See docs/design/context-attach-
    # merge.md scenario B and the "毫无逻辑的 echo" issue.)
    for src_item in src_commit.items:
        # Skip already-summarized items in the source — they contribute
        # no rendering and would just confuse the LLM with "" content.
        if src_item.state == "summarized":
            continue
        orig_role = src_item.role
        rendered = src_item.rendered or ""
        if orig_role == "user":
            prefix = f"[子 agent \"{label}\" 收到的指令]" if label else "[子 agent 收到的指令]"
        elif orig_role == "assistant":
            prefix = f"[子 agent \"{label}\" 的回复]" if label else "[子 agent 的回复]"
        elif orig_role == "tool":
            # views.py also wraps tool items in "[tool_result]"; keep
            # the narration consistent so the parent sees a clear chain.
            prefix = f"[子 agent \"{label}\" 工具调用结果]" if label else "[子 agent 工具调用结果]"
        elif orig_role == "summary":
            prefix = f"[子 agent \"{label}\" 早期对话摘要]" if label else "[子 agent 早期对话摘要]"
        else:
            prefix = f"[子 agent {orig_role}]"
        new_rendered = f"{prefix}\n{rendered}" if rendered else prefix
        out.append(ContextItem(
            source_node_id=src_item.source_node_id,
            role="user",
            # Reset state/locked: the rule pipeline re-evaluates the
            # attached content from scratch on the receiving branch.
            # Exception: ``is_base`` (merge base peer) locks everything
            # so summarize/aging never folds the base out.
            state="full",
            locked=is_base,
            rendered=new_rendered,
            tokens=_estimate_tokens(new_rendered),
            state_set_at=commit_id,
            reason="attached_base" if is_base else "attached",
            attached_from=source_commit_id,
        ))
    out.append(ContextItem(
        source_node_id=node.get("id") or "",
        role="user",
        state="full",
        locked=is_base,
        rendered=close_text,
        tokens=_estimate_tokens(close_text),
        state_set_at=commit_id,
        reason="attached_close",
        attached_from=source_commit_id,
    ))
    return out


def _build_item_from_node(node: dict, commit_id: str) -> Optional[ContextItem]:
    """Back-compat shim — single-item version of _build_items_from_node.

    Kept for callers that don't have a store / session_id context
    (e.g. unit tests for a single node). Returns the first non-marker
    item, or None for nodes that produce nothing. Doesn't load source
    commits — attach nodes always fall through to the legacy
    single-item path here.
    """
    items = _build_items_from_node(
        node,
        commit_id,
        already_attached=set(),
        store=None,
        session_id="",
    )
    if not items:
        return None
    # If it's an attach with markers, the legacy single-item callers
    # want one item back — pick the first non-marker (or just the
    # first if no markers).
    for it in items:
        if it.reason not in ("attached_open", "attached_close"):
            return it
    return items[0]


def _copy_item(item: ContextItem) -> ContextItem:
    """浅拷贝 ContextItem. dataclass 默认是 mutable, 不拷贝会被规则改到 parent commit 的 item."""
    return ContextItem(
        source_node_id=item.source_node_id,
        role=item.role,
        state=item.state,
        locked=item.locked,
        rendered=item.rendered,
        tokens=item.tokens,
        state_set_at=item.state_set_at,
        reason=item.reason,
        merged_into=item.merged_into,
        is_anchor=item.is_anchor,
        anchor_for_summary=item.anchor_for_summary,
        attached_from=item.attached_from,
    )


def _describe_changes(items, parent_snap, commit_id: str) -> str:
    """生成 1 行变化描述, UI timeline 显示用."""
    from collections import Counter
    counts = Counter(i.state for i in items)
    new_count = sum(1 for i in items if i.state_set_at == commit_id and i.reason == "new")
    parts = [f"new={new_count}"]
    for s in ("full", "aged", "cleared", "summarized", "summary"):
        if counts.get(s):
            parts.append(f"{s}={counts[s]}")
    return ", ".join(parts)

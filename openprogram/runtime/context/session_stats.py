"""Single source of truth for "how full is this session's context right now".

Both readers of context occupancy go through here:

- the composer's progress ring (pushed over WS as ``context_stats``), and
- the ``/context`` breakdown panel (``GET /api/sessions/{id}/context``).

They therefore always show the same number.

Two bases produce that number:

``measured``
    A real request just finished. ``total_used`` is what the provider
    billed for that call's prompt (``input_tokens + cache_read``) — the
    ground truth for the graph as it stood at request time.

``estimated``
    The graph changed since the last request (compaction landed, model
    switched, a branch was checked out or deleted). ``total_used`` is
    recomputed from the current *rendered* view with the local
    tokenizer, so the ring moves the moment the graph moves instead of
    lagging a request behind.

The history this module counts is always ``rendered_history`` (active
summary + uncovered tail) — the view the next model call actually
reads. Occupancy must not walk the raw ``get_branch`` / ``_get_messages``
cache; those keep covered turns for the transcript.

A measured reading also calibrates the estimator: ``calibration`` records
measured/estimated for the same graph, which tells a reader how far the
local estimate drifts from what the provider charges.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

_log = logging.getLogger(__name__)


def _bare_profile(agent_id: str) -> dict:
    return {"id": agent_id}


# How an agent id becomes its saved profile. The conversation package,
# which owns agent profiles, registers its loader on import; until then a
# profile is just its id.
_profile_loader: Callable[[str], dict] = _bare_profile


def set_agent_profile_loader(loader: "Callable[[str], dict] | None") -> None:
    """Register how breakdowns load an agent's profile (``None`` resets)."""
    global _profile_loader
    _profile_loader = loader or _bare_profile


def load_agent_profile(agent_id: str) -> dict:
    """The agent's saved profile, through the registered loader."""
    return _profile_loader(agent_id)


def model_ref_window(model: Optional[str], provider: Optional[str] = None) -> Optional[int]:
    """Context window of the model a stored reference names, or ``None``.

    ``model`` is ``provider/model``, ``provider:model`` or a bare id under
    ``provider`` (``catalog.models.split_model_ref``). The window is the
    one compaction reads for the same Model; an unresolvable reference is
    ``None``, never a default.
    """
    if not model:
        return None
    try:
        from openprogram.models.providers.catalog.models import find_model_ref
        from openprogram.runtime.context.tokens import known_context_window
        return known_context_window(find_model_ref(model, provider))
    except Exception:
        _log.debug("model window not resolved for %r", model, exc_info=True)
        return None


def session_model_window(sess: dict) -> tuple[str, Optional[int]]:
    """``(model id, window)`` for a session row.

    The branch's own pick (``provider_override`` / ``model_override``)
    first, then the model its last turn ran (``model``, written as
    ``provider/model``). The first one named decides; the id is bare and
    the window is ``None`` when that model does not resolve.
    """
    from openprogram.models.providers.catalog.models import split_model_ref

    extra = sess.get("extra_meta") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    fallback = sess.get("provider_name") or None
    candidates = (
        (sess.get("model_override") or extra.get("model_override"),
         sess.get("provider_override") or extra.get("provider_override") or fallback),
        (sess.get("model"), fallback),
    )
    for model, provider in candidates:
        if model:
            return (split_model_ref(model, provider)[1] or "",
                    model_ref_window(model, provider))
    return "", None


BREAKDOWN_CATEGORY_KEYS = (
    "system_prompt",
    "tools_schema",
    "tools_deferred_catalog",
    "mcp_tools",
    "mcp_tools_deferred",
    "memory",
    "skills",
    "messages",
    "unclassified",
)


def compute_breakdown(
    session_id: str,
    head_id: Optional[str] = None,
    *,
    context_window: Optional[int] = None,
) -> dict:
    """Per-category input-token breakdown for a session branch.

    Storage rule: the live compute stays the source of truth. Callers that
    already run this on a graph change (the ring refresh) should persist the
    returned snapshot so the /context panel can paint without recomputing.

    ``head_id`` is the DAG branch tip the user is looking at; a session can
    hold several branches, so the caller passes the current head and the
    breakdown describes that branch. ``None`` falls back to the session's
    global head.
    """
    from openprogram.runtime.context.session_db import default_db

    db = default_db()
    from openprogram.runtime.context.persistence import rendered_history
    branch = rendered_history(db, session_id, head_id) or []
    sess = db.get_session(session_id) or {}
    from openprogram.runtime.context.system_prompt_node import latest_recorded_prompt
    from openprogram.runtime.context.tool_snapshot_node import (
        latest_recorded_tool_snapshot,
    )
    from openprogram.runtime.context.system_prompt_node import latest_recorded_sections
    latest_system = latest_recorded_prompt(db, session_id, head_id) or ""
    recorded_sections = (
        latest_recorded_sections(db, session_id, head_id) if latest_system else None
    )
    tool_snapshot = latest_recorded_tool_snapshot(db, session_id, head_id)
    has_tool_snapshot = tool_snapshot is not None
    recorded_tool_rows = list((tool_snapshot or {}).get("tools") or [])

    # Section notes count where the requests send them: a conversation's
    # turns render them, and the latest turn on the branch records whether
    # its requests render the memory section's.
    from openprogram.runtime.context.turn_context import block as _turn_block, conversation_audience
    audience = conversation_audience(next(
        (bool(m.get("memory_map_notes")) for m in reversed(branch)
         if isinstance(m, dict) and m.get("role") == "user"), False))

    # 分支消息 + 从 extra(JSON) 里挖最近一次调用记下的原料
    # （tools_available / system_prompt）。
    msgs = []
    latest_tools = []
    for m in branch:
        content = m.get("content") or ""
        from openprogram.runtime.context.render import with_turn_block
        if m.get("role") == "user":
            content = with_turn_block(content, _turn_block(m, audience))
        # content 只是**可见文本**。一条 assistant 消息回填进下一轮
        # context 时还带着 thinking 块和 tool_call 的 JSON —— 这些都
        # 占 token。extra.blocks 里存了完整结构（thinking / text /
        # tool_use），只算 content 会让 Messages 一档严重虚低（漏掉
        # 往往比可见回复更长的 thinking）。这里把结构化块也拼进来估算。
        extra = m.get("extra")
        if extra:
            try:
                ex = json.loads(extra) if isinstance(extra, str) else extra
                if isinstance(ex, dict):
                    if not has_tool_snapshot and ex.get("tools_available"):
                        latest_tools = ex["tools_available"]
                    if not latest_system and ex.get("system_prompt"):
                        latest_system = ex["system_prompt"]
                    parts = [content]
                    has_tool_blocks = False
                    for blk in ex.get("blocks") or []:
                        if not isinstance(blk, dict):
                            continue
                        bt = blk.get("type")
                        if bt == "thinking":
                            parts.append(blk.get("text") or "")
                        elif bt == "text":
                            # blocks.text 与顶层 content 常常重复，只在
                            # content 为空时补，避免重复计数。
                            if not content:
                                parts.append(blk.get("text") or "")
                        elif bt in {"tool", "tool_result"}:
                            has_tool_blocks = True
                            try:
                                parts.append(json.dumps(
                                    {
                                        "tool": blk.get("tool") or blk.get("name"),
                                        "input": blk.get("input"),
                                        "result": blk.get("result", blk.get("content")),
                                        "is_error": blk.get("is_error", False),
                                    },
                                    ensure_ascii=False,
                                    default=str,
                                ))
                            except (TypeError, ValueError):
                                _log.debug(
                                    "tool block not counted for session %s",
                                    session_id, exc_info=True,
                                )
                    for tc in ([] if has_tool_blocks else ex.get("tool_calls") or []):
                        try:
                            parts.append(
                                json.dumps(tc, ensure_ascii=False, default=str)
                            )
                        except (TypeError, ValueError):
                            # One unserializable call drops out of the
                            # estimate; the ring reads slightly low.
                            _log.debug(
                                "tool call not counted for session %s",
                                session_id, exc_info=True)
                    content = "\n".join(p for p in parts if p)
            except (TypeError, ValueError):
                # Unparseable extra: the message counts by its visible
                # text alone, without its thinking and tool-call blocks.
                _log.debug("message extra not parsed for session %s",
                           session_id, exc_info=True)
        msgs.append({
            "role": m.get("role") or "",
            "content": content,
            "metadata": {},
        })

    # The caller's window is the model the turn resolved; without one, the
    # session row's model decides (``session_model_window``).
    model_id, row_window = session_model_window(sess)
    ctx_window = int(context_window or 0) or row_window or 0

    # 工具集：优先用节点存的原料（精确）；没有（如 codex 等自带
    # runtime 的 provider 没走采集路径）就回退到会话当前 toolset，
    # 这样 /context 对所有 provider 都能显示 tools/per-tool。
    try:
        from openprogram.programs import agent_tools as _agent_tools
        if has_tool_snapshot:
            tools = []
        elif latest_tools:
            tools = _agent_tools(names=list(latest_tools))
        else:
            tools = _agent_tools(toolset="full")
    except Exception:
        tools = []

    agent_view = None
    # system_prompt：优先节点原料；没有就按会话 agent 重建（identity +
    # tool_use 指引 + skills…），让所有 provider 的 /context 都能显示
    # System 类真实 token，而非缺省 0。
    if not latest_system:
        try:
            from openprogram.runtime.context.components import (
                build_system_prompt,
            )

            class _AgentView:
                def __init__(self, d):
                    self.__dict__.update(d)

            prof = load_agent_profile(sess.get("agent_id") or "main")
            if isinstance(prof, dict):
                latest_system = build_system_prompt(_AgentView(prof))
                agent_view = _AgentView(prof)
        except Exception:
            latest_system = ""

    message_rows: list[dict] = []
    try:
        from openprogram.runtime.context.compaction.view import load_compaction_view
        view = load_compaction_view(db, session_id, head_id, audience=audience)
        msgs = view.messages
        message_rows = view.history
    except (AttributeError, KeyError, TypeError):
        _log.debug("DAG view unavailable; using legacy message estimate", exc_info=True)

    return _classify(
        session_id=session_id, sess=sess, model_id=model_id,
        latest_system=latest_system, recorded_sections=recorded_sections,
        agent_view=agent_view, msgs=msgs, message_rows=message_rows,
        branch=branch, tools=tools, latest_tools=latest_tools,
        has_tool_snapshot=has_tool_snapshot,
        recorded_tool_rows=recorded_tool_rows, ctx_window=ctx_window,
    )


def _classify(
    *,
    session_id: Optional[str],
    sess: dict,
    model_id: str,
    latest_system: str,
    recorded_sections: Optional[list],
    agent_view,
    msgs: list,
    message_rows: list,
    branch: list,
    tools: list,
    latest_tools: list,
    has_tool_snapshot: bool,
    recorded_tool_rows: list,
    ctx_window: int,
) -> dict:
    """Categories and detail rows for one prompt, history and toolset."""
    from openprogram.runtime.context.breakdown import compute_call_breakdown

    bd = compute_call_breakdown(
        system_prompt=latest_system,
        history=msgs,
        tools=tools,
        context_window=ctx_window,
    )
    bd["session_id"] = session_id
    bd["model"] = model_id
    # 0 means the model's window is unknown: no limit is shown.
    bd["context_window"] = ctx_window or None
    bd["tools_source"] = (
        "recorded_snapshot" if has_tool_snapshot else
        ("recorded" if latest_tools else "session_default")
    )

    # 完整 /context（对齐 Claude Code）：补 skills / memory / mcp 明细，
    # 每项列名字 + token。best-effort，任一块失败不影响主分类。
    from openprogram.runtime.context.tokens import _text_tokens

    def _t(s: str) -> int:
        # Skills / memory / deferred catalogs are substrings of the one
        # system message. Count their text only; adding message overhead to
        # every subcomponent would count the same envelope several times.
        return _text_tokens(s or "")

    # Skills：按 source 分组，列每个 skill。口径对齐 Claude Code——
    # 系统提示里每个 skill 只占「name: 一行描述」的索引条目（skill
    # 正文按需加载、不常驻），所以算 name + description 首行，而非 body 全文。
    try:
        from openprogram.extensions.skills import loader as _sl
        sk_items = []
        skill_match = re.search(
            r"<available_skills>[\s\S]*?</available_skills>",
            latest_system,
        )
        skill_block = skill_match.group(0) if skill_match else ""
        for s in (_sl.list_skills() if skill_block else []):
            name = getattr(s, "name", "") or ""
            if name not in skill_block:
                continue
            desc = (getattr(s, "description", "") or "").splitlines()
            line = f"{name}: {desc[0] if desc else ''}"
            sk_items.append({
                "name": name,
                "source": getattr(s, "source", "") or "",
                "tokens": _t(line),
            })
        sk_items.sort(key=lambda x: -x["tokens"])
        bd["skills_detail"] = sk_items
        bd["skills"] = _t(skill_block)
    except Exception:
        bd["skills_detail"] = []

    # The system prompt by component (base prompt, environment, AGENTS.md,
    # memory, capabilities, …). The turn records each block's length next
    # to the prompt; an older node is split by locating the blocks of a
    # fresh build in it.
    sections = _prompt_sections(
        latest_system, recorded_sections, agent_view,
        sess.get("agent_id") or "main",
    )

    # Memory files: only what is always in the system prompt — each
    # workspace's MEMORY.md map and core.md. Topics and sources are
    # searched on demand and are not part of the context.
    memory_block = "\n\n".join(
        block for name, block in sections if name == "memory")
    bd["memory"] = _t(memory_block)
    bd["memory_detail"] = _memory_files(session_id, memory_block, _t)

    # Loaded/deferred and built-in/MCP are mutually exclusive top-level
    # categories. compute_call_breakdown already prices each tool with the
    # same rule used for its aggregate, so split that frozen list instead of
    # reading the live global registry a second time.
    if has_tool_snapshot:
        tool_rows = [
            {
                "name": str(row.get("name") or ""),
                "tokens": max(0, int(row.get("tokens") or 0)),
                "deferred": bool(row.get("deferred")),
                "server": str(row.get("server") or ""),
            }
            for row in recorded_tool_rows if isinstance(row, dict)
        ]
        bd["tools"] = [
            {key: row[key] for key in ("name", "tokens", "deferred")}
            for row in tool_rows
        ]
    else:
        tool_rows = []
        for tool, row in zip(tools, list(bd.get("tools") or [])):
            tool_rows.append({
                **row,
                "server": str(getattr(tool, "_mcp_server", None) or ""),
            })
    system_loaded = 0
    system_deferred = 0
    mcp_loaded = 0
    mcp_deferred = 0
    mcp_items = []
    for row in tool_rows:
        tokens = int(row.get("tokens") or 0)
        deferred = bool(row.get("deferred"))
        server = row.get("server") or ""
        if server:
            if deferred:
                mcp_deferred += tokens
            else:
                mcp_loaded += tokens
            mcp_items.append({
                "server": str(server),
                "name": row.get("name") or "",
                "tokens": tokens,
                "deferred": deferred,
            })
        elif deferred:
            system_deferred += tokens
        else:
            system_loaded += tokens
    mcp_items.sort(key=lambda x: -x["tokens"])
    bd["mcp_detail"] = mcp_items
    bd["tools_schema"] = system_loaded
    bd["tools_deferred_catalog"] = system_deferred
    bd["mcp_tools"] = mcp_loaded
    bd["mcp_tools_deferred"] = mcp_deferred

    # The recorded system prompt already contains Skills, always-on Memory,
    # and the deferred-tool catalog. Expose them as rows by subtracting them
    # from the parent row; do not add them to the total a second time.
    full_system = int(bd.get("system_prompt") or 0)
    nested_system = (
        int(bd.get("skills") or 0)
        + int(bd.get("memory") or 0)
        + system_deferred
        + mcp_deferred
    )
    bd["system_prompt"] = max(0, full_system - nested_system)
    bd["system_detail"] = _system_rows(
        sections, _t,
        nested={"capabilities": int(bd.get("skills") or 0)
                + system_deferred + mcp_deferred},
    )
    _describe_tools(bd.get("tools") or [], mcp_items, tools)
    bd["messages_detail"] = _message_detail(message_rows, branch)

    bd["unclassified"] = 0
    bd["input_used"] = sum(
        int(bd.get(key) or 0) for key in BREAKDOWN_CATEGORY_KEYS
    )
    bd["input_used_pct"] = (
        round(bd["input_used"] / ctx_window, 4) if ctx_window else None
    )
    # Free space
    bd["free_space"] = max(0, ctx_window - bd["input_used"]) if ctx_window else None

    return bd


def compute_draft_breakdown(
    agent_id: str = "main",
    provider: str = "",
    model: str = "",
    *,
    channel: str = "web",
    project_id: str = "",
) -> dict:
    """What a new chat would send before its first message: the agent's
    system prompt and toolset against the selected model's window.

    ``project_id`` is the project the chat will belong to; its memory is
    injected beside the personal one, as it will be once the chat exists.
    """
    from openprogram.state.memory.project_layer import for_project

    with for_project(project_id or None):
        return _draft_breakdown(agent_id, provider, model, channel)


def _draft_breakdown(agent_id: str, provider: str, model: str, channel: str) -> dict:
    from openprogram.runtime.context.components import build_system_prompt

    try:
        from openprogram.programs import agent_tools
        tools = agent_tools(toolset="full")
    except Exception:
        tools = []
    profile = load_agent_profile(agent_id or "main")
    agent_view = type("_AgentView", (), dict(profile or {}))()
    latest_system = build_system_prompt(agent_view, channel=channel, tools=tools)
    window = model_ref_window(model, provider) if provider and model else None
    bd = _classify(
        session_id=None, sess={"agent_id": agent_id}, model_id=model,
        latest_system=latest_system, recorded_sections=None,
        agent_view=agent_view, msgs=[], message_rows=[], branch=[],
        tools=tools, latest_tools=[], has_tool_snapshot=False,
        recorded_tool_rows=[], ctx_window=window or 0,
    )
    bd["basis"] = "estimated"
    bd["draft"] = True
    return bd


def _prompt_sections(
    text: str,
    recorded: Optional[list],
    agent_view,
    agent_id: str,
) -> list[tuple[str, str]]:
    """``(component, block)`` pairs for a system prompt."""
    from openprogram.runtime.context.components import (
        prompt_sections, split_sections,
    )

    if not text:
        return []
    known = recorded or prompt_sections(text)
    if known:
        return split_sections(text, known)
    # No lengths on record: build the prompt now and find each block in
    # the recorded text. A block that has changed since is left in
    # ``other``.
    try:
        from openprogram.runtime.context.components import assemble_sections

        if agent_view is None:
            profile = load_agent_profile(agent_id)
            agent_view = type("_AgentView", (), dict(profile or {}))()
        fresh = assemble_sections(agent_view, ["L0", "L1"])
    except Exception:
        _log.debug("system prompt sections unavailable", exc_info=True)
        fresh = []
    out: list[tuple[str, str]] = []
    rest = text
    for name, block in fresh:
        if block and block in rest:
            out.append((name, block))
            rest = rest.replace(block, "", 1)
    # Memory is fenced, so it is found even when its text has changed.
    for fenced in re.findall(r"<memory-context>[\s\S]*?</memory-context>", rest):
        out.append(("memory", fenced))
        rest = rest.replace(fenced, "", 1)
    rest = rest.strip()
    if rest:
        out.append(("other", rest))
    return out


def _system_rows(sections, count, *, nested: dict) -> list[dict]:
    """Rows of the System prompt category. Memory has a category of its
    own, and the skills index and deferred-tool catalog inside
    ``capabilities`` have theirs, so those tokens leave these rows."""
    totals: dict[str, int] = {}
    texts: dict[str, list[str]] = {}
    for name, block in sections:
        if name == "memory":
            continue
        totals[name] = totals.get(name, 0) + count(block)
        texts.setdefault(name, []).append(block)
    rows = []
    for name, tokens in totals.items():
        tokens = max(0, tokens - int(nested.get(name) or 0))
        if tokens > 0:
            rows.append({"name": name, "tokens": tokens,
                         "text": _preview("\n\n".join(texts[name]))})
    return rows


#: How much of a block the panel shows when a row is opened.
PREVIEW_CHARS = 4_000


def _preview(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS].rstrip() + "\n…"


def _describe_tools(rows: list, mcp_rows: list, tools: list) -> None:
    """Give tool rows the first line of their description."""
    by_name = {getattr(t, "name", ""): t for t in tools or []}
    missing = [r.get("name") for r in [*rows, *mcp_rows]
               if r.get("name") and r.get("name") not in by_name]
    if missing:
        try:
            from openprogram.programs import agent_tools
            by_name.update({getattr(t, "name", ""): t
                            for t in agent_tools(names=list(dict.fromkeys(missing)))})
        except Exception:
            _log.debug("tool descriptions unavailable", exc_info=True)
    for row in [*rows, *mcp_rows]:
        tool = by_name.get(row.get("name"))
        text = (getattr(tool, "description", "") or "").strip() if tool else ""
        if text:
            row["description"] = text.splitlines()[0][:200]


#: Largest messages the Messages detail lists.
TOP_MESSAGES = 8


def _message_detail(rows: list[dict], branch: list[dict]) -> dict:
    """Messages by kind, and the largest ones with a line of their text."""
    visible = {m.get("id"): (m.get("content") or "") for m in branch or []}
    groups: dict[str, dict] = {}
    items = []
    for row in rows or []:
        tokens = max(0, int(row.get("_context_tokens") or 0))
        if not tokens:
            continue
        kind = _message_kind(row)
        group = groups.setdefault(kind, {"kind": kind, "tokens": 0, "count": 0})
        group["tokens"] += tokens
        group["count"] += 1
        text = " ".join(str(visible.get(row.get("id")) or "").split())
        items.append({"id": row.get("id") or "", "kind": kind,
                      "tokens": tokens, "preview": text[:120]})
    items.sort(key=lambda item: -item["tokens"])
    return {
        "groups": sorted(groups.values(), key=lambda g: -g["tokens"]),
        "top": items[:TOP_MESSAGES],
    }


def _message_kind(row: dict) -> str:
    from openprogram.runtime.context.persistence import SUMMARY_NODE_NAME

    if (row.get("name") or "") == SUMMARY_NODE_NAME:
        return "summary"
    role = row.get("role") or ""
    return role if role in ("user", "assistant") else "other"


def _unfence(text: str) -> str:
    """The recalled text inside a ``<memory-context>`` fence."""
    text = re.sub(r"</?memory-context>", "", text)
    return re.sub(r"\[System note:[^\]]*\]", "", text).strip()


def _memory_files(session_id: Optional[str], block: str, count) -> list[dict]:
    """The memory block as files: one ``MEMORY.md`` per workspace (its map,
    then its core), counted from the recorded text."""
    if not block:
        return []
    fences = re.findall(r"<memory-context>[\s\S]*?</memory-context>", block) or [block]
    try:
        from openprogram.state.memory import store
        from openprogram.state.memory.project_layer import read_layers

        # Injected general to specific, the reverse of the read order.
        layers = list(reversed(read_layers(session_id, personal=store.root()).layers))
    except Exception:
        _log.debug("memory layers unavailable", exc_info=True)
        layers = []
    named = len(layers) == len(fences) and len(layers) > 1
    return [
        {"path": (f"{layers[i].name}/" if named else "") + "MEMORY.md",
         "tokens": count(fence), "text": _preview(_unfence(fence))}
        for i, fence in enumerate(fences)
    ]


def estimate_total_used(session_id: str, head_id: Optional[str] = None) -> tuple[int, int]:
    """``(total_used, context_window)`` for the branch as it stands now."""
    bd = compute_breakdown(session_id, head_id)
    return int(bd.get("input_used") or 0), int(bd.get("context_window") or 0)


def build_stats(
    session_id: str,
    *,
    head_id: Optional[str] = None,
    measured_total: Optional[int] = None,
    window: Optional[int] = None,
    estimated_total: Optional[int] = None,
    request_input_estimate: Optional[int] = None,
    calibration: Optional[float] = None,
) -> dict:
    """The one context-occupancy record both frontends read.

    ``measured_total`` is the provider-billed prompt size of a request
    that just completed. Passing it selects the ``measured`` basis; the
    estimate is still computed alongside so ``calibration`` can report the
    ratio between them. Omitting it selects ``estimated``.

    ``window`` is the window of the model the caller resolved now (the
    turn's Model, or the session's current model); without one the
    session row's model decides. It is never read back from an earlier
    record, and ``None`` means unknown: the reader shows no limit.
    """
    breakdown = None
    if estimated_total is None:
        breakdown = compute_breakdown(session_id, head_id, context_window=window)
        estimated = int(breakdown.get("input_used") or 0)
        est_window = int(breakdown.get("context_window") or 0)
    else:
        estimated = int(estimated_total)
        est_window = int(window or 0)
    window = int(window or 0) or est_window or None

    if measured_total and measured_total > 0:
        total_used = int(measured_total)
        basis = "measured"
    else:
        total_used = round(estimated * calibration) if calibration and calibration > 0 else estimated
        basis = "estimated"

    stats = {
        "window": window,
        "total_used": total_used,
        "basis": basis,
        "estimated": estimated,
    }
    if basis == "measured":
        paired_estimate = int(request_input_estimate or 0)
        if paired_estimate > 0:
            stats["calibration"] = round(total_used / paired_estimate, 4)
            stats["calibration_source"] = "request"
        elif estimated > 0:
            # Older provider records lack a paired request estimate. This
            # ratio is informative only; graph edits must not reuse it.
            stats["calibration"] = round(total_used / estimated, 4)
            stats["calibration_source"] = "graph"
    elif calibration and calibration > 0:
        stats["calibration"] = calibration
        stats["calibration_source"] = "request"
    if breakdown is not None:
        stats["_breakdown"] = breakdown
    return stats


def _scale_categories(values: dict[str, int], target: int) -> dict[str, int]:
    """Scale positive category estimates to an exact integer ``target``."""
    source = sum(values.values())
    if source <= 0 or source == target:
        return dict(values)
    raw = {key: value * target / source for key, value in values.items()}
    scaled = {key: int(value) for key, value in raw.items()}
    remainder = target - sum(scaled.values())
    if remainder > 0:
        order = sorted(
            values,
            key=lambda key: (raw[key] - scaled[key], values[key]),
            reverse=True,
        )
        for key in order[:remainder]:
            scaled[key] += 1
    return scaled


#: Detail lists whose rows divide one category between them.
_DETAIL_OF = {
    "system_detail": "system_prompt",
    "memory_detail": "memory",
    "skills_detail": "skills",
}


def finalize_breakdown(breakdown: dict, occupancy: dict, *,
                       running_turn: bool = False,
                       request_estimate: Optional[int] = None) -> dict:
    """Merge one local breakdown with the matching occupancy snapshot.

    Provider usage owns the headline total. Locally reconstructable rows keep
    their estimates; a positive provider residual is explicit. If the local
    estimate is higher, rows are proportionally calibrated so displayed rows
    still add up to the provider total without inventing a negative category.

    ``running_turn`` marks a total measured by a request inside a turn that
    is still running: that turn's own rounds, which the graph does not
    hold yet, count under Conversation as ``turn``. Their size is the
    request's own estimate (``request_estimate``) less the graph's; the
    rest of the residual (provider-side prompt, estimator error) stays
    unclassified, as it does once the turn ends.
    """
    result = dict(breakdown)
    result.update(occupancy)
    values = {
        key: max(0, int(breakdown.get(key) or 0))
        for key in BREAKDOWN_CATEGORY_KEYS
        if key != "unclassified"
    }
    classified_estimate = sum(values.values())
    total = max(0, int(result.get("total_used") or classified_estimate))
    scale = 1.0
    if classified_estimate > total:
        scale = total / classified_estimate if classified_estimate else 1.0
        values = _scale_categories(values, total)

    result.update(values)
    # A category's detail rows add up to the category as shown.
    for detail, key in _DETAIL_OF.items():
        rows = breakdown.get(detail)
        if not isinstance(rows, list) or not rows:
            continue
        scaled = _scale_categories(
            {i: max(0, int(row.get("tokens") or 0)) for i, row in enumerate(rows)},
            values.get(key, 0),
        )
        result[detail] = [
            {**row, "tokens": scaled[i]} for i, row in enumerate(rows)
        ]
    # Tool rows split four categories: built-in loaded / on demand, MCP
    # loaded / on demand.
    mcp_rows = [r for r in breakdown.get("mcp_detail") or [] if isinstance(r, dict)]
    mcp_names = {r.get("name") for r in mcp_rows}
    tool_rows = [r for r in breakdown.get("tools") or []
                 if isinstance(r, dict) and r.get("name") not in mcp_names]
    for rows_key, rows, loaded_key, deferred_key in (
        ("tools", tool_rows, "tools_schema", "tools_deferred_catalog"),
        ("mcp_detail", mcp_rows, "mcp_tools", "mcp_tools_deferred"),
    ):
        if not rows:
            continue
        scaled_rows = {}
        for deferred, key in ((False, loaded_key), (True, deferred_key)):
            part = {i: max(0, int(r.get("tokens") or 0))
                    for i, r in enumerate(rows) if bool(r.get("deferred")) == deferred}
            scaled_rows.update(_scale_categories(part, values.get(key, 0)) if part else {})
        result[rows_key] = [{**r, "tokens": scaled_rows.get(i, r.get("tokens"))}
                            for i, r in enumerate(rows)]
    detail = breakdown.get("messages_detail")
    if isinstance(detail, dict) and detail.get("groups"):
        source = sum(int(g.get("tokens") or 0) for g in detail["groups"])
        ratio = values.get("messages", 0) / source if source else 0
        result["messages_detail"] = {
            "groups": [{**g, "tokens": round(int(g.get("tokens") or 0) * ratio)}
                       for g in detail["groups"]],
            "top": [{**m, "tokens": round(int(m.get("tokens") or 0) * ratio)}
                    for m in detail.get("top") or []],
        }
    # Mid-turn, the request just measured carries the turn's own rounds
    # (replies, tool calls and results), which reach the graph only when
    # the turn ends. That growth is conversation, not unclassified.
    growth = (min(max(0, total - sum(values.values())),
                  max(0, int(request_estimate or 0) - classified_estimate))
              if running_turn else 0)
    if growth:
        values["messages"] = values.get("messages", 0) + growth
        result["messages"] = values["messages"]
        shown = result.get("messages_detail")
        shown = shown if isinstance(shown, dict) else {}
        result["messages_detail"] = {
            **shown,
            "groups": [{"kind": "turn", "tokens": growth, "count": 0},
                       *(shown.get("groups") or [])],
            "top": list(shown.get("top") or []),
        }
    result["classified_estimate"] = classified_estimate
    result["classified_used"] = sum(values.values())
    result["classification_scale"] = round(scale, 6)
    result["unclassified"] = max(0, total - result["classified_used"])
    result["input_used"] = classified_estimate
    # The occupancy's window is the one resolved now; a snapshot's own
    # ``context_window`` is only what it was computed against.
    window = int((occupancy.get("window") if "window" in occupancy
                  else breakdown.get("context_window")) or 0)
    result["window"] = result["context_window"] = window or None
    result["free_space"] = max(0, window - total) if window else None
    result["input_used_pct"] = round(total / window, 4) if window else None
    return result

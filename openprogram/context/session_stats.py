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
from typing import Optional

_log = logging.getLogger(__name__)


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
    from openprogram.agent.session_db import default_db
    from openprogram.context.breakdown import compute_call_breakdown
    from openprogram.context.tokens import real_context_window

    db = default_db()
    from openprogram.context.persistence import rendered_history
    branch = rendered_history(db, session_id, head_id) or []
    sess = db.get_session(session_id) or {}
    from openprogram.context.system_prompt_node import latest_recorded_prompt
    from openprogram.context.tool_snapshot_node import (
        latest_recorded_tool_snapshot,
    )
    latest_system = latest_recorded_prompt(db, session_id, head_id) or ""
    tool_snapshot = latest_recorded_tool_snapshot(db, session_id, head_id)
    has_tool_snapshot = tool_snapshot is not None
    recorded_tool_rows = list((tool_snapshot or {}).get("tools") or [])

    # 分支消息 + 从 extra(JSON) 里挖最近一次调用记下的原料
    # （tools_available / system_prompt）。
    msgs = []
    latest_tools = []
    for m in branch:
        content = m.get("content") or ""
        prefetch = m.get("memory_prefetch") or ""
        if prefetch:
            content = f"{prefetch.rstrip()}\n\n{content}"
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

    # sess["model"] 存的是 "provider:model"（如 openai-codex:gpt-5.5），
    # get_model 需要拆开的两个参数。以前整串传单参 TypeError 被吞，
    # 窗口永远回落 128k 默认值。
    model_id = sess.get("model") or ""
    provider_id = sess.get("provider_name") or ""
    if ":" in model_id:
        provider_id, model_id = model_id.split(":", 1)
    try:
        from openprogram.providers.models import get_model as _get_model
        model_obj = (_get_model(provider_id, model_id)
                     if provider_id and model_id else None)
    except Exception:
        model_obj = None
    ctx_window = int(context_window or 0) or real_context_window(model_obj)

    # 工具集：优先用节点存的原料（精确）；没有（如 codex 等自带
    # runtime 的 provider 没走采集路径）就回退到会话当前 toolset，
    # 这样 /context 对所有 provider 都能显示 tools/per-tool。
    try:
        from openprogram.programs import agent_tools as _agent_tools
        if has_tool_snapshot:
            tools = []
        elif latest_tools:
            tools = _agent_tools(names=list(latest_tools))
        elif sess.get("tools_enabled", True):
            tools = _agent_tools(toolset="full")
        else:
            tools = []
    except Exception:
        tools = []

    # system_prompt：优先节点原料；没有就按会话 agent 重建（identity +
    # tool_use 指引 + skills…），让所有 provider 的 /context 都能显示
    # System 类真实 token，而非缺省 0。
    if not latest_system:
        try:
            from openprogram.agent.internals._model_tools import (
                load_agent_profile,
            )
            from openprogram.context.components import (
                build_system_prompt,
            )

            class _AgentView:
                def __init__(self, d):
                    self.__dict__.update(d)

            prof = load_agent_profile(sess.get("agent_id") or "main")
            if isinstance(prof, dict):
                latest_system = build_system_prompt(_AgentView(prof))
        except Exception:
            latest_system = ""

    bd = compute_call_breakdown(
        system_prompt=latest_system,
        history=msgs,
        tools=tools,
        context_window=ctx_window,
    )
    bd["session_id"] = session_id
    bd["model"] = model_id
    bd["context_window"] = ctx_window
    bd["tools_source"] = (
        "recorded_snapshot" if has_tool_snapshot else
        ("recorded" if latest_tools else "session_default")
    )

    # 完整 /context（对齐 Claude Code）：补 skills / memory / mcp 明细，
    # 每项列名字 + token。best-effort，任一块失败不影响主分类。
    from openprogram.context.tokens import _text_tokens

    def _t(s: str) -> int:
        # Skills / memory / deferred catalogs are substrings of the one
        # system message. Count their text only; adding message overhead to
        # every subcomponent would count the same envelope several times.
        return _text_tokens(s or "")

    # Skills：按 source 分组，列每个 skill。口径对齐 Claude Code——
    # 系统提示里每个 skill 只占「name: 一行描述」的索引条目（skill
    # 正文按需加载、不常驻），所以算 name + description 首行，而非 body 全文。
    try:
        from openprogram.skills import loader as _sl
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

    # Memory files：只算真正**常驻进 system prompt** 的那块。
    # OpenProgram 的 memory 里只有 core.md 是 always-on block
    # （memory/core.py），wiki/journal 是按需检索、不常驻，不该计入
    # 当前 context（算全库会虚高到几十万 token）。对齐 Claude Code
    # 只列实际加载进 prompt 的 memory 文件。
    try:
        from openprogram.memory import get_backend as _mprovider
        block = ""
        try:
            block = _mprovider().system_prompt() or ""
        except Exception:
            block = ""
        mem_items = []
        if block and block in latest_system:
            mem_items.append({"path": "core.md", "tokens": _t(block)})
        bd["memory_detail"] = mem_items
        bd["memory"] = sum(x["tokens"] for x in mem_items)
    except Exception:
        bd["memory_detail"] = []

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

    bd["unclassified"] = 0
    bd["input_used"] = sum(
        int(bd.get(key) or 0) for key in BREAKDOWN_CATEGORY_KEYS
    )
    bd["input_used_pct"] = (
        round(bd["input_used"] / ctx_window, 4) if ctx_window else 0
    )
    # Free space
    bd["free_space"] = max(0, ctx_window - bd["input_used"])

    return bd


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
) -> dict:
    """The one context-occupancy record both frontends read.

    ``measured_total`` is the provider-billed prompt size of a request
    that just completed. Passing it selects the ``measured`` basis; the
    estimate is still computed alongside so ``calibration`` can report the
    ratio between them. Omitting it selects ``estimated``.
    """
    breakdown = None
    if estimated_total is None:
        breakdown = compute_breakdown(session_id, head_id, context_window=window)
        estimated = int(breakdown.get("input_used") or 0)
        est_window = int(breakdown.get("context_window") or 0)
    else:
        estimated = int(estimated_total)
        est_window = int(window or 0)
    window = int(window or 0) or est_window

    if measured_total and measured_total > 0:
        total_used = int(measured_total)
        basis = "measured"
    else:
        total_used = estimated
        basis = "estimated"

    stats = {
        "window": window,
        "total_used": total_used,
        "basis": basis,
        "estimated": estimated,
    }
    if basis == "measured" and estimated > 0:
        stats["calibration"] = round(total_used / estimated, 4)
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


def finalize_breakdown(breakdown: dict, occupancy: dict) -> dict:
    """Merge one local breakdown with the matching occupancy snapshot.

    Provider usage owns the headline total. Locally reconstructable rows keep
    their estimates; a positive provider residual is explicit. If the local
    estimate is higher, rows are proportionally calibrated so displayed rows
    still add up to the provider total without inventing a negative category.
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
    measured = result.get("basis") == "measured"

    scale = 1.0
    if measured and classified_estimate > total:
        scale = total / classified_estimate if classified_estimate else 1.0
        values = _scale_categories(values, total)

    result.update(values)
    result["classified_estimate"] = classified_estimate
    result["classified_used"] = sum(values.values())
    result["classification_scale"] = round(scale, 6)
    result["unclassified"] = (
        max(0, total - result["classified_used"]) if measured else 0
    )
    result["input_used"] = classified_estimate
    window = int(result.get("window") or result.get("context_window") or 0)
    result["free_space"] = max(0, window - total)
    return result

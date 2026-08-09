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
    recomputed from the current branch with the local tokenizer, so the
    ring moves the moment the graph moves instead of lagging a request
    behind.

A measured reading also calibrates the estimator: ``calibration`` records
measured/estimated for the same graph, which tells a reader how far the
local estimate drifts from what the provider charges.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)


def compute_breakdown(session_id: str, head_id: Optional[str] = None) -> dict:
    """Per-category input-token breakdown for a session branch.

    Storage rule: nothing is stored, everything is recomputed from the
    branch messages plus the tool / system-prompt raw material recorded on
    the most recent LLM call.

    ``head_id`` is the DAG branch tip the user is looking at; a session can
    hold several branches, so the caller passes the current head and the
    breakdown describes that branch. ``None`` falls back to the session's
    global head.
    """
    from openprogram.agent.session_db import default_db
    from openprogram.context.breakdown import compute_call_breakdown
    from openprogram.context.tokens import real_context_window

    db = default_db()
    branch = db.get_branch(session_id, head_id) or []
    sess = db.get_session(session_id) or {}

    # 分支消息 + 从 extra(JSON) 里挖最近一次调用记下的原料
    # （tools_available / system_prompt）。
    msgs = []
    latest_tools = []
    latest_system = ""
    for m in branch:
        content = m.get("content") or ""
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
                    if ex.get("tools_available"):
                        latest_tools = ex["tools_available"]
                    if ex.get("system_prompt"):
                        latest_system = ex["system_prompt"]
                    parts = [content]
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
                    for tc in ex.get("tool_calls") or []:
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
    ctx_window = real_context_window(model_obj)

    # 工具集：优先用节点存的原料（精确）；没有（如 codex 等自带
    # runtime 的 provider 没走采集路径）就回退到会话当前 toolset，
    # 这样 /context 对所有 provider 都能显示 tools/per-tool。
    try:
        from openprogram.functions import agent_tools as _agent_tools
        if latest_tools:
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
    bd["tools_source"] = "recorded" if latest_tools else "session_default"

    # 完整 /context（对齐 Claude Code）：补 skills / memory / mcp 明细，
    # 每项列名字 + token。best-effort，任一块失败不影响主分类。
    from openprogram.context.tokens import estimate_message_tokens as _tok

    def _t(s: str) -> int:
        return _tok({"role": "system", "content": s or ""})

    # Skills：按 source 分组，列每个 skill。口径对齐 Claude Code——
    # 系统提示里每个 skill 只占「name: 一行描述」的索引条目（skill
    # 正文按需加载、不常驻），所以算 name + description 首行，而非 body 全文。
    try:
        from openprogram.skills import loader as _sl
        sk_items = []
        for s in _sl.list_skills():
            name = getattr(s, "name", "") or ""
            desc = (getattr(s, "description", "") or "").splitlines()
            line = f"{name}: {desc[0] if desc else ''}"
            sk_items.append({
                "name": name,
                "source": getattr(s, "source", "") or "",
                "tokens": _t(line),
            })
        sk_items.sort(key=lambda x: -x["tokens"])
        bd["skills_detail"] = sk_items
        bd["skills"] = sum(x["tokens"] for x in sk_items)
    except Exception:
        bd["skills_detail"] = []

    # Memory files：只算真正**常驻进 system prompt** 的那块。
    # OpenProgram 的 memory 里只有 core.md 是 always-on block
    # （memory/core.py），wiki/journal 是按需检索、不常驻，不该计入
    # 当前 context（算全库会虚高到几十万 token）。对齐 Claude Code
    # 只列实际加载进 prompt 的 memory 文件。
    try:
        import os as _os
        from openprogram.paths import get_state_dir as _gsd
        from openprogram.memory import core as _mcore
        block = ""
        try:
            block = _mcore.system_prompt_block() or ""
        except Exception:
            block = ""
        mem_items = []
        if block:
            mem_items.append({"path": "core.md", "tokens": _t(block)})
        bd["memory_detail"] = mem_items
        bd["memory"] = sum(x["tokens"] for x in mem_items)
    except Exception:
        bd["memory_detail"] = []

    # MCP tools：像 System tools 一样统计每个 MCP 工具的 schema
    # token，并按 _defer 分 loaded / deferred（MCP 也走同一套 defer
    # 机制，见 mcp/adapter.py：非 always_load 的 MCP 工具默认 defer）。
    # 从 all_tools() 筛带 _mcp_server 属性的注册工具（MCP 工具只在本
    # webui 进程连着 server 时才注册）。loaded 计完整 schema token，
    # deferred 只计 catalog 一行。
    try:
        import json as _json
        from openprogram.functions._runtime import all_tools as _all
        mcp_items = []
        mcp_loaded_total = 0
        mcp_deferred_total = 0
        for t in (_all() or []):
            server = getattr(t, "_mcp_server", None)
            if not server:
                continue  # 只要 MCP 工具
            is_def = bool(getattr(t, "_defer", False))
            name = getattr(t, "name", "") or ""
            desc = getattr(t, "description", "") or ""
            if is_def:
                # deferred：只占 catalog 一行 `name: desc`
                tk = _t(f"{name}: {desc.splitlines()[0] if desc else ''}")
                mcp_deferred_total += tk
            else:
                # loaded：完整 schema
                schema = getattr(t, "schema", None) or getattr(t, "spec", None) or {}
                try:
                    body = _json.dumps(schema, default=str, ensure_ascii=False)
                except Exception:
                    body = name + desc
                tk = _t(body) + 5
                mcp_loaded_total += tk
            mcp_items.append({
                "server": server,
                "name": name,
                "tokens": tk,
                "deferred": is_def,
            })
        mcp_items.sort(key=lambda x: -x["tokens"])
        bd["mcp_detail"] = mcp_items
        bd["mcp_tools"] = mcp_loaded_total            # loaded 那档
        bd["mcp_tools_deferred"] = mcp_deferred_total  # deferred 那档
    except Exception:
        bd["mcp_detail"] = []

    # input_used 重算：compute_call_breakdown 只把 system_prompt +
    # history + loaded tool schema 算进 input_used，没算这里事后补的
    # deferred catalog / skills / memory / mcp。所以要把所有真实分类
    # 重新加总，否则上半部「已用量」会比下半部分类总和偏小。
    bd["input_used"] = (
        bd.get("system_prompt", 0)
        + bd.get("messages", 0)
        + bd.get("tools_schema", 0)
        + bd.get("tools_deferred_catalog", 0)
        + bd.get("mcp_tools", 0)
        + bd.get("mcp_tools_deferred", 0)
        + bd.get("memory", 0)
        + bd.get("skills", 0)
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
) -> dict:
    """The one context-occupancy record both frontends read.

    ``measured_total`` is the provider-billed prompt size of a request
    that just completed. Passing it selects the ``measured`` basis; the
    estimate is still computed alongside so ``calibration`` can report the
    ratio between them. Omitting it selects ``estimated``.
    """
    estimated, est_window = estimate_total_used(session_id, head_id)
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
    return stats

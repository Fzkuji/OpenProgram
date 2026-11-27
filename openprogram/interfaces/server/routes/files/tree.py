"""Read-only catalog endpoints: DAG tree, token stats, programs meta.

These routes are mostly thin DB wrappers (SessionDB + ENABLED_MODELS registry)
plus a ``_discover_functions`` server-helper call.
"""
from __future__ import annotations

import logging
import asyncio

from fastapi.responses import JSONResponse
from openprogram.state.store.session.conversation_work import run_for_conversation

_log = logging.getLogger(__name__)


def _coverage_nodes(db, session_id: str, node_ids: list) -> list:
    """Per-node context coverage for ``node_ids``, in the same order.

    ``[{"node_id", "in_context", "aged", "spilled"}]``. ``aged`` is
    always false: the render never shortens an old result (only
    compaction replaces it), and the key stays for the client. ``spilled`` reads the
    ``metadata.spilled`` blob ``spill_if_large`` wrote at record time.
    Falls back to flags-off (still ``in_context``) when the graph can't
    be loaded: coverage is a highlight, never a reason to 500.
    """
    if not node_ids:
        return []
    aged: set = set()
    spilled: set = set()
    try:
        from openprogram.state.store.session.session_node_writer import SessionNodeWriter

        graph = SessionNodeWriter(db, session_id).load()
        spilled = {
            nid for nid in node_ids
            if nid in graph.nodes
            and isinstance((graph.nodes[nid].metadata or {}).get("spilled"), dict)
        }
    except Exception:
        # Aged/spilled markers are display hints; the tree renders without
        # them.
        _log.debug(
            "aged/spilled node ids unavailable for session %s", session_id, exc_info=True)
    return [
        {
            "node_id": nid,
            "in_context": True,
            "aged": nid in aged,
            "spilled": nid in spilled,
        }
        for nid in node_ids
    ]


def _with_code_nodes(db, session_id: str, head_id, node_ids: list) -> list:
    """``node_ids`` plus the code nodes the next request carries under them.

    ``rendered_history`` walks the predecessor chain, so it lists the
    conversation turns only — the tool calls a reply made hang off it by
    ``caller`` and never appear. The real render reads them too
    (``render_context``, the walk ``engine.py`` feeds the model), so the
    graph drew every opened call as out of context, and the ``aged``
    flag — a code-node fact — could never be set. Calls are admitted
    when ``render_context`` reads them AND their conversation owner is
    in ``node_ids``, which keeps the compaction view's cut: a call under
    a covered turn stays out with its turn. Order follows ``node_ids``,
    each call right after its owner.
    """
    if not node_ids:
        return node_ids
    try:
        from openprogram.runtime.context.session_dag.nodes import render_context
        from openprogram.state.store.session.session_node_writer import SessionNodeWriter

        graph = SessionNodeWriter(db, session_id).load()
        head = head_id if head_id in graph.nodes else node_ids[-1]
        if head not in graph.nodes:
            return node_ids
        reads = render_context(graph, head_id=head, frame_entry_seq=-1)
    except Exception:
        return node_ids
    spine = set(node_ids)

    def _owner(nid: str):
        seen: set = set()
        cur = graph.nodes.get(nid)
        while cur is not None and cur.caller and cur.caller != "ROOT" \
                and cur.id not in seen:
            seen.add(cur.id)
            if cur.caller in spine:
                return cur.caller
            cur = graph.nodes.get(cur.caller)
        return None

    calls: dict = {}
    for nid in reads:
        node = graph.nodes.get(nid)
        if nid in spine or node is None or not node.is_code():
            continue
        owner = _owner(nid)
        if owner is not None:
            calls.setdefault(owner, []).append(nid)
    out: list = []
    for nid in node_ids:
        out.append(nid)
        out.extend(calls.get(nid, ()))
    return out


def register(app):
    @app.get("/api/programs")
    async def get_functions():
        from openprogram.interfaces.server import server as _s
        # Discovery reads and parses Program sources; off the event loop so
        # every other request keeps answering meanwhile.
        return JSONResponse(content=await asyncio.to_thread(_s._discover_functions))

    @app.get("/api/tools")
    async def get_tools():
        """The regular (non-agentic) built-in tools — bash, file edits,
        web search, … Each carries a ``disabled`` flag so the Functions
        page can render a per-tool on/off switch: a disabled tool is
        filtered out of every LLM toolset (agent_tools() honours
        ``tools.disabled``), so the model never sees it. Toggle via
        ``POST /api/settings {key:"tools.disabled.<name>", value:<on>}``."""
        from openprogram.programs import agent_tools
        from openprogram.state.memory.tool_names import MEMORY_TOOL_NAMES
        from openprogram.programs.tools.disabled import read_disabled_tools
        disabled = read_disabled_tools()
        _TOOL_GROUPS = {
            "bash": "file", "read": "file", "write": "file", "edit": "file",
            "glob": "file", "grep": "file", "list": "file",
            "apply_patch": "file", "process": "file",
            # Read from the bundle so a renamed memory tool lands in its
            # group rather than falling out of the grouping entirely.
            **{name: "memory" for name in MEMORY_TOOL_NAMES},
            "web_search": "web", "web_fetch": "web",
            "pdf": "web", "image_analyze": "web", "image_generate": "web",
            "enter_plan_mode": "planning", "exit_plan_mode": "planning",
            "task_start": "planning",
            "task_step": "planning", "task_list": "planning",
            "agent": "agents", "archive_agent": "agents",
            "list_agents": "agents", "read_conversation": "agents",
            "send_message": "agents", "mixture_of_agents": "agents",
            "cron": "jobs", "scheduler": "jobs", "list_jobs": "jobs",
            "job_output": "jobs",
            "execute_code": "code", "lsp_definition": "code",
            "lsp_diagnostics": "code", "lsp_references": "code",
            "semble_find_related": "code", "semble_search": "code",
            "list_mcp_prompts": "mcp", "get_mcp_prompt": "mcp",
            "list_mcp_resources": "mcp", "read_mcp_resource": "mcp",
            "tool_search": "mcp",
            "worktree_create": "worktree", "worktree_merge": "worktree",
            "worktree_discard": "worktree", "worktree_list": "worktree",
            "worktree_keep": "worktree",
            "ask_user_question": "interaction", "canvas": "interaction",
            "send_file": "interaction",
            "program": "runtime", "skill": "runtime",
        }
        out = []
        for t in agent_tools(toolset="full", include_disabled=True):
            if getattr(t, "_is_agent_method", False):
                continue
            desc = (t.description or "").strip().split("\n")[0]
            mcp_server = getattr(t, "_mcp_server", None)
            out.append({
                "name": t.name,
                "description": desc,
                "disabled": t.name in disabled,
                "group": "connected" if mcp_server else _TOOL_GROUPS.get(t.name, "other"),
                "source": "mcp" if mcp_server else "builtin",
                "server": mcp_server,
            })
        out.sort(key=lambda r: r["name"])
        return JSONResponse(content=out)

    # Tool profiles
    # A profile = a named tool set the user configures on the Functions
    # page and selects in the chat composer. "full" = all exposed
    # tools (immutable). New profile = copy of default; user removes
    # tools they don't need for that scenario.

    def _all_tool_names() -> list[str]:
        """Every exposed tool name — leaf tools AND agentic programs.
        Profiles cover everything the model can use."""
        from openprogram.programs._host.runtime import exposed_names
        return sorted(exposed_names())

    def _builtin_tool_names() -> list[str]:
        """Only non-agentic (built-in) tools."""
        from openprogram.programs import agent_tools
        return sorted(
            t.name for t in agent_tools(toolset="full", include_disabled=True)
            if not getattr(t, "_is_agent_method", False)
        )

    # These two profiles always exist, cannot be modified or deleted.
    IMMUTABLE_PROFILES = ("FULL", "BUILT-IN")

    def _ensure_defaults(data: dict) -> dict:
        """Ensure the two immutable profiles exist with correct content."""
        profiles = data.setdefault("profiles", {})
        profiles["FULL"] = _all_tool_names()
        profiles["BUILT-IN"] = _builtin_tool_names()
        # Clean up legacy profiles that are now redundant
        for legacy in ("full", "default"):
            if legacy in profiles:
                del profiles[legacy]
        data.setdefault("active", "FULL")
        if data["active"] in ("full", "default"):
            data["active"] = "FULL"
        return data

    def _load_profiles() -> dict:
        from openprogram.programs._host.meta_storage import load_functions_meta

        data = load_functions_meta(
            {"profiles": {"full": _all_tool_names()}, "active": "full"},
        )
        # migrate old {folders:} shape if present
        if "folders" in data and "profiles" not in data:
            data["profiles"] = data.pop("folders")
        return data

    def _save_profiles(data: dict):
        from openprogram.programs._host.meta_storage import save_functions_meta

        _ensure_defaults(data)
        save_functions_meta(data)

    @app.get("/api/tool-profiles")
    async def get_tool_profiles():
        """All profiles + which is active."""
        data = _load_profiles()
        _ensure_defaults(data)
        return JSONResponse(content=data)

    @app.post("/api/tool-profiles")
    async def save_tool_profiles(body: dict = None):
        _save_profiles(body or {})
        return JSONResponse(content={"ok": True})

    @app.post("/api/tool-profiles/create")
    async def create_tool_profile(body: dict = None):
        """Create a new profile = copy of all tools.
        body: {"name": "profile name"}"""
        name = (body or {}).get("name", "new")
        data = _load_profiles()
        if name in IMMUTABLE_PROFILES:
            return JSONResponse(content={"ok": False, "error": "cannot overwrite immutable profile"}, status_code=400)
        data["profiles"][name] = list(_all_tool_names())
        _save_profiles(data)
        return JSONResponse(content={"ok": True, "profile": name,
                                     "tools": data["profiles"][name]})

    @app.post("/api/tool-profiles/delete")
    async def delete_tool_profile(body: dict = None):
        name = (body or {}).get("name", "")
        if name in IMMUTABLE_PROFILES:
            return JSONResponse(content={"ok": False, "error": "cannot delete immutable profile"}, status_code=400)
        data = _load_profiles()
        data["profiles"].pop(name, None)
        if data.get("active") == name:
            data["active"] = "FULL"
        _save_profiles(data)
        return JSONResponse(content={"ok": True})

    @app.post("/api/tool-profiles/add-tool")
    async def profile_add_tool(body: dict = None):
        """Add a tool to a profile. body: {"profile":"X","tool":"bash"}"""
        b = body or {}
        name, tool = b.get("profile", ""), b.get("tool", "")
        data = _load_profiles()
        tools = data["profiles"].get(name)
        if tools is None:
            return JSONResponse(content={"ok": False, "error": "profile not found"}, status_code=404)
        if tool not in tools:
            tools.append(tool)
            tools.sort()
        _save_profiles(data)
        return JSONResponse(content={"ok": True})

    @app.post("/api/tool-profiles/remove-tool")
    async def profile_remove_tool(body: dict = None):
        """Remove a tool from a profile. body: {"profile":"X","tool":"bash"}"""
        b = body or {}
        name, tool = b.get("profile", ""), b.get("tool", "")
        if name in IMMUTABLE_PROFILES:
            return JSONResponse(content={"ok": False, "error": "cannot modify immutable profile"}, status_code=400)
        data = _load_profiles()
        tools = data["profiles"].get(name)
        if tools is None:
            return JSONResponse(content={"ok": False, "error": "profile not found"}, status_code=404)
        if tool in tools:
            tools.remove(tool)
        _save_profiles(data)
        return JSONResponse(content={"ok": True})

    @app.post("/api/tool-profiles/activate")
    async def activate_tool_profile(body: dict = None):
        """Set the active profile. body: {"name":"FULL"}"""
        name = (body or {}).get("name", "FULL")
        data = _load_profiles()
        if name not in data["profiles"]:
            return JSONResponse(content={"ok": False, "error": "profile not found"}, status_code=404)
        data["active"] = name
        _save_profiles(data)
        return JSONResponse(content={"ok": True, "active": name})

    # Keep the old /api/functions/meta endpoints for compatibility.
    @app.get("/api/functions/meta")
    async def get_functions_meta():
        data = _load_profiles()
        return JSONResponse(content={
            "profiles": data.get("profiles", {}),
            "active": data.get("active", "full"),
        })

    @app.post("/api/functions/meta")
    async def save_functions_meta(body: dict = None):
        _save_profiles(body or {})
        return JSONResponse(content={"ok": True})

    # The session reads below take the conversation's lock, which a turn or a
    # cold load can hold for a while: they run in a worker thread so a held
    # conversation delays its own request, never the event loop.
    def _known_conversation(session_id: str) -> bool:
        """Whether ``session_id`` names a conversation, as ``load_session``
        decides it: one the server holds (a draft's first Send is held
        before the store writes it) or one the store has. A deleted id and
        one nobody created are not conversations."""
        from openprogram.interfaces.server import server as _s
        from openprogram.runtime.context.session_db import default_db

        with _s._sessions_lock:
            if session_id in _s._sessions:
                return True
        return default_db().get_session(session_id) is not None

    async def _conversation_read(session_id: str, fn, *args) -> JSONResponse:
        """``fn(*args)`` as the answer of a read of ``session_id``, or 404
        ``session_not_found`` when there is no such conversation: every
        read below would otherwise describe an empty conversation."""
        def read():
            if not _known_conversation(session_id):
                return None
            return {"body": fn(*args)}

        found = await run_for_conversation(session_id, read)
        if found is None:
            return JSONResponse(content={"error": "session_not_found"}, status_code=404)
        return JSONResponse(content=found["body"])

    @app.get("/api/sessions/{session_id}/branches/tokens")
    async def get_branches_tokens(session_id: str):
        """Lightweight token summary for every branch tip in this session."""
        return await _conversation_read(session_id, _branches_tokens, session_id)

    def _branches_tokens(session_id: str) -> dict:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

        db = default_db()
        branches = db.list_branches(session_id)
        out: list[dict] = []
        for b in branches:
            head_id = b.get("id") or b.get("head_msg_id")
            if not head_id:
                continue
            stats = db.get_branch_token_stats(session_id, head_id=head_id)
            window = stats.get("context_window") or 0
            mid = stats.get("model")
            if not window and mid:
                cands = [v for v in ENABLED_MODELS.values() if v.id == mid]
                if mid in ENABLED_MODELS:
                    cands.insert(0, ENABLED_MODELS[mid])
                if cands:
                    window = max(
                        int(getattr(c, "context_window", 0) or 0)
                        for c in cands
                    )
            pct = (stats["current_tokens"] / window) if window else 0.0
            out.append({
                "head_id": head_id,
                "current_tokens": stats["current_tokens"],
                "context_window": window,
                "pct_used": pct,
                "cache_hit_rate": stats.get("cache_hit_rate", 0.0),
                "cache_read_total": stats.get("cache_read_total", 0),
                "model": mid,
            })
        return {"branches": out}

    @app.get("/api/sessions/{session_id}/tokens")
    async def get_session_tokens(session_id: str, head_id: str | None = None,
                                 model: str | None = None,
                                 provider: str | None = None):
        return await _conversation_read(
            session_id, _session_tokens, session_id, head_id, model, provider)

    def _session_tokens(session_id: str, head_id: str | None, model: str | None,
                        provider: str | None) -> dict:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

        model_obj = None
        if model:
            key = f"{provider}/{model}" if provider else None
            model_obj = (ENABLED_MODELS.get(key) if key else None) or ENABLED_MODELS.get(model)
            if model_obj is None:
                for v in ENABLED_MODELS.values():
                    if v.id == model:
                        model_obj = v
                        break

        stats = default_db().get_branch_token_stats(
            session_id, head_id=head_id, model=model_obj,
        )

        if not stats["context_window"] and stats.get("model"):
            mid = stats["model"]
            candidates = [ENABLED_MODELS.get(mid)] if mid in ENABLED_MODELS else []
            candidates.extend(v for v in ENABLED_MODELS.values() if v.id == mid)
            candidates = [c for c in candidates if c is not None]
            if candidates:
                m = max(
                    candidates,
                    key=lambda c: int(getattr(c, "context_window", 0) or 0),
                )
                stats["context_window"] = int(getattr(m, "context_window", 0) or 0)
                if stats["context_window"]:
                    stats["pct_used"] = (
                        stats["current_tokens"] / stats["context_window"]
                    )

        return stats

    @app.get("/api/sessions/{session_id}/context-range")
    async def get_context_range(session_id: str, head_id: str | None = None):
        """Node ids the next chat message's LLM call will carry as context.

        That is the active branch — from root (or the most recent
        compaction summary) up to the head — which the dispatcher loads
        via ``get_branch`` and feeds to the context engine. The WebUI
        dims DAG nodes outside this set so the user can see, before
        sending, roughly how much history the next message will include.

        ``nodes`` carries the per-node coverage detail the DAG's
        coverage mode paints (dag/rendering.md §8): every id in
        ``node_ids`` with the two degradations the context pipeline
        applies to it.

            ``in_context``  always true here — the list IS the covered set
            ``aged``        always false: old results are no longer
                            shortened by the render
            ``spilled``     the node's result was written to
                            ``large_nodes/`` and the render only cites it

        Both flags come from the same functions the real render calls,
        so the graph never guesses at semantics the backend owns.
        """
        return await _conversation_read(session_id, _context_range, session_id, head_id)

    def _context_range(session_id: str, head_id: str | None) -> dict:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.context.compaction.persistence import rendered_history

        db = default_db()
        # The COMPACTED view, exactly what the dispatcher feeds the
        # engine: active summary standing in for its covered segment,
        # then the kept turns. The raw get_branch walk would paint the
        # covered turns white as if the next request still carried them.
        branch = rendered_history(db, session_id, head_id)
        node_ids = _with_code_nodes(
            db, session_id, head_id,
            [m["id"] for m in branch if m.get("id")],
        )
        return {
            "session_id": session_id,
            "node_ids": node_ids,
            "count": len(node_ids),
            "nodes": _coverage_nodes(db, session_id, node_ids),
        }

    @app.get("/api/context/draft")
    async def get_draft_context(
        provider: str = "", model: str = "", agent_id: str = "main",
        project_id: str = "",
    ):
        """The /context breakdown of a chat that has no session yet: the
        agent's system prompt and tools against the selected model, with
        the selected project's memory."""
        import asyncio
        from openprogram.runtime.context.token_accounting import session_stats as _cs

        try:
            bd = await asyncio.to_thread(
                _cs.compute_draft_breakdown, agent_id or "main", provider, model,
                project_id=project_id,
            )
        except Exception as e:
            return JSONResponse(
                status_code=200,
                content={"error": f"{type(e).__name__}: {e}", "tools": []},
            )
        window = int(bd.get("context_window") or 0) or None
        return JSONResponse(content=_cs.finalize_breakdown(bd, {
            "total_used": int(bd.get("input_used") or 0),
            "window": window,
            "basis": "estimated",
        }))

    @app.get("/api/sessions/{session_id}/context")
    async def get_session_context(session_id: str, head_id: str | None = None):
        """Per-category input-token breakdown for the session (Claude Code /context).

        The breakdown itself is recomputed live by
        ``openprogram.runtime.context.token_accounting.session_stats``; the ``total_used`` /
        ``window`` / ``basis`` triple on top of it is the same record the
        composer ring reads, so panel and ring never disagree.
        """
        return await _conversation_read(session_id, _session_context, session_id, head_id)

    def _session_context(session_id: str, head_id: str | None) -> dict:
        from openprogram.runtime.context.token_accounting import session_stats as _cs
        from openprogram.interfaces.server import server as _s

        try:
            conv = _s._sessions.get(session_id) or {}
            stored = conv.get("_last_context_breakdown") or (
                (conv.get("_last_context_stats") or {}).get("breakdown")
            )
            same_head = bool(
                stored and (
                    head_id is None
                    or head_id == stored.get("head_id")
                    or (
                        head_id == conv.get("head_id")
                        and stored.get("head_id") in (None, conv.get("head_id"))
                    )
                )
            )
            same_rev = bool(stored) and int(stored.get("_context_rev") or 0) == int(
                conv.get("_context_rev") or 0
            )
            if same_head and same_rev:
                # The snapshot's rows are reused; its window is not — the
                # window is resolved from the session's model now, so one
                # saved with a wrong window cannot stick.
                occupancy = _s.session_context_stats(
                    session_id,
                    head_id=head_id,
                    estimated_total=int(stored.get("input_used") or 0),
                )
                return _cs.finalize_breakdown(stored, occupancy)
            bd = _cs.compute_breakdown(
                session_id,
                head_id,
                context_window=_s._conv_context_window(conv, head_id),
            )
            from openprogram.interfaces.server.server_runtime.context_stats import _branch_at_head
            if _branch_at_head(conv, head_id) is not None:
                # A branch that is not selected: the model named beside
                # the window is that branch's too.
                bd["model"] = _s._conv_model(conv, head_id)[1] or ""
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "tools": []}
        occupancy = _s.session_context_stats(
            session_id,
            head_id=head_id,
            estimated_total=int(bd.get("input_used") or 0),
            window=int(bd.get("context_window") or 0) or None,
        )
        return _cs.finalize_breakdown(bd, occupancy)

    @app.get("/api/sessions/{session_id}/dag")
    async def get_session_dag(session_id: str):
        """Full session session DAG as a TNode tree (step 8)."""
        from openprogram.runtime.execution.projections.dag import build_session_dag
        return await _conversation_read(
            session_id, lambda: {"tree": build_session_dag(session_id)})

    @app.get("/api/programs/meta")
    async def get_programs_meta():
        from openprogram.programs._host.meta_storage import load_programs_meta

        return JSONResponse(content=load_programs_meta({"favorites": [], "folders": {}}))

    @app.post("/api/programs/meta")
    async def save_programs_meta(body: dict = None):
        from openprogram.programs._host.meta_storage import save_programs_meta as save

        save(body or {})
        return JSONResponse(content={"ok": True})

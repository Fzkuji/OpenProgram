"""Read-only catalog endpoints: DAG tree, token stats, programs meta.

These routes are mostly thin DB wrappers (SessionDB + MODELS registry)
plus a ``_discover_functions`` server-helper call.
"""
from __future__ import annotations

import json
import os

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/functions")
    async def get_functions():
        from openprogram.webui import server as _s
        return JSONResponse(content=_s._discover_functions())

    @app.get("/api/tools")
    async def get_tools():
        """The regular (non-agentic) built-in tools — bash, file edits,
        web search, … Each carries a ``disabled`` flag so the Functions
        page can render a per-tool on/off switch: a disabled tool is
        filtered out of every LLM toolset (agent_tools() honours
        ``tools.disabled``), so the model never sees it. Toggle via
        ``POST /api/settings {key:"tools.disabled.<name>", value:<on>}``."""
        from openprogram.functions import agent_tools
        from openprogram.setup import read_disabled_tools
        disabled = read_disabled_tools()
        out = []
        # include_disabled=True so DISABLED tools still appear in the list
        # (otherwise agent_tools() filters them out and the user could
        # never switch them back on). full toolset = the whole universe.
        for t in agent_tools(toolset="full", include_disabled=True):
            if getattr(t, "_is_agentic", False):
                continue
            desc = (t.description or "").strip().split("\n")[0]
            out.append({
                "name": t.name,
                "description": desc,
                "disabled": t.name in disabled,
            })
        out.sort(key=lambda r: r["name"])
        return JSONResponse(content=out)

    # ── Functions meta (folders-as-categories) ────────────────────────
    # Same {favorites, folders} shape as programs_meta.json.
    # New folder → defaults to ALL exposed tools (per design: default-on).

    def _functions_meta_path():
        from openprogram.webui import server as _s
        return os.path.join(os.path.dirname(_s.__file__), "functions_meta.json")

    def _load_functions_meta() -> dict:
        p = _functions_meta_path()
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {"favorites": [], "folders": {}}

    def _save_functions_meta(data: dict):
        p = _functions_meta_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @app.get("/api/functions/meta")
    async def get_functions_meta():
        return JSONResponse(content=_load_functions_meta())

    @app.post("/api/functions/meta")
    async def save_functions_meta(body: dict = None):
        _save_functions_meta(body or {})
        return JSONResponse(content={"ok": True})

    @app.post("/api/functions/meta/create-folder")
    async def create_functions_folder(body: dict = None):
        """Create a new folder with ALL exposed tools (design: default-on).
        body: {"name": "folder name"}"""
        from openprogram.functions import agent_tools
        name = (body or {}).get("name", "New folder")
        meta = _load_functions_meta()
        # default = every exposed tool's name
        all_names = sorted(t.name for t in agent_tools(include_disabled=True)
                           if not getattr(t, "_is_agentic", False))
        meta["folders"][name] = all_names
        _save_functions_meta(meta)
        return JSONResponse(content={"ok": True, "folder": name,
                                     "tools": all_names})

    @app.get("/api/sessions/{session_id}/branches/tokens")
    async def get_branches_tokens(session_id: str):
        """Lightweight token summary for every branch tip in this session."""
        from openprogram.agent.session_db import default_db
        from openprogram.providers.models_generated import MODELS

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
                cands = [v for v in MODELS.values() if v.id == mid]
                if mid in MODELS:
                    cands.insert(0, MODELS[mid])
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
        return JSONResponse(content={"branches": out})

    @app.get("/api/sessions/{session_id}/tokens")
    async def get_session_tokens(session_id: str, head_id: str | None = None,
                                 model: str | None = None,
                                 provider: str | None = None):
        from openprogram.agent.session_db import default_db
        from openprogram.providers.models_generated import MODELS

        model_obj = None
        if model:
            key = f"{provider}/{model}" if provider else None
            model_obj = (MODELS.get(key) if key else None) or MODELS.get(model)
            if model_obj is None:
                for v in MODELS.values():
                    if v.id == model:
                        model_obj = v
                        break

        stats = default_db().get_branch_token_stats(
            session_id, head_id=head_id, model=model_obj,
        )

        if not stats["context_window"] and stats.get("model"):
            mid = stats["model"]
            candidates = [MODELS.get(mid)] if mid in MODELS else []
            candidates.extend(v for v in MODELS.values() if v.id == mid)
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

        return JSONResponse(content=stats)

    @app.get("/api/sessions/{session_id}/context-range")
    async def get_context_range(session_id: str, head_id: str | None = None):
        """Node ids the next chat message's LLM call will carry as context.

        That is the active branch — from root (or the most recent
        compaction summary) up to the head — which the dispatcher loads
        via ``get_branch`` and feeds to the context engine. The WebUI
        dims DAG nodes outside this set so the user can see, before
        sending, roughly how much history the next message will include.
        """
        from openprogram.agent.session_db import default_db

        branch = default_db().get_branch(session_id, head_id) or []
        node_ids = [m["id"] for m in branch if m.get("id")]
        return JSONResponse(content={
            "session_id": session_id,
            "node_ids": node_ids,
            "count": len(node_ids),
        })

    @app.get("/api/programs/meta")
    async def get_programs_meta():
        from openprogram.webui import server as _s
        meta_path = os.path.join(os.path.dirname(_s.__file__), "programs_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                return JSONResponse(content=json.load(f))
        return JSONResponse(content={"favorites": [], "folders": {}})

    @app.post("/api/programs/meta")
    async def save_programs_meta(body: dict = None):
        from openprogram.webui import server as _s
        meta_path = os.path.join(os.path.dirname(_s.__file__), "programs_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2)
        return JSONResponse(content={"ok": True})

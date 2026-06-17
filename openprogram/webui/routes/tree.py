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

    # ── Tool profiles ──────────────────────────────────────────────────
    # A profile = a named tool set the user configures on the Functions
    # page and selects in the chat composer. "default" = all exposed
    # tools (immutable). New profile = copy of default; user removes
    # tools they don't need for that scenario.

    def _functions_meta_path():
        from openprogram.webui import server as _s
        return os.path.join(os.path.dirname(_s.__file__), "functions_meta.json")

    def _all_tool_names() -> list[str]:
        """Every exposed tool name — leaf tools AND agentic programs.
        Profiles cover everything the model can use."""
        from openprogram.functions._runtime import exposed_names
        return sorted(exposed_names())

    def _load_profiles() -> dict:
        p = _functions_meta_path()
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            # migrate old {folders:} shape if present
            if "folders" in data and "profiles" not in data:
                data["profiles"] = data.pop("folders")
            return data
        return {"profiles": {"default": _all_tool_names()}, "active": "default"}

    def _save_profiles(data: dict):
        # ensure "default" always exists with all tools
        data.setdefault("profiles", {})["default"] = _all_tool_names()
        data.setdefault("active", "default")
        p = _functions_meta_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @app.get("/api/tool-profiles")
    async def get_tool_profiles():
        """All profiles + which is active. default profile is always
        regenerated from the live registry so new tools appear."""
        data = _load_profiles()
        data["profiles"]["default"] = _all_tool_names()
        return JSONResponse(content=data)

    @app.post("/api/tool-profiles")
    async def save_tool_profiles(body: dict = None):
        _save_profiles(body or {})
        return JSONResponse(content={"ok": True})

    @app.post("/api/tool-profiles/create")
    async def create_tool_profile(body: dict = None):
        """Create a new profile = copy of default (all tools).
        body: {"name": "profile name"}"""
        name = (body or {}).get("name", "new")
        data = _load_profiles()
        if name == "default":
            return JSONResponse(content={"ok": False, "error": "cannot overwrite default"}, status_code=400)
        data["profiles"][name] = list(_all_tool_names())
        _save_profiles(data)
        return JSONResponse(content={"ok": True, "profile": name,
                                     "tools": data["profiles"][name]})

    @app.post("/api/tool-profiles/delete")
    async def delete_tool_profile(body: dict = None):
        name = (body or {}).get("name", "")
        if name == "default":
            return JSONResponse(content={"ok": False, "error": "cannot delete default"}, status_code=400)
        data = _load_profiles()
        data["profiles"].pop(name, None)
        if data.get("active") == name:
            data["active"] = "default"
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
        if name == "default":
            return JSONResponse(content={"ok": False, "error": "cannot modify default"}, status_code=400)
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
        """Set the active profile. body: {"name":"research"}"""
        name = (body or {}).get("name", "default")
        data = _load_profiles()
        if name not in data["profiles"]:
            return JSONResponse(content={"ok": False, "error": "profile not found"}, status_code=404)
        data["active"] = name
        _save_profiles(data)
        return JSONResponse(content={"ok": True, "active": name})

    # Keep the old /api/functions/meta endpoints for compatibility
    # (the folder-as-toolset resolver in __init__.py reads "folders").
    @app.get("/api/functions/meta")
    async def get_functions_meta():
        data = _load_profiles()
        # Backward compat: expose profiles as "folders" too so the
        # _resolve_folder_toolset helper (agent_tools) finds them.
        return JSONResponse(content={
            "profiles": data.get("profiles", {}),
            "active": data.get("active", "default"),
            "folders": data.get("profiles", {}),  # compat
        })

    @app.post("/api/functions/meta")
    async def save_functions_meta(body: dict = None):
        _save_profiles(body or {})
        return JSONResponse(content={"ok": True})

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

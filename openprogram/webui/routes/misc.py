"""Misc endpoints — /healthz liveness probe and external module registration."""
from __future__ import annotations

import importlib

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/healthz")
    async def healthz():
        """Liveness + readiness probe. Reports DB connectivity, tool count, uptime."""
        import time as _time
        from openprogram.webui import server as _s
        info: dict = {
            "status": "ok",
            "checked_at": _time.time(),
            "uptime_seconds": int(_time.time() - _s._SERVER_START_TIME),
        }
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            session_count = len(db.list_sessions(limit=1))
            info["db_ok"] = True
            info["sessions_visible"] = session_count
            cutoff = _time.time() - 24 * 3600
            info["messages_24h"] = db.count_recent_nodes(cutoff)
        except Exception as e:
            info["db_ok"] = False
            info["db_error"] = f"{type(e).__name__}: {e}"
            info["status"] = "degraded"
        try:
            from openprogram.tools import list_registered_agent_tools
            info["tools_registered"] = len(list_registered_agent_tools())
        except Exception:
            info["tools_registered"] = 0
        return JSONResponse(content=info)

    @app.post("/api/register")
    async def register_external(body: dict = None):
        """Register an external module's @agentic_function callables."""
        if not body or "module" not in body:
            return JSONResponse(content={"error": "no module path"}, status_code=400)
        module_path = body["module"]
        try:
            mod = importlib.import_module(module_path)
            registered = []
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if callable(obj) and hasattr(obj, '_fn'):
                    registered.append(attr_name)
            return JSONResponse(content={
                "registered": True,
                "module": module_path,
                "functions": registered,
            })
        except ImportError as e:
            return JSONResponse(content={"error": f"Cannot import: {e}"}, status_code=400)

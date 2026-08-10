"""Misc endpoints — /healthz liveness probe and external module registration."""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

from fastapi.responses import JSONResponse

_HEAD_SHA: str | None = None


def _head_sha() -> str:
    """Git sha of the checkout serving this process, or "" when it isn't a
    checkout. Cached — it cannot change without a restart, and
    ``openprogram upgrade`` polls this endpoint in a loop."""
    global _HEAD_SHA
    if _HEAD_SHA is None:
        _HEAD_SHA = ""
        try:
            import openprogram
            root = Path(openprogram.__file__).resolve().parents[1]
            if (root / ".git").exists():
                res = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=str(root),
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0:
                    _HEAD_SHA = res.stdout.strip()
        except Exception:
            _HEAD_SHA = ""
    return _HEAD_SHA


def register(app):
    @app.get("/api/doctor")
    async def doctor_api():
        """Run the same checks as ``openprogram doctor`` and return the
        results as JSON for the web UI / slash command."""
        from openprogram._cli_cmds.doctor import run_checks
        results = run_checks()
        return JSONResponse(content={
            "results": results,
            "all_ok": all(r["ok"] for r in results),
        })

    @app.get("/healthz")
    async def healthz():
        """Non-identifying liveness probe available before authentication."""
        return JSONResponse(content={"status": "ok"})

    @app.get("/api/diagnostics")
    async def runtime_diagnostics():
        """Authenticated runtime and storage diagnostics."""
        import time as _time
        from openprogram.webui import server as _s

        info: dict = {
            "status": "ok",
            "checked_at": _time.time(),
            "uptime_seconds": int(_time.time() - _s._SERVER_START_TIME),
            "revision": _head_sha(),
        }
        try:
            from openprogram.agent.session_db import default_db

            db = default_db()
            info["database_ok"] = True
            info["has_visible_sessions"] = bool(db.list_sessions(limit=1))
            info["message_count_24h"] = db.count_recent_nodes(
                _time.time() - 24 * 3600
            )
        except Exception as exc:  # noqa: BLE001
            info["database_ok"] = False
            info["database_error"] = f"{type(exc).__name__}: {exc}"
            info["status"] = "degraded"
        try:
            from openprogram.functions import list_registered_agent_tools

            info["registered_tool_count"] = len(list_registered_agent_tools())
        except Exception:  # noqa: BLE001
            info["registered_tool_count"] = 0
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

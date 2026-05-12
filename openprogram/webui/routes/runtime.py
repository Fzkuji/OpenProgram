"""Runtime / provider switch endpoints (NOT model picker — that one's
in server.py because it rebinds module-level `_user_pinned_*` globals).

Three handlers:
  GET  /api/providers — list known providers + their key/CLI status
  POST /api/provider/{name} — switch the active provider
  GET  /api/models — list models for the active runtime
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/providers")
    async def get_providers():
        from openprogram.webui import server as _s
        return JSONResponse(content=_s._list_providers())

    @app.post("/api/provider/{name}")
    async def switch_provider(name: str, body: dict = None):
        from openprogram.webui import server as _s
        session_id = body.get("session_id") if body else None
        if session_id:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
            if conv and conv.get("provider_name") == name:
                return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        elif name == _s._runtime_management._default_provider:
            return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        try:
            _s._switch_runtime(name, session_id=session_id)
            return JSONResponse(content={"switched": True, "provider": name})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)

    @app.get("/api/models")
    async def list_models():
        """List available models for the current provider."""
        from openprogram.webui import server as _s
        with _s._runtime_management._runtime_lock:
            if _s._runtime_management._default_provider is None:
                (_s._runtime_management._default_provider,
                 _s._runtime_management._default_runtime) = _s._detect_default_provider()

        provider = _s._runtime_management._default_provider or "none"
        runtime = _s._runtime_management._default_runtime
        current_model = runtime.model if runtime else None

        model_list = []
        if runtime and hasattr(runtime, "list_models"):
            try:
                model_list = runtime.list_models()
            except Exception as e:
                print(f"[list_models] {provider} error: {e}")
        if current_model and current_model not in model_list:
            model_list = [current_model] + model_list

        return JSONResponse(content={
            "provider": provider,
            "current": current_model,
            "models": model_list,
        })

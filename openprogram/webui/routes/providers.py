"""Provider catalog routes — list/toggle/configure/fetch-models/test.

Pure dispatch to ``openprogram.webui._model_catalog`` and
``openprogram.providers.configuration``. Plus the web-search provider
catalog and per-env-var API-key reveal endpoint.

The heavier runtime-switching routes (/api/model, /api/provider/{name},
/api/models) still live in server.py because they mutate module-level
state via ``global`` statements.
"""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/search-providers/list")
    async def api_search_providers_list():
        """Web-search backend catalog (Tavily / Exa / DuckDuckGo).
        Mirrors /api/providers/list shape so the settings UI can reuse
        the same row-and-key-field components.

        ``default`` (str|null) tells callers which provider the user has
        pinned as default; ``providers[*].is_default`` mirrors the same
        info per-row for convenient list rendering.

        Each row now also carries the catalog metadata (``name``,
        ``description``, ``tier``, ``signup_url``, ``docs_url``,
        ``setup_steps``) sourced from
        ``openprogram.tools.web_search.catalog``. Unknown providers
        fall back to a synthesised display name + empty metadata so the
        UI can still render the row.
        """
        from openprogram.webui import server as _s
        from openprogram.tools.web_search.registry import registry as _wsr
        from openprogram.tools.web_search import catalog as _wsc
        import openprogram.tools.web_search.providers  # noqa: F401
        from openprogram.setup import read_search_default_provider
        default = read_search_default_provider()
        out = []
        for p in _wsr.all():
            env_var = (list(getattr(p, "requires_env", ()) or []) or [None])[0]
            configured = bool(_s._get_api_key(env_var)) if env_var else True
            meta = _wsc.get_dict(p.name) or {}
            out.append({
                "id": p.name,
                # Prefer the catalog's display name (e.g. "Google PSE")
                # over the raw registry id (.capitalize() of "google"
                # would lose the "PSE" qualifier).
                "name": meta.get("name") or p.name.capitalize(),
                "description": meta.get("description", ""),
                "tier": meta.get("tier", ""),
                "signup_url": meta.get("signup_url"),
                "docs_url": meta.get("docs_url"),
                "setup_steps": meta.get("setup_steps") or [],
                "priority": p.priority,
                "env_var": env_var,
                "configured": configured,
                "available": bool(getattr(p, "is_available", lambda: False)()),
                "is_default": (default == p.name),
            })
        return JSONResponse(content={"providers": out, "default": default})

    @app.get("/api/search-providers/default")
    async def api_search_providers_default():
        from openprogram.setup import read_search_default_provider
        return JSONResponse(content={"provider": read_search_default_provider()})

    @app.post("/api/search-providers/{provider_id}/test")
    async def api_test_search_provider(provider_id: str, body: dict = None):
        """Run a tiny live query against the named search backend.

        Mirrors /api/providers/{name}/test (the LLM provider connectivity
        check). Returns ``{ok, latency_ms, error?}`` so the UI can show a
        green check or red X with the failure reason. Uses a stable
        zero-result-friendly query ("openprogram health check") and asks
        for 1 result to minimise API quota burn.
        """
        import time as _t
        from openprogram.tools.web_search.registry import registry as _wsr
        import openprogram.tools.web_search.providers  # noqa: F401
        if not _wsr.has(provider_id):
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"unknown provider {provider_id!r}"},
            )
        backend = _wsr.get(provider_id)
        # is_available() catches missing env vars before we burn a
        # request budget on a guaranteed-to-fail call.
        try:
            if not backend.is_available():
                missing = [e for e in (getattr(backend, "requires_env", None) or [])
                           if not os.environ.get(e)]
                return JSONResponse(content={
                    "ok": False,
                    "error": (
                        f"Backend not available — set env: {missing}"
                        if missing
                        else "Backend reports unavailable"
                    ),
                })
        except Exception as e:
            return JSONResponse(content={
                "ok": False,
                "error": f"is_available() raised: {type(e).__name__}: {e}",
            })

        started = _t.time()
        try:
            results = backend.search("openprogram health check", num_results=1)
            latency_ms = int((_t.time() - started) * 1000)
            return JSONResponse(content={
                "ok": True,
                "latency_ms": latency_ms,
                "result_count": len(results or []),
            })
        except Exception as e:
            latency_ms = int((_t.time() - started) * 1000)
            return JSONResponse(content={
                "ok": False,
                "latency_ms": latency_ms,
                "error": f"{type(e).__name__}: {e}",
            })

    @app.post("/api/search-providers/default")
    async def api_set_search_providers_default(body: dict = None):
        from openprogram.setup import write_search_default_provider
        from openprogram.tools.web_search.registry import registry as _wsr
        import openprogram.tools.web_search.providers  # noqa: F401
        name = (body or {}).get("provider")
        if name in (None, "", "auto"):
            write_search_default_provider(None)
            return JSONResponse(content={"ok": True, "provider": None})
        name = str(name).strip().lower()
        if not _wsr.has(name):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"unknown provider {name!r}"},
            )
        write_search_default_provider(name)
        return JSONResponse(content={"ok": True, "provider": name})

    @app.get("/api/providers/list")
    async def api_providers_list():
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={"providers": _mc.list_providers()})

    @app.get("/api/providers/{name}/models")
    async def api_provider_models(name: str):
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={
            "provider": name,
            "models": _mc.list_models_for_provider(name),
        })

    @app.post("/api/providers/{name}/toggle")
    async def api_toggle_provider(name: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        enabled = bool((body or {}).get("enabled", False))
        return JSONResponse(content=_mc.toggle_provider(name, enabled))

    @app.post("/api/providers/{name}/models/{model_id:path}/toggle")
    async def api_toggle_model(name: str, model_id: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        enabled = bool((body or {}).get("enabled", False))
        return JSONResponse(content=_mc.toggle_model(name, model_id, enabled))

    @app.get("/api/config/key/{env_var}")
    async def api_get_api_key(env_var: str, reveal: bool = False):
        """Return the current value of an API-key env var, masked by
        default. With ?reveal=1 returns plaintext (only safe because the
        webui is bound to localhost)."""
        from openprogram.webui import server as _s
        val = os.environ.get(env_var) or _s._load_config().get("api_keys", {}).get(env_var, "")
        if not val:
            return JSONResponse(content={"has_value": False, "value": "", "masked": ""})
        if reveal:
            return JSONResponse(content={"has_value": True, "value": val, "masked": ""})
        if len(val) > 12:
            mid = "•" * min(max(len(val) - 8, 6), 24)
            masked = val[:4] + mid + val[-4:]
        else:
            masked = "•" * len(val)
        return JSONResponse(content={"has_value": True, "value": "", "masked": masked})

    @app.get("/api/models/enabled")
    async def api_enabled_models():
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={"models": _mc.list_enabled_models()})

    @app.get("/api/providers/{name}/config")
    async def api_provider_config(name: str):
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content=_mc.get_provider_config(name))

    @app.post("/api/providers/{name}/config")
    async def api_set_provider_config(name: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content=_mc.set_provider_config(name, body or {}))

    @app.post("/api/providers/{name}/fetch-models")
    async def api_fetch_models(name: str):
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content=_mc.fetch_models_remote(name))

    @app.post("/api/providers/{name}/test")
    async def api_test_provider(name: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        model = (body or {}).get("model")
        return JSONResponse(content=_mc.test_provider(name, model=model))

    @app.delete("/api/providers/{name}/models/{model_id:path}")
    async def api_delete_custom_model(name: str, model_id: str):
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content=_mc.remove_custom_model(name, model_id))

    @app.get("/api/providers/{name}/configure")
    async def get_provider_configure(name: str):
        from openprogram.providers import configuration as _cfg
        entry = _cfg.get_provider(name)
        if entry is None:
            return JSONResponse(
                content={"error": f"No configuration for provider {name!r}"},
                status_code=404,
            )
        return JSONResponse(content={
            "provider": name,
            "label": entry["label"],
            "type": entry["type"],
            "description": entry.get("description", ""),
            "steps": [{"id": s["id"], "label": s["label"]} for s in entry["steps"]],
        })

    @app.post("/api/providers/{name}/configure/step/{step_id}")
    async def run_configure_step(name: str, step_id: str, body: dict = None):
        from openprogram.providers import configuration as _cfg
        ctx = dict(body or {})
        result = _cfg.run_step(name, step_id, ctx)
        return JSONResponse(content={"result": result, "context": ctx})

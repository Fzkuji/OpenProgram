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
        the same row-and-key-field components."""
        from openprogram.webui import server as _s
        from openprogram.tools.web_search.registry import registry as _wsr
        import openprogram.tools.web_search.providers  # noqa: F401
        descs = {
            "tavily": "LLM-tuned search (Tavily API). Snippets pre-summarised for agents. Free tier: 1000 queries/month.",
            "exa": "Neural search (Exa API). Catches semantically related pages keyword engines miss.",
            "perplexity": "Sonar API — returns an LLM-written answer with citations. Good for one-shot Q&A. Pay-as-you-go.",
            "brave": "Independent index, privacy-first. Free tier (Data for AI): 2000 queries/month.",
            "google": "Real Google results via Programmable Search Engine. Free tier: 100 queries/day. Needs GOOGLE_PSE_API_KEY + GOOGLE_PSE_CX.",
            "firecrawl": "SERP + full page content in one call (no follow-up fetch needed). Free tier: 500 credits/month.",
            "searxng": "Self-hosted meta search (aggregates Google/Bing/DDG). Set SEARXNG_URL to your instance. No API key.",
            "duckduckgo": "Zero-key public fallback. No setup required.",
        }
        out = []
        for p in _wsr.all():
            env_var = (list(getattr(p, "requires_env", ()) or []) or [None])[0]
            configured = bool(_s._get_api_key(env_var)) if env_var else True
            out.append({
                "id": p.name,
                "name": p.name.capitalize(),
                "description": descs.get(p.name, ""),
                "priority": p.priority,
                "env_var": env_var,
                "configured": configured,
                "available": bool(getattr(p, "is_available", lambda: False)()),
            })
        return JSONResponse(content={"providers": out})

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

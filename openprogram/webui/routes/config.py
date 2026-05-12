"""/api/config* — generic key-value config + bulk API-key save + verify."""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse


def _validate_api_key(env_var: str, value: str) -> str | None:
    """Lightweight API-key test call. Returns error string or None on success."""
    try:
        if env_var == "OPENAI_API_KEY":
            import openai
            client = openai.OpenAI(api_key=value)
            client.models.list()
            return None
        elif env_var == "ANTHROPIC_API_KEY":
            import anthropic
            client = anthropic.Anthropic(api_key=value)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return None
        elif env_var in ("GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
            import google.generativeai as genai
            genai.configure(api_key=value)
            for m in ("gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"):
                try:
                    model = genai.GenerativeModel(m)
                    model.generate_content("hi", generation_config={"max_output_tokens": 1})
                    return None
                except Exception:
                    continue
            list(genai.list_models())
            return None
        else:
            return None  # Unknown key type — skip validation
    except Exception as e:
        return str(e)


def register(app):
    @app.get("/api/config")
    async def get_config():
        from openprogram.webui import server as _s
        config = _s._load_config()
        keys = config.get("api_keys", {})
        masked = {k: (v[:8] + "..." if len(v) > 8 else "***") for k, v in keys.items() if v}
        return JSONResponse(content={"api_keys": masked})

    @app.post("/api/config")
    async def save_config(body: dict = None):
        from openprogram.webui import server as _s
        if not body or "api_keys" not in body:
            return JSONResponse(content={"error": "Missing api_keys"}, status_code=400)
        config = _s._load_config()
        if "api_keys" not in config:
            config["api_keys"] = {}
        for key, val in body["api_keys"].items():
            val = val.strip()
            if val:
                config["api_keys"][key] = val
                os.environ[key] = val
            else:
                config["api_keys"].pop(key, None)
                os.environ.pop(key, None)
        _s._save_config(config)
        return JSONResponse(content={"saved": True})

    @app.post("/api/config/verify")
    async def verify_key(body: dict = None):
        """Verify a single API key without saving."""
        from openprogram.webui import server as _s
        if not body or "env" not in body:
            return JSONResponse(content={"error": "Missing env"}, status_code=400)
        value = body.get("value", "")
        if not value or value.endswith("..."):
            config = _s._load_config()
            value = config.get("api_keys", {}).get(body["env"], "")
        if not value:
            return JSONResponse(content={"valid": False, "error": "No key provided"})
        error = _validate_api_key(body["env"], value)
        return JSONResponse(content={"valid": error is None, "error": error})

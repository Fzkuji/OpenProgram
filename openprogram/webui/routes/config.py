"""/api/config* — generic key-value config + bulk API-key save + verify."""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse


def _validate_api_key(env_var: str, value: str) -> str | None:
    """Validate one API key. Returns an error string, or ``None`` on success
    (or when the key isn't an LLM provider key we know how to probe).

    Thin shim over the unified validator: map the env var to a provider id and
    run the model-independent auth probe. This replaces the old per-provider
    branches that (a) only covered OpenAI/Anthropic/Google and silently no-op'd
    ~17 others, and (b) spent a real completion to validate. Now every
    OpenAI-compatible / OpenRouter / Anthropic / Google key is checked without
    invoking a model. See docs/design/providers/auth/credential-validation-unification.md.
    """
    try:
        from openprogram.webui._model_listing import (
            provider_id_for_env_var,
            validate_credential,
        )
        pid = provider_id_for_env_var(env_var)
        if pid is None:
            return None  # not an LLM provider key (search keys, etc.) — skip
        r = validate_credential(pid, api_key=value, use_cache=False)
        # `unknown` (offline / ambiguous) must not block a save — only a
        # definitively rejected credential is an error here.
        return r.detail if r.status == "invalid_credential" else None
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

    # /api/settings — the schema-driven settings the TUI panel + `openprogram
    # config` use, mirrored over REST so the web pages render the SAME source.
    @app.get("/api/settings")
    async def get_settings_api():
        from openprogram.config_schema import get_settings
        return JSONResponse(content={"settings": get_settings()})

    @app.post("/api/settings")
    async def set_setting_api(body: dict = None):
        from openprogram.config_schema import set_setting
        if not body or "key" not in body:
            return JSONResponse(content={"error": "Missing key"}, status_code=400)
        res = set_setting(body["key"], body.get("value"))
        return JSONResponse(content=res, status_code=400 if res.get("error") else 200)

    @app.post("/api/config")
    async def save_config(body: dict = None):
        from openprogram import setup as _setup
        if not body or "api_keys" not in body:
            return JSONResponse(content={"error": "Missing api_keys"}, status_code=400)
        items = {k: (v or "").strip() for k, v in body["api_keys"].items()}
        # Validate BEFORE mutating: reject a masked / garbled value. API keys are
        # printable ASCII; the UI's masked preview is "••••" (U+2022 bullets) and
        # saving those would overwrite the real key with non-ASCII junk that then
        # crashes outbound requests with a UnicodeEncodeError.
        for key, val in items.items():
            if val and any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in val):
                return JSONResponse(
                    content={"error": (
                        f"{key}: the value has invalid characters — it looks "
                        "like the masked placeholder, not a real key. Re-type "
                        "the key and Save."
                    )},
                    status_code=400,
                )

        def _merge_keys(config: dict) -> None:
            keys = config.setdefault("api_keys", {})
            for key, val in items.items():
                if val:
                    keys[key] = val
                else:
                    keys.pop(key, None)

        # Atomic read-modify-write so a concurrent settings save (TUI / CLI)
        # can't clobber these keys, or vice-versa.
        _setup.update_config(_merge_keys)
        # Reflect into the live process env so the running worker resolves the
        # key immediately.
        for key, val in items.items():
            if val:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)
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

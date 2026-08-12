"""/api/config* — generic key-value config + bulk API-key save + verify."""
from __future__ import annotations

import os
from typing import Any

from fastapi import Body, Request, Response
from fastapi.responses import JSONResponse

from ._credential_secrets import (
    is_declared_credential_name,
    is_nonempty_printable_ascii,
    mask_credential,
)


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
    except Exception:
        # A transport failure is an ``unknown`` validation result. Only a
        # definite provider rejection may block credential replacement.
        return None


def register(app):
    def resource_limits_view(session_id: str) -> dict[str, Any]:
        from openprogram.agent.resource_governance import (
            global_resource_limits, resolve_resource_limits, session_resource_limits,
        )
        from openprogram.agent.session_db import default_db
        if default_db().get_session(session_id) is None:
            raise KeyError(session_id)
        return resolve_resource_limits(
            global_resource_limits(), session=session_resource_limits(session_id),
        ).to_dict()

    @app.get("/api/sessions/{session_id}/resource-limits")
    async def get_session_resource_limits_api(session_id: str):
        try:
            return JSONResponse(content=resource_limits_view(session_id))
        except KeyError:
            return JSONResponse(content={"error": "session not found"}, status_code=404)

    @app.put("/api/sessions/{session_id}/resource-limits")
    async def put_session_resource_limits_api(session_id: str, request: Request, body: Any = Body(default=None)):
        if not isinstance(body, dict) or "limits" not in body:
            return JSONResponse(content={"error": "body must contain limits"}, status_code=400)
        try:
            resource_limits_view(session_id)
        except KeyError:
            return JSONResponse(content={"error": "session not found"}, status_code=404)
        # Authority comes from the authenticated connection only. A body-supplied
        # "authority" is a forgery attempt and is refused, never merged in.
        auth_state = getattr(request.app.state, "owner_auth", None)
        authority = getattr(auth_state, "authority", None)
        if authority is None or set(body) != {"limits"}:
            return JSONResponse(content={"error": "trusted owner authority required"}, status_code=403)
        try:
            from openprogram.agent.resource_governance import save_session_resource_limits
            save_session_resource_limits(session_id, body["limits"], authority=authority)
            return JSONResponse(content=resource_limits_view(session_id))
        except PermissionError:
            return JSONResponse(content={"error": "owner authority required"}, status_code=403)
        except (TypeError, ValueError) as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)

    @app.get("/api/config")
    async def get_config():
        from openprogram.webui import server as _s
        config = _s._load_config()
        keys = config.get("api_keys", {})
        masked = {k: mask_credential(v) for k, v in keys.items() if v}
        return JSONResponse(content={"api_keys": masked})

    # /api/settings — the schema-driven settings the TUI panel + `openprogram
    # config` use, mirrored over REST so the web pages render the SAME source.
    @app.get("/api/settings")
    async def get_settings_api():
        from openprogram.config_schema import get_settings
        return JSONResponse(content={"settings": get_settings()})

    @app.post("/api/settings")
    async def set_setting_api(body: Any = Body(default=None)):
        """Set one schema-declared setting. Credentials are not settings — they
        go through /api/config and the account endpoints, which mask and
        rotate; ``set_setting`` writes plain config with neither."""
        if not isinstance(body, dict) or not set(body).issubset({"key", "value"}):
            return JSONResponse(
                content={"error": "body must contain only key and value"},
                status_code=400,
            )
        key = body.get("key")
        if not isinstance(key, str) or not key:
            return JSONResponse(content={"error": "Missing key"}, status_code=400)
        if is_declared_credential_name(key) or "api_key" in key.lower():
            return JSONResponse(
                content={"error": "credentials are not settings"}, status_code=400
            )
        from openprogram.config_schema import set_setting
        res = set_setting(key, body.get("value"))
        return JSONResponse(content=res, status_code=400 if res.get("error") else 200)

    @app.post("/api/config")
    async def save_config(body: Any = Body(default=None)):
        from openprogram import setup as _setup
        if not isinstance(body, dict) or set(body) != {"api_keys"}:
            return JSONResponse(
                content={"error": "body must contain only api_keys"},
                status_code=400,
            )
        items = body["api_keys"]
        if not isinstance(items, dict):
            return JSONResponse(
                content={"error": "api_keys must be an object"},
                status_code=400,
            )

        # Validate the complete request before probing or mutating anything.
        for key, val in items.items():
            if not is_declared_credential_name(key):
                return JSONResponse(
                    content={"error": f"unknown credential name: {key}"},
                    status_code=400,
                )
            if not is_nonempty_printable_ascii(val):
                return JSONResponse(
                    content={"error": f"{key}: invalid credential value"},
                    status_code=400,
                )

        for key, val in items.items():
            error = _validate_api_key(key, val)
            if error is not None:
                return JSONResponse(
                    content={"error": f"{key}: credential rejected"},
                    status_code=400,
                )

        def _merge_keys(config: dict) -> None:
            keys = config.setdefault("api_keys", {})
            for key, val in items.items():
                keys[key] = val

        # Atomic read-modify-write so a concurrent settings save (TUI / CLI)
        # can't clobber these keys, or vice-versa.
        _setup.update_config(_merge_keys)
        # Reflect into the live process env so the running worker resolves the
        # key immediately.
        for key, val in items.items():
            os.environ[key] = val
        return JSONResponse(content={"saved": True})

    @app.delete("/api/config/key/{env_var}")
    async def delete_config_key(env_var: str, request: Request):
        if not is_declared_credential_name(env_var):
            return JSONResponse(
                content={"error": "unknown credential name"},
                status_code=404,
            )
        if await request.body():
            return JSONResponse(
                content={"error": "request body is not allowed"},
                status_code=400,
            )

        from openprogram import setup as _setup

        def _delete_key(config: dict) -> None:
            keys = config.get("api_keys")
            if isinstance(keys, dict):
                keys.pop(env_var, None)

        _setup.update_config(_delete_key)
        os.environ.pop(env_var, None)
        return Response(status_code=204)

    @app.post("/api/config/verify")
    async def verify_key(body: Any = Body(default=None)):
        """Probe one credential without saving it.

        ``value`` omitted ⇒ probe the STORED credential, which is how the UI
        re-checks a key it can no longer read. A supplied ``value`` must be a
        real key: a displayed mask is rejected rather than silently falling
        back to the stored value, so "verify" can never report a mask as valid.
        """
        if not isinstance(body, dict) or not set(body).issubset({"env", "value"}):
            return JSONResponse(
                content={"error": "body must contain only env and value"},
                status_code=400,
            )
        env_var = body.get("env")
        if not is_declared_credential_name(env_var):
            return JSONResponse(
                content={"error": "unknown credential name"}, status_code=404
            )

        from openprogram.webui import server as _s

        if "value" in body:
            value = body["value"]
            if not is_nonempty_printable_ascii(value):
                return JSONResponse(
                    content={"error": "invalid credential value"}, status_code=400
                )
            stored = _s._load_config().get("api_keys", {}).get(env_var, "")
            if stored and value == mask_credential(stored):
                return JSONResponse(
                    content={"error": "a masked value is not a credential"},
                    status_code=400,
                )
        else:
            value = os.environ.get(env_var) or _s._load_config().get(
                "api_keys", {}
            ).get(env_var, "")
            if not value:
                return JSONResponse(content={"valid": False, "error": "No key stored"})

        error = _validate_api_key(env_var, value)
        return JSONResponse(content={"valid": error is None, "error": error})

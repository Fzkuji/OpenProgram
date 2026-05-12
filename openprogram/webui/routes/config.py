"""/api/config* — generic key-value config + bulk API-key save."""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse


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

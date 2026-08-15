"""Owner-authenticated bridge from the stdio MCP process to browser control."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException


def _owner_id(payload: dict[str, Any]) -> str:
    value = payload.get("owner_id")
    if not isinstance(value, str) or not value.startswith("mcp:") or len(value) > 256:
        raise HTTPException(status_code=400, detail="invalid computer use owner")
    return value


def register(app: FastAPI) -> None:
    @app.post("/api/computer-use")
    def computer_use(payload: dict[str, Any]):
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="invalid computer use arguments")
        owner_id = _owner_id(payload)
        from openprogram.programs._runtime import _normalize_result
        from openprogram.programs.agentic_functions.browser_agent import (
            execute_direct_computer_use,
        )

        try:
            raw = execute_direct_computer_use(arguments, owner_id=owner_id)
            result = _normalize_result(
                raw,
                call_id="mcp-http-" + uuid.uuid4().hex,
                max_chars=100_000,
                persist_full=False,
                head_ratio=0.7,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="computer use failed") from exc
        return {"ok": True, "result": result.model_dump(mode="json")}

    @app.post("/api/computer-use/release-owner")
    def release_owner(payload: dict[str, Any]):
        owner_id = _owner_id(payload)
        from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
            get_registry,
        )

        get_registry().release_owner(owner_id)
        return {"ok": True}

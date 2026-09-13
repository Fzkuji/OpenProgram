"""Owner-authenticated bridge from the stdio MCP process to browser control."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException


def _owner_id(payload: dict[str, Any]) -> str:
    value = payload.get("owner_id")
    if not isinstance(value, str) or not value.startswith("mcp:") or len(value) > 256:
        raise HTTPException(status_code=400, detail="invalid web use owner")
    return value


def register(app: FastAPI) -> None:
    def execute(payload: dict[str, Any], *, legacy: bool = False):
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="invalid web use arguments")
        arguments = dict(arguments)
        if legacy and "computer_session_id" in arguments:
            arguments.setdefault(
                "web_session_id", arguments.pop("computer_session_id")
            )
        owner_id = _owner_id(payload)
        from openprogram.programs._runtime import _normalize_result
        from openprogram.programs.workflow.browser import (
            execute_direct_web_use,
        )

        try:
            raw = execute_direct_web_use(arguments, owner_id=owner_id)
            if legacy:
                if hasattr(raw, "json_data"):
                    metadata = dict(raw.json_data or {})
                    if "web_session_id" in metadata:
                        metadata["computer_session_id"] = metadata.pop(
                            "web_session_id"
                        )
                        raw.json_data = metadata
                elif isinstance(raw, dict) and "web_session_id" in raw:
                    raw = dict(raw)
                    raw["computer_session_id"] = raw.pop("web_session_id")
            result = _normalize_result(
                raw,
                call_id="mcp-http-" + uuid.uuid4().hex,
                max_chars=100_000,
                persist_full=False,
                head_ratio=0.7,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="web use failed") from exc
        return {"ok": True, "result": result.model_dump(mode="json")}

    @app.post("/api/web-use")
    def web_use(payload: dict[str, Any]):
        return execute(payload)

    @app.post("/api/computer-use", include_in_schema=False)
    def legacy_computer_use(payload: dict[str, Any]):
        return execute(payload, legacy=True)

    @app.post("/api/web-use/release-owner")
    @app.post("/api/computer-use/release-owner", include_in_schema=False)
    def release_owner(payload: dict[str, Any]):
        owner_id = _owner_id(payload)
        from openprogram.programs.workflow.browser.web_use_runtime import (
            get_registry,
        )

        get_registry().release_owner(owner_id)
        return {"ok": True}

    @app.post("/api/web-use/release-pages", include_in_schema=False)
    def release_pages(payload: dict[str, Any]):
        owner_id = _owner_id(payload)
        tokens = payload.get("page_context_tokens")
        if not isinstance(tokens, list) or any(
            not isinstance(token, str) or not token for token in tokens
        ):
            raise HTTPException(status_code=400, detail="invalid page capabilities")
        from openprogram.programs.workflow.browser.web_use_runtime import (
            get_registry,
        )

        released = get_registry().release_page_capabilities(
            tokens, owner_id=owner_id,
        )
        return {"ok": True, "released": released}

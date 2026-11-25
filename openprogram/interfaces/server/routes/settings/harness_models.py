"""Which model Codex and Claude Code use outside a chat (Settings → Third-Party Harnesses).

  GET /api/harnesses/models   each harness's choice, and whether its own
                              config has it ("Also in the terminal") or drifted
  PUT /api/harnesses/models   {"harness", "model", "tiers"?, "terminal"?}

``model`` is ``""`` for the harness's own sign-in or a ``provider/model``
ref of an OpenProgram model. The choice is stored as settings
(``harnesses.<id>.*``, ``state/settings.py``). With ``terminal`` on, the
harness's own config file points at the gateway
(``models/gateway/harness_config.py``); turning it off, or going back to the
harness's own sign-in, restores what was there.
"""
from __future__ import annotations

from typing import Any

from fastapi import Body
from fastapi.responses import JSONResponse

_LABELS = {"codex": "Codex", "claude_code": "Claude Code"}
_TIERS = ("opus", "sonnet", "haiku")


def _row(harness: str) -> dict[str, Any]:
    from openprogram.models.gateway import harness_config
    from openprogram.state.settings import setting_value
    row: dict[str, Any] = {
        "id": harness,
        "label": _LABELS[harness],
        "model": setting_value(f"harnesses.{harness}.model") or "",
        "terminal": bool(setting_value(f"harnesses.{harness}.terminal")),
    }
    if harness == "claude_code":
        tiers = setting_value("harnesses.claude_code.tiers") or {}
        row["tiers"] = {tier: tiers.get(tier, "") for tier in _TIERS}
    try:
        row["config"] = harness_config.status(harness)
    except Exception as exc:  # noqa: BLE001 - shown on the card, never hidden
        row["config"] = {"path": "", "applied": False, "drift": [], "error": str(exc)}
    return row


def _save(harness: str, key: str, value: Any) -> str | None:
    from openprogram.state.settings import set_setting
    result = set_setting(f"harnesses.{harness}.{key}", value)
    return result.get("error")


def register(app) -> None:
    @app.get("/api/harnesses/models")
    def api_harness_models():
        return JSONResponse({"harnesses": [_row(h) for h in _LABELS]})

    @app.put("/api/harnesses/models")
    def api_set_harness_model(body: Any = Body(default=None)):
        from openprogram.models.gateway import harness_config
        if not isinstance(body, dict) or body.get("harness") not in _LABELS:
            return JSONResponse({"error": "harness must be codex or claude_code"}, status_code=400)
        harness = body["harness"]
        current = _row(harness)
        model = body.get("model", current["model"])
        terminal = body.get("terminal", current["terminal"])
        tiers = body.get("tiers", current.get("tiers"))
        if not isinstance(model, str) or (model and "/" not in model):
            return JSONResponse({"error": "model must be empty or provider/model"}, status_code=400)
        if not isinstance(terminal, bool):
            return JSONResponse({"error": "terminal must be true or false"}, status_code=400)
        if harness == "claude_code" and tiers is not None and (
                not isinstance(tiers, dict) or any(k not in _TIERS or not isinstance(v, str) for k, v in tiers.items())):
            return JSONResponse({"error": "tiers maps opus, sonnet and haiku to models"}, status_code=400)
        terminal = terminal and bool(model)
        try:
            if terminal:
                versions = None
                if harness == "codex":
                    from openprogram.runtime.harnesses.codex.maintenance import prompt_versions
                    versions = prompt_versions()
                harness_config.apply(harness, model, tiers if harness == "claude_code" else None,
                                     codex_versions=versions)
            elif current["config"].get("applied"):
                harness_config.restore(harness)
        except harness_config.HarnessConfigError as exc:
            return JSONResponse({"error": str(exc), **_row(harness)}, status_code=409)
        for key, value in (("model", model), ("terminal", terminal)):
            error = _save(harness, key, value)
            if error:
                return JSONResponse({"error": error}, status_code=400)
        if harness == "claude_code" and tiers is not None:
            error = _save(harness, "tiers", {k: v for k, v in tiers.items() if v})
            if error:
                return JSONResponse({"error": error}, status_code=400)
        return JSONResponse(_row(harness))

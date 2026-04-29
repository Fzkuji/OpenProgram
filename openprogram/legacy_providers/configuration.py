"""
Shared provider configuration framework.

Each provider declares a list of configuration steps (check_cli, check_auth,
select_model, save, ...) as callables. Both the CLI wizard
(`openprogram configure`) and the WebUI wizard modal consume this same
schema, so configuration logic lives in one place.

Step contract:
    fn(context: dict) -> dict with:
        status:     "ok" | "needs_input" | "error"
        message:    human-readable status / instruction
        data:       optional payload (detected email, model options, ...)
        fix:        optional — shell command the user can run to resolve
        input_key:  optional — for "needs_input", the ctx key to populate
        options:    optional — for "needs_input", list of [(value, desc), ...]

The `context` dict is mutable: caller (CLI or WebUI) writes user input
into it between steps, and step functions write their detection results
into it.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Codex configuration steps
# ---------------------------------------------------------------------------

_CODEX_MODELS = [
    ("gpt-5.5-mini", "fast & cheap — default"),
    ("gpt-5.5", "balanced"),
    ("gpt-5.5-pro", "best reasoning, slow & expensive"),
]


def _decode_codex_jwt_email(access_token: str) -> str | None:
    """JWT payload → email from 'https://api.openai.com/profile'.

    Best-effort: returns None on any parse error. Matches the logic in
    OpenClaw's openai-codex-auth-identity.ts (decodeCodexJwtPayload +
    resolveCodexAuthIdentity).
    """
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        profile = payload.get("https://api.openai.com/profile") or {}
        email = profile.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    except Exception:
        return None
    return None


def _codex_home() -> Path:
    """Honors $CODEX_HOME, defaults to ~/.codex (matches Codex CLI itself)."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    if not configured:
        return Path.home() / ".codex"
    if configured == "~":
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    return Path(configured).resolve()


def _codex_check_cli(ctx: dict) -> dict:
    path = shutil.which("codex")
    if not path:
        return {
            "status": "error",
            "message": "Codex CLI not found on PATH.",
            "fix": "npm install -g @openai/codex",
        }
    ctx["cli_path"] = path
    return {"status": "ok", "message": f"Codex CLI found at {path}"}


def _codex_check_auth(ctx: dict) -> dict:
    auth_path = _codex_home() / "auth.json"
    if not auth_path.exists():
        return {
            "status": "error",
            "message": "Codex CLI is not logged in.",
            "fix": "codex login --device-auth",
        }
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "message": f"Cannot read {auth_path}: {e}"}

    if data.get("auth_mode") != "chatgpt":
        return {
            "status": "error",
            "message": (
                f"auth_mode is {data.get('auth_mode')!r}, need 'chatgpt'. "
                "Re-login with device auth to use your ChatGPT subscription."
            ),
            "fix": "codex login --device-auth",
        }

    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access.strip():
        return {"status": "error", "message": "No access_token in auth.json."}

    email = _decode_codex_jwt_email(access)
    ctx["auth_path"] = str(auth_path)
    ctx["email"] = email
    ctx["account_id"] = tokens.get("account_id")
    return {
        "status": "ok",
        "message": f"Codex logged in as {email or '(account email unavailable)'}",
        "data": {"email": email, "account_id": ctx["account_id"]},
    }


def _codex_select_model(ctx: dict) -> dict:
    if ctx.get("model"):
        return {"status": "ok", "message": f"Model: {ctx['model']}"}
    return {
        "status": "needs_input",
        "message": "Pick the default Codex model:",
        "input_key": "model",
        "options": [{"value": v, "desc": d} for v, d in _CODEX_MODELS],
        "default": _CODEX_MODELS[0][0],
    }


def _codex_save(ctx: dict) -> dict:
    config_dir = Path.home() / ".openprogram"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    model = ctx.get("model") or _CODEX_MODELS[0][0]
    existing["default_provider"] = "openai-codex"
    existing["default_model"] = model
    config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "message": f"Saved {config_path}: default_provider=openai-codex, default_model={model}",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROVIDER_CONFIG: dict[str, dict] = {
    "openai-codex": {
        "label": "OpenAI Codex (ChatGPT subscription)",
        "type": "cli-oauth",
        "description": (
            "Use your ChatGPT Plus/Pro/Business subscription via the Codex CLI. "
            "Requires `codex` on PATH and a one-time `codex login --device-auth`."
        ),
        "steps": [
            {"id": "check_cli",    "label": "Check codex CLI installed", "run": _codex_check_cli},
            {"id": "check_auth",   "label": "Verify codex login",        "run": _codex_check_auth},
            {"id": "select_model", "label": "Choose default model",      "run": _codex_select_model},
            {"id": "save",         "label": "Save configuration",        "run": _codex_save},
        ],
    },
}


def list_providers() -> list[dict]:
    """Metadata for every configurable provider (for menu UIs)."""
    return [
        {
            "id": pid,
            "label": entry["label"],
            "type": entry["type"],
            "description": entry.get("description", ""),
            "step_ids": [s["id"] for s in entry["steps"]],
        }
        for pid, entry in PROVIDER_CONFIG.items()
    ]


def get_provider(provider: str) -> dict | None:
    return PROVIDER_CONFIG.get(provider)


def run_step(provider: str, step_id: str, context: dict) -> dict:
    """Execute one step by id. Returns the step's result dict."""
    entry = PROVIDER_CONFIG.get(provider)
    if entry is None:
        return {"status": "error", "message": f"Unknown provider: {provider}"}
    for step in entry["steps"]:
        if step["id"] == step_id:
            try:
                return step["run"](context)
            except Exception as e:
                return {"status": "error", "message": f"Step crashed: {e}"}
    return {"status": "error", "message": f"Unknown step: {step_id}"}

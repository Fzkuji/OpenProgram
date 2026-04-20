"""
OpenAICodexRuntime — thin Runtime subclass that burns ChatGPT subscription.

Reads ~/.codex/auth.json (written by `codex login --device-auth`), refreshes
the OAuth access_token against auth.openai.com/oauth/token when near expiry,
and feeds it to the standard openai-codex-responses provider as `api_key`.

All streaming / tool-loop / exec-tree recording flows through the default
Runtime → AgentSession → provider path. This class only handles OAuth.

Usage:
    from openprogram.providers.openai_codex import OpenAICodexRuntime
    rt = OpenAICodexRuntime(model="gpt-5.4-mini")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from openprogram.agentic_programming.runtime import Runtime


OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _codex_home() -> Path:
    """Honor $CODEX_HOME, default ~/.codex (matches Codex CLI)."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    if not configured:
        return Path.home() / ".codex"
    if configured in ("~", "~/"):
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    return Path(configured).resolve()


def _auth_path() -> Path:
    return _codex_home() / "auth.json"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT: not 3 segments")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _extract_account_id(access_token: str) -> str:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get(JWT_CLAIM_PATH) or {}
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError("JWT has no chatgpt_account_id — re-run `codex login --device-auth`")
    return account_id.strip()


def _jwt_expiry_epoch(access_token: str) -> int | None:
    try:
        exp = _decode_jwt_payload(access_token).get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def _refresh_oauth_token(refresh_token: str, timeout: float = 30.0) -> dict[str, Any]:
    r = httpx.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OAuth refresh failed {r.status_code}: {r.text[:200]}")
    data = r.json()
    for k in ("access_token", "refresh_token", "expires_in"):
        if k not in data:
            raise RuntimeError(f"OAuth refresh response missing {k!r}")
    return data


def _write_auth_json_atomic(data: dict[str, Any]) -> None:
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class _AuthState:
    """Cached auth.json (chatgpt mode) + refresh. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._auth: dict[str, Any] | None = None

    def resolve(self) -> tuple[str, str]:
        """Return (access_token, account_id), refreshing if near expiry."""
        with self._lock:
            if self._auth is None:
                path = _auth_path()
                if not path.exists():
                    raise RuntimeError(
                        f"{path} not found. OpenAICodexRuntime requires the "
                        "ChatGPT subscription. Run: codex login --device-auth"
                    )
                self._auth = json.loads(path.read_text(encoding="utf-8"))

            auth = self._auth
            if auth.get("auth_mode") != "chatgpt":
                raise RuntimeError(
                    f"{_auth_path()} has auth_mode={auth.get('auth_mode')!r}, "
                    "need 'chatgpt'. OpenAICodexRuntime is subscription-only. "
                    "Run: codex login --device-auth"
                )

            tokens = auth.get("tokens") or {}
            access = tokens.get("access_token")
            refresh = tokens.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError(
                    f"{_auth_path()} is in chatgpt mode but missing tokens. "
                    "Run: codex login --device-auth"
                )

            exp = _jwt_expiry_epoch(access)
            if exp is not None and exp - 60 < time.time():
                new_tokens = _refresh_oauth_token(refresh)
                access = new_tokens["access_token"]
                auth["tokens"]["access_token"] = access
                auth["tokens"]["refresh_token"] = new_tokens["refresh_token"]
                if "id_token" in new_tokens:
                    auth["tokens"]["id_token"] = new_tokens["id_token"]
                _write_auth_json_atomic(auth)

            account_id = tokens.get("account_id") or _extract_account_id(access)
            return access, account_id


_KNOWN_CODEX_MODELS = [
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro",
    "gpt-5.3-codex", "gpt-5.3-codex-spark",
    "gpt-5.2-codex", "gpt-5.1-codex", "gpt-5.1-codex-mini",
]


class OpenAICodexRuntime(Runtime):
    """
    Args:
        model:   Default model id (e.g. "gpt-5.4-mini", "gpt-5.4").
        system:  Optional system prompt (forwarded as `instructions`).

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, full_auto, session_id).
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        system: str | None = None,
        **_ignored: Any,
    ) -> None:
        self._auth = _AuthState()
        access, account_id = self._auth.resolve()

        super().__init__(model=f"openai-codex:{model}", api_key=access)

        # ChatGPT backend wants extra headers alongside Bearer. Attach them
        # to a per-runtime Model copy so we don't mutate the registry.
        self.api_model = self.api_model.model_copy(update={
            "headers": {
                "chatgpt-account-id": account_id,
                "originator": "openprogram",
                "OpenAI-Beta": "responses=experimental",
            },
        })

        self.system = system

    def list_models(self) -> list[str]:
        return list(_KNOWN_CODEX_MODELS)

    def exec(self, *args: Any, **kwargs: Any) -> Any:
        # Refresh the access_token (and account header) if close to expiry.
        access, account_id = self._auth.resolve()
        if access != self.api_key:
            self.api_key = access
            new_headers = dict(self.api_model.headers or {})
            new_headers["chatgpt-account-id"] = account_id
            self.api_model = self.api_model.model_copy(update={"headers": new_headers})
        return super().exec(*args, **kwargs)

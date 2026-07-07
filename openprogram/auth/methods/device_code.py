"""OAuth 2.0 Device Authorization Grant (RFC 8628).

Used by providers where the agent runs without a browser — Nous Portal,
GitHub Copilot, OpenAI Codex's device mode, Qwen. Flow:

  1. POST device endpoint → get ``device_code`` + ``user_code`` + verification URI
  2. Show the user_code + URI; they open it on a phone/other device and type the code
  3. Poll the token endpoint with ``grant_type=device_code`` + our ``device_code``
  4. When the user completes the auth on the other device, the poll flips
     from "authorization_pending" to a token response

Polls are cheap-but-rate-limited: providers return ``slow_down`` to ask
us to back off. We honor their interval increase.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from ..types import (
    Credential,
    CredentialData,
    LoginMethod,
    LoginUi,
)


@dataclass
class DeviceCodeConfig:
    """Per-provider device-flow parameters."""

    device_code_url: str             # POST here to begin
    token_url: str                   # POST here to poll
    client_id: str
    scopes: list[str] = field(default_factory=list)
    # Some providers (GitHub) encode scopes comma-separated instead of
    # space-separated; default to space per RFC 8628.
    scope_separator: str = " "
    extra_start_params: dict[str, str] = field(default_factory=dict)
    extra_poll_params: dict[str, str] = field(default_factory=dict)
    # Stop polling after this long. Most device flows have a server-
    # enforced expiry (``expires_in``), but we also enforce client-side
    # so a UI can unstick itself.
    max_poll_seconds: float = 900.0
    # Minimum poll interval floor. Server returns a suggested interval;
    # we use max(server_suggested, floor) to avoid DDoS'ing ourselves.
    min_poll_interval_seconds: float = 2.0


class DeviceCodeMethod(LoginMethod):
    """Device Authorization Grant login.

    Stateless; same instance is re-usable across users.
    """

    method_id = "device_code"

    def __init__(
        self,
        provider_id: str,
        config: DeviceCodeConfig,
        *,
        profile_id: str = "default",
        metadata: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config
        self._profile_id = profile_id
        self._metadata = dict(metadata or {})

    async def run(self, ui: LoginUi) -> Credential:
        import httpx
        cfg = self._cfg

        # Step 1 — begin device flow.
        start_params = {"client_id": cfg.client_id, **cfg.extra_start_params}
        if cfg.scopes:
            start_params["scope"] = cfg.scope_separator.join(cfg.scopes)
        async with httpx.AsyncClient(timeout=30.0) as client:
            start_resp = await client.post(cfg.device_code_url, data=start_params)
        if start_resp.status_code != 200:
            raise RuntimeError(
                f"device-code init failed: {start_resp.status_code} {start_resp.text[:200]}"
            )
        data = start_resp.json()
        for k in ("device_code", "user_code", "verification_uri", "interval", "expires_in"):
            if k not in data:
                # verification_uri_complete is optional; device_code /
                # user_code / interval / expires_in are mandatory.
                if k == "interval" and "interval" not in data:
                    data["interval"] = 5  # RFC default
                    continue
                if k == "expires_in" and "expires_in" not in data:
                    data["expires_in"] = 600
                    continue
                raise RuntimeError(f"device-code response missing {k}: {data}")

        user_code = data["user_code"]
        verification_uri = data.get("verification_uri_complete") or data["verification_uri"]
        interval = max(float(data["interval"]), cfg.min_poll_interval_seconds)
        expires_in = float(data["expires_in"])
        device_code = data["device_code"]

        # Let UI decide: CLI prints "go to <uri> and type <code>"; webui
        # might render a clickable link + big user code.
        await ui.show_code(user_code=user_code, verification_uri=verification_uri)

        # Step 2 — poll until we get tokens or we time out.
        deadline = time.time() + min(expires_in, cfg.max_poll_seconds)
        poll_params = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": cfg.client_id,
            **cfg.extra_poll_params,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            while time.time() < deadline:
                await asyncio.sleep(interval)
                resp = await client.post(cfg.token_url, data=poll_params)
                if resp.status_code == 200:
                    tokens = resp.json()
                    for key in ("access_token", "expires_in"):
                        if key not in tokens:
                            raise RuntimeError(
                                f"device-code token response missing {key}: {tokens}"
                            )
                    expires_at_ms = int(time.time() * 1000) + int(tokens["expires_in"]) * 1000
                    return Credential(
                        provider_id=self.provider_id,
                        profile_id=self._profile_id,
                        kind="device_code",
                        payload=CredentialData(
                            kind="device_code",
                            auth_value=tokens["access_token"],
                            data={
                                "refresh_token": tokens.get("refresh_token", ""),
                                "expires_at_ms": expires_at_ms,
                                "device_code_flow_id": device_code,
                                "extra": {k: v for k, v in tokens.items()
                                          if k not in ("access_token", "refresh_token", "expires_in")},
                            },
                        ),
                        source=f"{self.method_id}:{self.provider_id}",
                        metadata=self._metadata,
                    )
                # Standard device-flow errors come back as HTTP 400 JSON
                # with an ``error`` field. Known: authorization_pending,
                # slow_down, access_denied, expired_token.
                try:
                    err = resp.json().get("error", "")
                except Exception:
                    err = f"HTTP {resp.status_code}"
                if err == "authorization_pending":
                    continue
                if err == "slow_down":
                    # RFC says "increase interval by 5s".
                    interval += 5
                    continue
                if err in ("access_denied", "expired_token"):
                    raise RuntimeError(f"device-code flow aborted: {err}")
                # Unrecognized error — surface rather than loop forever.
                raise RuntimeError(f"device-code poll failed: {resp.text[:200]}")

        raise TimeoutError("device-code flow timed out")

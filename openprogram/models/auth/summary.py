"""Short, secret-free descriptions of credentials for listings and reports.

The CLI, the server's credential routes and :mod:`.doctor` / :mod:`.adoption`
show a credential through these helpers, so a secret is only ever shown as a
masked prefix and suffix.
"""
from __future__ import annotations
import time
from .types import Credential


def mask_secret(secret: str, keep_prefix: int = 6, keep_suffix: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= keep_prefix + keep_suffix + 1:
        return "*" * len(secret)
    return f"{secret[:keep_prefix]}…{secret[-keep_suffix:]}"


def payload_summary(cred: Credential) -> str:
    p = cred.payload
    if p.kind == "api_key":
        return f"api_key {mask_secret(p.auth_value)}"
    if p.kind == "oauth":
        extra = " (+refresh)" if p.data.get("refresh_token") else ""
        expiry = format_expiry(p.data.get("expires_at_ms", 0))
        return f"oauth {mask_secret(p.auth_value)}{extra} exp={expiry}"
    if p.kind == "device_code":
        return f"device_code {mask_secret(p.auth_value)} exp={format_expiry(p.data.get('expires_at_ms', 0))}"
    if p.kind == "cli_delegated":
        return f"cli_delegated → {p.data.get('store_path')}"
    if p.kind == "credential_process":
        return f"credential_process {' '.join(p.data.get('command', []))}"
    return cred.kind


def format_expiry(ms: int) -> str:
    if not ms:
        return "unknown"
    delta = ms - int(time.time() * 1000)
    if delta < 0:
        return f"{format_duration(-delta)} ago (expired)"
    return f"in {format_duration(delta)}"


def format_duration(ms: int) -> str:
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"


__all__ = ["format_duration", "format_expiry", "mask_secret", "payload_summary"]

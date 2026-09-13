"""Credential CLI formatting responsibilities."""
from __future__ import annotations
import time
from ..types import Credential


def _mask(secret: str, keep_prefix: int = 6, keep_suffix: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= keep_prefix + keep_suffix + 1:
        return "*" * len(secret)
    return f"{secret[:keep_prefix]}…{secret[-keep_suffix:]}"



def _payload_summary(cred: Credential) -> str:
    p = cred.payload
    if p.kind == "api_key":
        return f"api_key {_mask(p.auth_value)}"
    if p.kind == "oauth":
        extra = " (+refresh)" if p.data.get("refresh_token") else ""
        expiry = _fmt_expiry(p.data.get("expires_at_ms", 0))
        return f"oauth {_mask(p.auth_value)}{extra} exp={expiry}"
    if p.kind == "device_code":
        return f"device_code {_mask(p.auth_value)} exp={_fmt_expiry(p.data.get('expires_at_ms', 0))}"
    if p.kind == "cli_delegated":
        return f"cli_delegated → {p.data.get('store_path')}"
    if p.kind == "credential_process":
        return f"credential_process {' '.join(p.data.get('command', []))}"
    return cred.kind



def _fmt_expiry(ms: int) -> str:
    if not ms:
        return "unknown"
    delta = ms - int(time.time() * 1000)
    if delta < 0:
        return f"{_fmt_duration(-delta)} ago (expired)"
    return f"in {_fmt_duration(delta)}"



def _fmt_duration(ms: int) -> str:
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"



def _confirm(question: str, *, default: bool) -> bool:
    """Prompt for y/n; Enter accepts ``default``. Returns the bool.

    Accepts y/yes/n/no (case-insensitive). Any other input repeats the
    prompt. EOF/^C propagate — the wizard handles them at the top
    level so each sub-step doesn't have to."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{question} {hint} ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  (answer y or n)")


"""Codex-specific glue for auth v2.

One file connecting three moving parts:

  * :class:`openprogram.models.auth.credential_provider.CredentialProvider` — generic orchestrator
    that wants a ``refresh_fn`` per provider
  * :class:`openprogram.models.auth.sources.CodexCliSource` — discovers the
    existing ``~/.codex/auth.json`` state
  * this module's ``_codex_refresh`` — does the real OAuth call against
    ``auth.openai.com/oauth/token`` and writes the rotated tokens back
    to the Codex CLI's own file, so ``codex login`` / ``codex exec``
    keep working under the user's hand

Why write back to Codex's file at all: a Codex user may run both the
Codex CLI and OpenProgram against the same account. Rotating tokens
without updating ``auth.json`` would break the CLI. We mirror what the
CLI itself does on refresh — same JSON shape, same atomic tmp+rename.

On import, :func:`register_codex_auth` registers the provider config.
Import is idempotent so repeated registration from different modules is
safe; last registration wins (useful for tests that want to swap the
refresh fn).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional


from openprogram.models.auth.credential_provider import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.models.auth.types import (
    AuthNeedsReauthError,
    Credential,
    CredentialData,
)
from openprogram.host._platform.filesystem import replace_file, restrict_descriptor_to_user

_log = logging.getLogger(__name__)


PROVIDER_ID = "openai-codex"

OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


def build_pkce_config():
    """PKCE OAuth config that matches the Codex CLI's own browser flow.

    Mirrors the authorize URL you get from ``codex login`` (which
    OpenClaw and the Codex CLI itself use): callback on
    ``localhost:1455/auth/callback``, ``codex_cli_simplified_flow=true``,
    ``id_token_add_organizations=true`` so the returned JWT carries
    the chatgpt_account_id our runtime needs.
    """
    from openprogram.models.auth.methods.pkce_oauth import PkceConfig
    return PkceConfig(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
        client_id=OAUTH_CLIENT_ID,
        scopes=["openid", "profile", "email", "offline_access"],
        callback_port=1455,
        callback_path="/auth/callback",
        also_prompt_paste=True,
        extra_authorize_params={
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "openprogram",
        },
    )


# ---------------------------------------------------------------------------
# Path resolution (honors $CODEX_HOME exactly like Codex CLI)
# ---------------------------------------------------------------------------

def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if not configured:
        return Path.home() / ".codex"
    if configured in ("~", "~/"):
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    return Path(configured).resolve()


def codex_auth_path() -> Path:
    return _codex_home() / "auth.json"


# ---------------------------------------------------------------------------
# JWT helpers (ChatGPT's access token is a JWT carrying account_id)
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT: not 3 segments")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def extract_account_id(access_token: str) -> str:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get(JWT_CLAIM_PATH) or {}
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError(
            "JWT has no chatgpt_account_id — re-run `codex login --device-auth`"
        )
    return account_id.strip()


def jwt_expiry_epoch_ms(access_token: str) -> Optional[int]:
    try:
        exp = _decode_jwt_payload(access_token).get("exp")
        if isinstance(exp, (int, float)):
            return int(exp) * 1000
    except Exception:
        return None
    return None


def plan_of(*tokens: str) -> Optional[str]:
    """The ChatGPT plan a sign-in's tokens name (``chatgpt_plan_type``:
    ``plus``, ``pro``, ``team`` ...), from the first token that carries it.

    The id token and the access token both hold the claim; the Codex CLI
    reads its plan from the same place. ``None`` when no token says.
    """
    for token in tokens:
        if not token:
            continue
        try:
            auth = _decode_jwt_payload(token).get(JWT_CLAIM_PATH) or {}
        except Exception:
            continue
        plan = auth.get("chatgpt_plan_type") if isinstance(auth, dict) else None
        if isinstance(plan, str) and plan.strip():
            return plan.strip().lower()
    return None


def refresh_token_digest(refresh_token: str) -> str:
    """A refresh token's fingerprint: what is kept to recognise a spent one."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:24]


class SharedLoginSpent(AuthNeedsReauthError):
    """The login in the Codex CLI's file can no longer be refreshed: its
    refresh token was refused (``invalid_grant``, already used, revoked).

    ``spent`` is that refresh token's digest. While the file still holds
    it, asking again can only be refused again."""

    def __init__(self, path: Path, refresh_token: str, detail: str = "") -> None:
        why = f" ({detail})" if detail else ""
        super().__init__(
            f"The ChatGPT sign-in in {path} can no longer be refreshed{why}: "
            "login expired. Sign in again, or run: codex login",
            provider_id=PROVIDER_ID,
        )
        self.spent = refresh_token_digest(refresh_token)


# ---------------------------------------------------------------------------
# Refresh — core of the adapter
# ---------------------------------------------------------------------------
#
# One ChatGPT sign-in is one refresh-token family, and on this machine the
# Codex CLI's ``auth.json`` is where that family lives: the Codex CLI reads
# and refreshes it, a delegated credential (a development instance, or any
# profile that linked the CLI's login) reads it on every call, and an
# OpenProgram ``oauth`` credential for the same account mirrors every
# refresh into it. Whoever spends the refresh token does so holding the
# file's refresh lock, re-reads the file first, and adopts a newer login
# someone else wrote instead of spending a token that is already used.

def _jwt_claim(token: str, claim: str) -> Optional[int]:
    try:
        value = _decode_jwt_payload(token).get(claim)
    except Exception:
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _account_of(access_token: str, hint: Any = None) -> str:
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    try:
        return extract_account_id(access_token)
    except Exception:
        return ""


def token_expiring(access_token: str, skew_seconds: int) -> bool:
    """Whether the token's own ``exp`` is past or within ``skew_seconds``."""
    exp = _jwt_claim(access_token, "exp")
    return exp is not None and time.time() + skew_seconds >= exp


def read_shared_login(path: Optional[Path] = None) -> Optional[dict[str, str]]:
    """The ChatGPT tokens in the Codex CLI's ``auth.json``, or ``None``."""
    path = Path(path) if path else codex_auth_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (data.get("auth_mode") or "chatgpt").lower() != "chatgpt":
        return None
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    if not access or not refresh:
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": tokens.get("id_token") or "",
        "account_id": _account_of(access, tokens.get("account_id")),
    }


def _post_refresh(refresh_token: str) -> dict[str, Any]:
    """Spend ``refresh_token`` at auth.openai.com; the new token set."""
    from openprogram.host.security.safe_http import safe_client

    with safe_client("provider.oauth.fixed") as client:
        response = client.post(
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
            timeout=30.0,
        )
    if response.status_code != 200:
        # 400 invalid_grant when the refresh token was already spent; the
        # caller's permanent-failure check marks the login for sign-in.
        raise RuntimeError(f"OAuth refresh failed {response.status_code}{_refusal_code(response)}")
    data = response.json()
    for k in ("access_token", "refresh_token", "expires_in"):
        if k not in data:
            raise RuntimeError(f"OAuth refresh response missing {k!r}")
    return data


def _refusal_code(response: Any) -> str:
    """`` invalid_grant`` / `` refresh_token_reused``: the code a refused
    refresh names, and nothing else of its body."""
    try:
        body = response.json()
    except Exception:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") or error.get("type") if isinstance(error, dict) else error
    if not isinstance(code, str):
        return ""
    code = "".join(ch for ch in code if ch.isalnum() or ch == "_")[:48]
    return f" {code}" if code else ""


def _payload_from_tokens(
    access: str, refresh: str, id_token: str, expires_at_ms: int, previous: CredentialData,
) -> CredentialData:
    return CredentialData(
        kind="oauth",
        auth_value=access,
        data={
            "refresh_token": refresh,
            "expires_at_ms": expires_at_ms,
            "scope": previous.data.get("scope"),
            "client_id": previous.data.get("client_id") or OAUTH_CLIENT_ID,
            "token_endpoint": OAUTH_TOKEN_URL,
            "id_token": id_token or previous.data.get("id_token"),
            "extra": dict(previous.data.get("extra") or {}),
        },
    )


def _with_payload(cred: Credential, payload: CredentialData) -> Credential:
    return Credential(
        provider_id=cred.provider_id,
        account_id=cred.account_id,
        kind="oauth",
        payload=payload,
        status="valid",
        created_at_ms=cred.created_at_ms,
        updated_at_ms=int(time.time() * 1000),
        source=cred.source,
        metadata=dict(cred.metadata),
        cooldown_until_ms=0,
        last_used_at_ms=cred.last_used_at_ms,
        use_count=cred.use_count,
        last_error=None,
        read_only=False,
        credential_id=cred.credential_id,
    )


def _shared_is_newer(cred: Credential, shared: Optional[dict[str, str]]) -> bool:
    """Whether the Codex file holds a later login of ``cred``'s account."""
    if not shared:
        return False
    ours = cred.payload.auth_value
    theirs = shared["access_token"]
    if not ours or theirs == ours or token_expiring(theirs, 0):
        return False
    account = _account_of(ours, cred.metadata.get("account_id"))
    if not account or account != shared["account_id"]:
        return False
    their_issue = _jwt_claim(theirs, "iat") or _jwt_claim(theirs, "exp")
    our_issue = _jwt_claim(ours, "iat") or _jwt_claim(ours, "exp")
    return their_issue is not None and (our_issue is None or their_issue > our_issue)


def adopt_newer_shared_login(cred: Credential) -> Optional[Credential]:
    """``cred`` carrying the Codex file's newer login of the same account,
    or ``None`` when the file holds nothing newer. Reads only."""
    shared = read_shared_login()
    if not _shared_is_newer(cred, shared):
        return None
    assert shared is not None
    access = shared["access_token"]
    return _with_payload(cred, _payload_from_tokens(
        access, shared["refresh_token"], shared["id_token"],
        jwt_expiry_epoch_ms(access) or 0, cred.payload,
    ))


def _codex_refresh(cred: Credential) -> Credential:
    """Synchronous refresh — called by CredentialProvider via executor.

    Under the Codex file's refresh lock: adopt a newer login of the same
    account another holder already wrote there; otherwise spend this
    credential's refresh token and mirror the result into the file so the
    Codex CLI and delegated readers share it. Returns a fresh
    :class:`Credential` with the same ``credential_id``.
    """
    from openprogram.models.auth import refresh_lock

    payload = cred.payload
    if payload.kind != "oauth":
        raise RuntimeError(
            f"codex refresh called with non-OAuth payload kind: {payload.kind!r}"
        )
    refresh_token = payload.data.get("refresh_token", "")
    if not refresh_token:
        raise RuntimeError("codex credential has no refresh_token")

    path = codex_auth_path()
    with refresh_lock.held(path):
        adopted = adopt_newer_shared_login(cred)
        if adopted is not None:
            _log.info("openai-codex: adopted the newer ChatGPT sign-in from %s", path)
            return adopted
        data = _post_refresh(refresh_token)
        new_payload = _payload_from_tokens(
            data["access_token"], data["refresh_token"], data.get("id_token") or "",
            int(time.time() * 1000) + int(data["expires_in"]) * 1000, payload,
        )
        account = _account_of(payload.auth_value, cred.metadata.get("account_id"))
        _write_back_to_codex_file(new_payload, account=account)
    _log.info("openai-codex: refreshed the ChatGPT sign-in")
    return _with_payload(cred, new_payload)


def refresh_shared_login(path: Optional[Path], rejected: str) -> str:
    """Refresh the login in the Codex CLI's ``auth.json`` itself, as the
    Codex CLI does on 401 or near expiry, and return its access token.

    For a delegated credential, which owns no copy: the file is the only
    store, so the refreshed tokens are written back to it. A login someone
    else refreshed while we waited for the lock is returned as is.

    Raises :class:`SharedLoginSpent` when the token endpoint refuses the
    file's refresh token and the file holds no newer login.
    """
    from openprogram.models.auth import refresh_lock
    from openprogram.models.auth.types import AuthConfigError

    path = Path(path) if path else codex_auth_path()
    with refresh_lock.held(path):
        shared = read_shared_login(path)
        if shared is None:
            raise AuthConfigError(
                f"{path} holds no ChatGPT OAuth login. Run: codex login --device-auth",
                provider_id=PROVIDER_ID,
            )
        current = shared["access_token"]
        if current != rejected and not token_expiring(current, 0):
            return current
        try:
            data = _post_refresh(shared["refresh_token"])
        except Exception as exc:
            from openprogram.models.auth.credential_provider import _is_permanent_refresh_failure
            if not _is_permanent_refresh_failure(exc):
                raise
            # The Codex CLI does not take this lock: it may have spent the
            # token itself and written its newer login in the meantime.
            latest = read_shared_login(path)
            if latest and latest["refresh_token"] != shared["refresh_token"] \
                    and latest["access_token"] != rejected \
                    and not token_expiring(latest["access_token"], 0):
                return latest["access_token"]
            raise SharedLoginSpent(path, shared["refresh_token"], str(exc)[:120]) from exc
        new_payload = CredentialData(
            kind="oauth",
            auth_value=data["access_token"],
            data={
                "refresh_token": data["refresh_token"],
                "id_token": data.get("id_token") or shared["id_token"],
            },
        )
        _write_back_to_codex_file(new_payload, path=path)
    _log.info("openai-codex: refreshed the ChatGPT sign-in in %s", path)
    return data["access_token"]


def _write_back_to_codex_file(
    payload: CredentialData, *, path: Optional[Path] = None, account: str = "",
) -> None:
    """Keep ``~/.codex/auth.json`` in sync after we rotate.

    The file is left alone when it holds a different ChatGPT account (the
    Codex CLI signed in elsewhere). Non-fatal on failure: if the file is
    gone or permission-denied, we still have the new tokens in our own
    store; the Codex CLI will just refresh independently next time.
    """
    path = Path(path) if path else codex_auth_path()
    try:
        existing = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
    except (OSError, json.JSONDecodeError):
        existing = {}
    tokens = existing.get("tokens") or {}
    if account and tokens.get("access_token"):
        theirs = _account_of(tokens["access_token"], tokens.get("account_id"))
        if theirs and theirs != account:
            return
    existing.setdefault("auth_mode", "chatgpt")
    tokens = existing.setdefault("tokens", {})
    tokens["access_token"] = payload.auth_value
    tokens["refresh_token"] = payload.data.get("refresh_token", "")
    if payload.data.get("id_token"):
        tokens["id_token"] = payload.data.get("id_token")
    # Codex's own file doesn't store expires_at_ms — the CLI decodes the
    # JWT at read time. We leave it out for shape-compatibility.
    existing["last_refresh"] = _iso_now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        # Owner-only from the first byte, and on disk before it replaces
        # the file: the file holds the only copy of a rotated refresh
        # token for a delegated reader, so a crash must not leave it empty.
        fd = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600,
        )
        try:
            restrict_descriptor_to_user(fd)
            os.write(fd, (json.dumps(existing, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        replace_file(tmp, path)
    except OSError as exc:
        # The tokens were spent; whoever holds only this file must sign in
        # again if it was not updated, so say so (path and error only).
        _log.warning("could not save the refreshed ChatGPT sign-in to %s: %s",
                     path, type(exc).__name__)


def _iso_now() -> str:
    # Codex writes RFC3339-ish timestamps with ms precision. Match the shape.
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
# Importing from Codex CLI's file
# ---------------------------------------------------------------------------

def import_from_codex_file(
    *,
    account_id: str = "default",
    auth_path: Optional[Path] = None,
) -> Optional[Credential]:
    """Read the Codex CLI's auth.json, produce an ``oauth`` credential.

    Returns ``None`` if the file is absent or unusable — callers decide
    whether that's a failure. Not to be confused with
    :class:`CodexCliSource` which produces a *delegated* (read-only)
    credential; this one produces a writable one so CredentialProvider can
    rotate it.
    """
    path = Path(auth_path) if auth_path else codex_auth_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Codex CLI stores two shapes:
    #   1. ChatGPT OAuth  → {"auth_mode": "chatgpt", "tokens": {...}}
    #   2. Bare API key   → {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-..."}
    #
    # Only the chatgpt shape drives the Responses backend this runtime
    # needs. The apikey shape has no chatgpt_account, no refresh path,
    # and semantically means "use OpenAI directly" — which belongs to
    # the `openai` provider, not here. We refuse to import it rather
    # than silently routing it to a different pool: the user asked to
    # import Codex credentials, and an apikey-mode file isn't one. If
    # they want OAuth, they need to re-run `codex login` without
    # --api-key. Matches OpenClaw's codex-cli-auth handling.
    #
    # Pre-2026 Codex CLI builds didn't write ``auth_mode`` at all —
    # they relied entirely on which keys were populated. Infer the
    # shape so older files don't silently fall through: if the
    # discriminator is missing but ``tokens.access_token`` is set
    # *and* the apikey slot is empty, treat as chatgpt mode.
    auth_mode = (data.get("auth_mode") or "").lower()
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not auth_mode:
        if access and not (data.get("OPENAI_API_KEY") or "").strip():
            auth_mode = "chatgpt"
    if auth_mode != "chatgpt":
        return None

    if not access or not refresh:
        return None

    # Codex stores expiry inside the JWT, not as a separate field.
    expires_at_ms = jwt_expiry_epoch_ms(access) or 0
    metadata: dict[str, Any] = {"imported_from": "codex_cli", "source_path": str(path)}
    if tokens.get("account_id"):
        metadata["account_id"] = tokens["account_id"]
    else:
        try:
            metadata["account_id"] = extract_account_id(access)
        except RuntimeError:
            pass

    return Credential(
        provider_id=PROVIDER_ID,
        account_id=account_id,
        kind="oauth",
        payload=CredentialData(
            kind="oauth",
            auth_value=access,
            data={
                "refresh_token": refresh,
                "expires_at_ms": expires_at_ms,
                "client_id": OAUTH_CLIENT_ID,
                "token_endpoint": OAUTH_TOKEN_URL,
                "id_token": tokens.get("id_token", ""),
            },
        ),
        source="codex_cli_import",
        metadata=metadata,
        read_only=False,
    )


# ---------------------------------------------------------------------------
# Registration (idempotent)
# ---------------------------------------------------------------------------

_REGISTERED = False


def _reread_login() -> None:
    from .credentials import reread_login
    reread_login()


def _login_plan() -> Optional[str]:
    from .credentials import login_plan
    return login_plan()


def register_codex_auth() -> None:
    """Register the Codex provider config with :mod:`auth.credential_provider`.

    Called at module import. Idempotent. Tests that need to swap the
    refresh function can call :func:`openprogram.models.auth.credential_provider.register_provider_config`
    directly — last registration wins.
    """
    global _REGISTERED
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=60,
            refresh=_codex_refresh,
            async_refresh=None,
            reread_login=_reread_login,
            plan=_login_plan,
        )
    )
    _REGISTERED = True


# Register on import — other modules don't have to remember to call us.
register_codex_auth()


__all__ = [
    "PROVIDER_ID",
    "codex_auth_path",
    "extract_account_id",
    "jwt_expiry_epoch_ms",
    "adopt_newer_shared_login",
    "import_from_codex_file",
    "plan_of",
    "read_shared_login",
    "refresh_shared_login",
    "refresh_token_digest",
    "SharedLoginSpent",
    "token_expiring",
    "register_codex_auth",
]

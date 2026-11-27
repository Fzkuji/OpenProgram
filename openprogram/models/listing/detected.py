"""What Add provider offers before anything is typed.

``GET /api/providers/detected`` answers with three lists:

  * ``env`` — provider keys already set in the Worker's environment, one row
    per variable, matched to the catalogue by the provider's key names
    (the static table, then models.dev's ``env`` list), the way OpenCode
    recognises keys from its catalogue. Only the name and a masked tail
    leave the process; each one is already a read-only credential row of its
    provider (``models/auth/store.py`` ``env_rows``), never copied.
  * ``local`` — local servers (Ollama, LM Studio, vLLM, llama.cpp) that
    answer ``GET /models`` on their configured loopback address within a
    short timeout, without a key.
  * ``popular`` — :data:`catalog.consoles.POPULAR` with each provider's
    name, how it connects and where to get a key.

Nothing here sends a request with a key.
"""
from __future__ import annotations

import contextlib

import os
from typing import Literal, Optional, TypedDict
from urllib.parse import urlsplit

AuthKind = Literal["api_key", "sign_in", "local", "cloud"]

#: How long a local server has to answer.
LOCAL_CONNECT_TIMEOUT_S = 0.5
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_CLOUD = frozenset({"amazon-bedrock", "google-vertex", "azure-openai-responses"})


class ProviderChoice(TypedDict):
    id: str
    label: str
    auth: AuthKind
    #: The provider's page for creating a key (``catalog/consoles.py``), or null.
    console_url: Optional[str]
    #: Already has a stored key or sign-in.
    connected: bool
    #: A matching key found in the environment (its variable name), or null.
    env_var: Optional[str]


class PopularProvider(ProviderChoice):
    variants: list[ProviderChoice]


class DetectedEnvKey(TypedDict):
    env_var: str
    #: The provider ``Use`` connects (the most popular candidate).
    provider: str
    label: str
    #: Every catalogue provider that reads this variable, ``provider`` first.
    providers: list[str]
    masked: str
    connected: bool


class DetectedLocal(TypedDict):
    id: str
    label: str
    base_url: str
    running: bool
    enabled: bool


class DetectedPayload(TypedDict):
    env: list[DetectedEnvKey]
    local: list[DetectedLocal]
    popular: list[PopularProvider]
    #: How many providers the catalogue offers (for "Search N providers").
    catalogue_count: int


def auth_kind(provider_id: str) -> AuthKind:
    """How a provider connects: a pasted key, a sign-in, a local server or a cloud chain."""
    from openprogram.models.providers.catalog.local import is_local_provider
    if is_local_provider(provider_id):
        return "local"
    if provider_id in _CLOUD:
        return "cloud"
    try:
        from openprogram.models.auth.login.login_method_registry import login_methods
        methods = [mid for mid, _label in login_methods(provider_id)]
    except Exception:
        methods = []
    if methods and "api_key" not in methods:
        return "sign_in"
    return "api_key"


def key_env_names(provider_id: str) -> list[str]:
    """The environment variables that hold a pasteable key for a provider (never OAuth tokens)."""
    if auth_kind(provider_id) != "api_key":
        return []
    try:
        from openprogram.models.auth.env_api_keys import env_vars_for
        names = env_vars_for(provider_id)
    except Exception:
        names = []
    return [name for name in names if name and "OAUTH" not in name.upper()]


def env_value(name: str) -> str:
    """A variable's key, without a pasted ``Bearer`` prefix; ``""`` when unset."""
    value = (os.environ.get(name) or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


def _catalogue_ids() -> list[str]:
    ids: list[str] = []
    with contextlib.suppress(Exception):
        from openprogram.models.providers.catalog.metadata import _shipped_provider_ids
        ids += sorted(_shipped_provider_ids())
    with contextlib.suppress(Exception):
        from openprogram.models.providers.catalog.sources import models_dev
        ids += sorted(p["id"] for p in models_dev.list_providers() if p.get("id"))
    try:
        from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    except Exception:
        UNSUPPORTED_PROVIDERS = frozenset()
    seen: set[str] = set()
    out = []
    for pid in ids:
        if pid not in seen and pid not in UNSUPPORTED_PROVIDERS:
            seen.add(pid)
            out.append(pid)
    return out


def _rank() -> dict[str, int]:
    """Popular providers first, then their variants, in the table's order."""
    from openprogram.models.providers.catalog.consoles import POPULAR, variants
    order = list(POPULAR) + [v for pid in POPULAR for v in variants(pid)]
    return {pid: index for index, pid in enumerate(order)}


def _stored_providers() -> set[str]:
    try:
        from openprogram.models.auth.provider_policy import is_withdrawn
        from openprogram.models.auth.store import get_store
        return {pool.provider_id for pool in get_store().list_pools()
                if pool.credentials and not is_withdrawn(pool.provider_id)}
    except Exception:
        return set()


def _label(provider_id: str) -> str:
    try:
        from openprogram.models.providers.catalog.metadata import label_for
        return label_for(provider_id)
    except Exception:
        return provider_id


def env_keys(provider_ids: Optional[list[str]] = None, *, stored: Optional[set[str]] = None) -> list[DetectedEnvKey]:
    """One row per set variable that a catalogue provider reads as its key."""
    from openprogram.host.security.credential_mask import mask_credential
    from openprogram.models.auth.env_api_keys import provider_id_for_env_var
    ids = _catalogue_ids() if provider_ids is None else provider_ids
    stored = _stored_providers() if stored is None else stored
    readers: dict[str, list[str]] = {}
    for pid in ids:
        for name in key_env_names(pid):
            if env_value(name):
                readers.setdefault(name, []).append(pid)
    rank = _rank()
    out: list[DetectedEnvKey] = []
    for name, pids in readers.items():
        owner = provider_id_for_env_var(name)
        pids.sort(key=lambda p: (rank.get(p, len(rank) + (0 if p == owner else 1)), p))
        first = pids[0]
        out.append({
            "env_var": name, "provider": first, "label": _label(first), "providers": pids,
            "masked": mask_credential(env_value(name)), "connected": first in stored,
        })
    out.sort(key=lambda row: (rank.get(row["provider"], len(rank)), row["env_var"]))
    return out


def local_base_url(provider_id: str) -> str:
    try:
        from openprogram.models.providers.settings.storage import _resolve_base_url
        base = _resolve_base_url(provider_id)
    except Exception:
        base = None
    if base:
        return base
    from openprogram.models.providers.catalog.local import LOCAL_PROVIDERS
    port = LOCAL_PROVIDERS.get(provider_id, ("", 8000))[1]
    return f"http://127.0.0.1:{port}/v1"


#: base URL -> (probed at, running); every probe is remembered for ``max_age`` readers.
_LOCAL_SEEN: dict[str, tuple[float, bool]] = {}


def local_server_running(provider_id: str, base_url: Optional[str] = None, *,
                         max_age: float = 0.0) -> Optional[bool]:
    """Whether the local server answers its model list at all (any HTTP status);
    ``None`` for an address off this machine, which is not probed.

    The request goes through the same managed client and owner exception as
    the key check's ``GET /models`` (``credentials._http_get``), without a key.
    With ``max_age`` a probe younger than that many seconds is reused, so a
    reader that runs on every picker open (``provider_views.picker_state``)
    probes a local server at most once per ``max_age``.
    """
    import time
    base = base_url or local_base_url(provider_id)
    host = (urlsplit(base).hostname or "").lower()
    if host not in _LOOPBACK:
        return None
    seen = _LOCAL_SEEN.get(base)
    if max_age > 0 and seen is not None and time.time() - seen[0] < max_age:
        return seen[1]
    from .credentials import _http_get
    answer = _http_get(base.rstrip("/") + "/models", timeout=LOCAL_CONNECT_TIMEOUT_S, configured_url=base)
    _LOCAL_SEEN[base] = (time.time(), answer is not None)
    return answer is not None


def local_servers() -> list[DetectedLocal]:
    from openprogram.models.providers.catalog.consoles import LOCAL_OFFERED
    try:
        from openprogram.models.providers.settings.storage import _read_providers_cfg
        cfg = _read_providers_cfg()
    except Exception:
        cfg = {}
    out: list[DetectedLocal] = []
    for pid in LOCAL_OFFERED:
        base = local_base_url(pid)
        out.append({"id": pid, "label": _label(pid), "base_url": base,
                    "running": bool(local_server_running(pid, base)),
                    "enabled": bool((cfg.get(pid) or {}).get("enabled"))})
    return out


def _choice(pid: str, stored: set[str], env_by_provider: dict[str, str]) -> ProviderChoice:
    from openprogram.models.providers.catalog.consoles import console_url
    return {"id": pid, "label": _label(pid), "auth": auth_kind(pid), "console_url": console_url(pid, "keys"),
            "connected": pid in stored, "env_var": env_by_provider.get(pid)}


def popular(*, stored: Optional[set[str]] = None, env: Optional[list[DetectedEnvKey]] = None) -> list[PopularProvider]:
    from openprogram.models.providers.catalog.consoles import POPULAR, variants
    stored = _stored_providers() if stored is None else stored
    env_by_provider: dict[str, str] = {}
    for row in env or []:
        for pid in row["providers"]:
            env_by_provider.setdefault(pid, row["env_var"])
    out: list[PopularProvider] = []
    for pid in POPULAR:
        row: PopularProvider = {**_choice(pid, stored, env_by_provider),  # type: ignore[typeddict-item]
                                "variants": [_choice(v, stored, env_by_provider) for v in variants(pid)]}
        out.append(row)
    return out


def detected() -> DetectedPayload:
    stored = _stored_providers()
    ids = _catalogue_ids()
    env = env_keys(ids, stored=stored)
    return {"env": env, "local": local_servers(), "popular": popular(stored=stored, env=env),
            "catalogue_count": len(ids)}


__all__ = [
    "AuthKind",
    "DetectedEnvKey",
    "DetectedLocal",
    "DetectedPayload",
    "PopularProvider",
    "ProviderChoice",
    "auth_kind",
    "detected",
    "env_keys",
    "env_value",
    "key_env_names",
    "local_base_url",
    "local_server_running",
    "local_servers",
    "popular",
]

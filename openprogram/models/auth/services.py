"""The services tools reach with a key, and which company's keys they read.

A key is stored once, for the company that issued it (``store.company_keys``);
where it can be used is the services' own business. A tool asks for its
service's key here and never reads the process environment for it: the
company's stored keys first, then the variables the service (or one of the
company's ways) names, read where they are set and never written.

A company that also serves models shares its keys with its search: the
MiniMax search reads MiniMax's keys, Kimi's search Moonshot AI's, so a key
added on the provider card works for the search too, and the other way round.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Service:
    #: The tool service (a web search backend, an image or speech service).
    id: str
    #: The company whose keys it reads.
    company: str
    #: The variables that hold its key, first preferred.
    env_vars: tuple[str, ...]


SERVICES: tuple[Service, ...] = (
    Service("tavily", "tavily", ("TAVILY_API_KEY",)),
    Service("brave", "brave", ("BRAVE_API_KEY",)),
    Service("serper", "serper", ("SERPER_API_KEY",)),
    Service("exa", "exa", ("EXA_API_KEY",)),
    Service("youcom", "youcom", ("YDC_API_KEY", "YOU_API_KEY")),
    Service("jina", "jina", ("JINA_API_KEY",)),
    Service("kagi", "kagi", ("KAGI_API_KEY",)),
    Service("firecrawl", "firecrawl", ("FIRECRAWL_API_KEY",)),
    Service("google-pse", "google-pse", ("GOOGLE_PSE_API_KEY",)),
    Service("fal", "fal", ("FAL_KEY",)),
    Service("elevenlabs", "elevenlabs", ("ELEVENLABS_API_KEY",)),
    Service("playht", "playht", ("PLAYHT_API_KEY",)),
    # Companies that also serve models.
    Service("perplexity-search", "perplexity", ("PERPLEXITY_API_KEY",)),
    Service("minimax-search", "minimax", ("MINIMAX_CODE_PLAN_KEY", "MINIMAX_CODING_API_KEY", "MINIMAX_API_KEY")),
    Service("moonshot-search", "moonshotai", ("KIMI_API_KEY", "MOONSHOT_API_KEY")),
    Service("ollama-search", "ollama", ("OLLAMA_API_KEY",)),
)

_BY_ID = {s.id: s for s in SERVICES}

#: Values a service needs beside its key that are no secret: the variable
#: that once held each, and where ``config.json`` keeps it now.
SETTINGS: dict[str, tuple[str, str]] = {
    "GOOGLE_PSE_CX": ("search", "google_pse_cx"),  # the search engine's id
}


def setting(name: str) -> str:
    """The value of the service setting variable ``name`` names:
    ``config.json``'s, else the variable where it is set."""
    section, key = SETTINGS[name]
    try:
        from openprogram.state.config import read_config
        value = ((read_config().get(section) or {}).get(key) or "").strip()
    except Exception:
        value = ""
    return value or (os.environ.get(name) or "").strip()


def save_setting(name: str, value: str) -> None:
    section, key = SETTINGS[name]
    from openprogram.state.config import update_config

    def _set(cfg: dict) -> None:
        part = dict(cfg.get(section) or {})
        if value.strip():
            part[key] = value.strip()
        else:
            part.pop(key, None)
        cfg[section] = part

    update_config(_set)

#: Companies that are only a tool service: their keys are no provider's.
SERVICE_ONLY: frozenset[str] = frozenset(s.company for s in SERVICES if s.company == s.id)


def service(service_id: str) -> Service:
    return _BY_ID[service_id]


def service_for_env_var(name: str) -> Optional[Service]:
    """The service a variable holds the key of, if a service names it."""
    return next((s for s in SERVICES if name in s.env_vars), None)


def company_for_env_var(name: str) -> Optional[str]:
    """The company whose key variable ``name`` holds: a service's, else the
    company of the provider that reads it (its connection entry, else the
    catalogue)."""
    found = service_for_env_var(name)
    if found is not None:
        return found.company
    from openprogram.models.auth.login.login_method_registry import provider_for_env_var
    provider = provider_for_env_var(name)
    if not provider:
        return None
    from .company_keys import company_for
    return company_for(provider)


def _company_env_vars(company: str) -> list[str]:
    """The key variables the company's ways read."""
    from openprogram.models.auth.login.login_method_registry import companies, env_vars
    for c in companies():
        if c.id == company:
            out: list[str] = []
            for way in c.ways:
                out.extend(n for n in env_vars(way.provider) if n not in out and "OAUTH" not in n.upper())
            return out
    return []


def _first_usable(company: str, keys: list[dict]) -> str:
    """The first of ``keys``, in their stored order, that some way of the
    company has not refused (all of them refused: the first, so the service
    says what is wrong with it)."""
    values = [(k["id"], (k.get("value") or "").strip()) for k in keys if (k.get("value") or "").strip()]
    if len(values) > 1 and company not in SERVICE_ONLY:
        from .company_keys import refused_everywhere
        for key_id, value in values:
            try:
                if not refused_everywhere(company, key_id):
                    return value
            except Exception:
                return value
    return values[0][1] if values else ""


def service_key(service_id: str) -> str:
    """The key ``service_id`` sends now, or ``""``: the company's first
    stored key, else the first variable set among the service's own and its
    company's ways'."""
    svc = _BY_ID[service_id]
    from .store import get_store
    try:
        keys = get_store().company_keys(svc.company)
    except Exception:
        keys = []
    value = _first_usable(svc.company, keys)
    if value:
        return value
    for name in svc.env_vars:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    if svc.company in SERVICE_ONLY:
        return ""  # no way of a company reads its key: the catalogue is not asked
    for name in _company_env_vars(svc.company):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def key_for_env_var(name: str) -> str:
    """The key a variable name stands for: its company's stored key (a
    service's, or a provider's through the request path), else the variable.
    A service setting's name gives the setting."""
    if name in SETTINGS:
        return setting(name)
    found = service_for_env_var(name)
    if found is not None:
        return service_key(found.id)
    from openprogram.models.auth.login.login_method_registry import provider_for_env_var
    provider = provider_for_env_var(name)
    if provider is not None:
        from openprogram.models.providers.settings.env_api_keys import resolve_provider_key
        try:
            value = resolve_provider_key(provider)
        except Exception:
            value = None
        if value and value != "<authenticated>":
            return value
    company = company_for_env_var(name)
    if company:
        from .store import get_store
        value = _first_usable(company, get_store().company_keys(company))
        if value:
            return value
    return (os.environ.get(name) or "").strip()


def stored_key(name: str) -> str:
    """The key stored for the company variable ``name`` names (not the
    variable itself), or ``""``."""
    if name in SETTINGS:
        section, key = SETTINGS[name]
        from openprogram.state.config import read_config
        return ((read_config().get(section) or {}).get(key) or "").strip()
    company = company_for_env_var(name)
    if not company:
        return ""
    from .store import get_store
    return _first_usable(company, get_store().company_keys(company))


def save_key(name: str, value: str, *, source: str = "web") -> Optional[str]:
    """Store ``value`` for the company variable ``name`` names; returns the
    key's id, ``None`` when no company reads ``name``. A tool service's key
    is replaced; a company that serves models gets one more key, placed
    first. A service setting's name saves the setting."""
    if name in SETTINGS:
        save_setting(name, value)
        return name
    company = company_for_env_var(name)
    if not company:
        return None
    from .store import get_store
    store = get_store()
    if company in SERVICE_ONLY and store.company_keys(company):
        # A tool service has one key: saving it again replaces it.
        import time
        box: dict = {}

        def _replace(keys: list[dict]):
            keys[0]["value"] = value.strip()
            keys[0]["updated_at_ms"] = int(time.time() * 1000)
            box["id"] = keys[0]["id"]
            return keys[0]["id"]

        store.update_company_keys(company, _replace)
        store._drop_key_generations(company)
        return box["id"]
    # A company that also serves models keeps its other keys; the one saved
    # for a tool goes first, since the tools read the first.
    return store.add_company_key(company, value, source=source, first=True)


def remove_key(name: str) -> bool:
    """Remove the stored key ``name`` stands for (the company's first)."""
    if name in SETTINGS:
        had = bool(stored_key(name))
        save_setting(name, "")
        return had
    company = company_for_env_var(name)
    if not company:
        return False
    from .store import get_store
    store = get_store()
    keys = store.company_keys(company)
    return bool(keys) and store.remove_company_key(company, keys[0]["id"])


def checked_way(name: str) -> Optional[str]:
    """The provider a key saved under ``name`` can be checked on before it
    is stored: only a company with one way that takes a key. A key of a
    company with several ways is not any one way's, so no single way's
    refusal may stop it."""
    from openprogram.models.auth.login.login_method_registry import provider_for_env_var
    from .company_keys import company_for, key_ways
    if service_for_env_var(name) is not None and service_for_env_var(name).company == service_for_env_var(name).id:
        return None
    provider = provider_for_env_var(name)
    if not provider:
        return None
    ways = key_ways(company_for(provider))
    return provider if ways == [provider] else None


__all__ = ["SERVICES", "SERVICE_ONLY", "SETTINGS", "Service", "save_setting", "setting", "company_for_env_var", "key_for_env_var",
           "checked_way", "remove_key", "save_key", "service", "service_for_env_var", "service_key", "stored_key"]

"""A company's API keys: each key stored once, for the company that issued it.

Where a key can be used is not known when it is saved, and is not decided
then: every way of the company that takes a key sees every one of its keys
(MiniMax's four ways all list a MiniMax key), and a request sends it to the
address of the way it goes through. A way the key does not work on says so
when it is used, on that way's row (its status, cooldown and blocked models
are the way's own, kept in the way's ``credentials.json`` beside its
sign-ins). The secret itself lives only here:

    <state>/auth/_keys/<company>.json   {"v": 1, "keys": [{id, label, value,
                                          source, created_at_ms, updated_at_ms}]}

A provider that is no company's way is a company of its own; a tool service
(``services.py``) is one too, or reads a company's keys.
"""
from __future__ import annotations

import hmac
import json
import time
from pathlib import Path
from typing import Callable, Optional

KEYS_DIR = "_keys"
#: The metadata mark of a way's row that is a company key (its secret is here).
COMPANY_MARK = "company_key"


def company_for(provider_id: str) -> str:
    """The company whose keys ``provider_id`` sees."""
    from openprogram.models.auth.login.login_method_registry import company_of
    found = company_of(provider_id)
    if found is not None:
        return found[0].id
    from .services import _BY_ID
    svc = _BY_ID.get(provider_id)
    return svc.company if svc is not None else provider_id


def takes_company_keys(provider_id: str) -> bool:
    """A way that takes an API key sees its company's keys."""
    from openprogram.models.auth.login.login_method_registry import accepts_api_key
    try:
        return accepts_api_key(provider_id)
    except Exception:
        return False


def key_ways(company: str) -> list[str]:
    """The providers that see ``company``'s keys."""
    from openprogram.models.auth.login.login_method_registry import companies
    from .services import SERVICE_ONLY
    for c in companies():
        if c.id == company:
            return [w.provider for w in c.ways if takes_company_keys(w.provider)]
    if company in SERVICE_ONLY:
        return []
    return [company] if takes_company_keys(company) else []


def ways_for_refused_keys(provider_id: str, model_id: str = "",
                          exclude: frozenset = frozenset()) -> list[tuple[str, frozenset]]:
    """The other ways of ``provider_id``'s company a request can go through
    when every key is refused here: those that list a key refused on
    ``provider_id`` and have not refused it themselves, a way the key has
    answered on before any it has not been tried on, each with the ids of
    those keys: the request sent there uses them and no other credential of
    that way. Only a refusal moves a request (never a limit or a spent
    quota), and only along a key: it is the same key on the other way, so
    the same account pays."""
    from .selection import usable
    from .store import get_store
    store = get_store()
    refused = {c.id for c in store.get(provider_id).credentials
               if c.kind == "api_key" and c.status == "invalid_key"
               and (c.metadata.get(COMPANY_MARK) or c.source == "env")}
    if not refused:
        return []
    found: list[tuple[int, int, str, frozenset]] = []
    for place, way in enumerate(key_ways(company_for(provider_id))):
        if way == provider_id or way in exclude:
            continue
        rows = [r for r in store.get(way).credentials if r.id in refused and usable(r, model_id)]
        if rows:
            found.append((0 if any(r.use_count for r in rows) else 1, place, way, frozenset(r.id for r in rows)))
    return [(way, ids) for _, _, way, ids in sorted(found, key=lambda f: f[:3])]


def refused_everywhere(company: str, key_id: str) -> bool:
    """Every way of ``company`` has refused this key (``False`` for a
    company with no way: nothing has said anything about its keys)."""
    from .store import get_store
    store = get_store()
    ways = key_ways(company)
    if not ways:
        return False
    for way in ways:
        row = store.get(way).find(key_id)
        if row is None or row.status != "invalid_key":
            return False
    return True


def keys_path(base_dir: Path, company: str) -> Path:
    return base_dir / KEYS_DIR / f"{company}.json"


def parse(raw: Optional[bytes], path: Path) -> list[dict]:
    """The keys in a keys file (an unreadable one through its last good copy)."""
    if raw is None:
        return []
    from openprogram.state.last_good import load_json
    doc = load_json(path, raw)
    keys = doc.get("keys") if isinstance(doc, dict) else None
    return [k for k in keys if isinstance(k, dict) and k.get("id") and isinstance(k.get("value"), str)] \
        if isinstance(keys, list) else []


def dump(keys: list[dict]) -> bytes:
    return json.dumps({"v": 1, "keys": keys}, indent=2, ensure_ascii=False).encode("utf-8")


def same_value(entry: dict, value: str) -> bool:
    return hmac.compare_digest((entry.get("value") or "").strip().encode(), value.strip().encode())


def new_entry(key_id: str, value: str, *, label: str = "", source: str = "web") -> dict:
    now = int(time.time() * 1000)
    return {"id": key_id, "label": label, "value": value.strip(), "source": source,
            "created_at_ms": now, "updated_at_ms": now}


Updater = Callable[[list[dict]], Optional[list[dict]]]

__all__ = ["COMPANY_MARK", "KEYS_DIR", "company_for", "dump", "key_ways", "keys_path", "new_entry",
           "parse", "refused_everywhere", "same_value", "takes_company_keys", "ways_for_refused_keys"]

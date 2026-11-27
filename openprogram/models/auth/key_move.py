"""Move every API key to the company that issued it, once per key.

Two places held keys before the company files (``company_keys.py``):

* a way's own list (``auth/<way>/credentials.json``): a key pasted on one
  way of a company was that way's only, and the same key pasted on another
  way was a second copy;
* ``config.json``'s ``api_keys``, which the server once copied into its
  process environment for the web search and speech tools (and which held
  model keys too).

A key in a way's list moves to its company's file and the way keeps its row
(place and state) without the secret; the same key on several ways becomes
one key, its rows on each way keeping their state. A key in ``config.json``
moves to the company its variable names (``services.company_for_env_var``),
unless it is already stored anywhere; the moved names leave ``api_keys``.
A name that is a service's setting and no secret (Google PSE's engine id)
moves to that setting. A key the store cannot place (a variable no company
names) stays where it is.

Both run at every start and do nothing once nothing is left to move.
"""
from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)


def move_keys_to_companies(store) -> None:
    _move_way_keys(store)
    _move_config_keys(store)


def _eligible(row: dict) -> bool:
    payload = row.get("payload") or {}
    return (row.get("kind") == "api_key" and row.get("source") != "env" and not row.get("read_only")
            and bool((payload.get("auth_value") or "").strip())
            and not payload.get("base_url") and not payload.get("headers")
            and not (row.get("metadata") or {}).get("company_key"))


def _move_way_keys(store) -> None:
    from openprogram.state.private_files import _private_atomic_update
    from .company_keys import COMPANY_MARK, company_for, takes_company_keys
    from .store import FILE_LOCK_TIMEOUT_SECONDS, FILE_NAME

    base = store.base_dir()
    if not base.is_dir():
        return
    for folder in sorted(base.iterdir()):
        path = folder / FILE_NAME
        if not folder.is_dir() or folder.name.startswith("_") or not path.exists():
            continue
        provider = folder.name
        if not takes_company_keys(provider):
            continue
        try:
            doc = json.loads(path.read_bytes())
        except Exception:
            continue
        if not any(_eligible(r) for r in doc.get("credentials") or []):
            continue
        company = company_for(provider)
        from .types import Credential
        for row in doc.get("credentials") or []:
            if not row.get("id"):
                # A row saved without an id keeps the steady one a read gives it.
                row["id"] = Credential.from_dict(row).id
        moved = {}
        for row in doc.get("credentials") or []:
            if _eligible(row):
                value = row["payload"]["auth_value"].strip()
                moved[row.get("id")] = store.add_company_key(
                    company, value, label=row.get("label") or "", source=row.get("source") or "web",
                    key_id=row.get("id") or "")

        def _strip(raw):
            d = json.loads(raw) if raw else {}
            rows, seen = [], set()
            for row in d.get("credentials") or []:
                if not row.get("id"):
                    row["id"] = Credential.from_dict(row).id
                if _eligible(row) and row.get("id") in moved:
                    row["id"] = moved[row["id"]]
                    row["payload"]["auth_value"] = ""
                    row["metadata"] = {**(row.get("metadata") or {}), COMPANY_MARK: company}
                if row.get("id") in seen:
                    continue
                seen.add(row.get("id"))
                rows.append(row)
            d["credentials"] = rows
            return json.dumps(d, indent=2, ensure_ascii=False).encode("utf-8")

        _private_atomic_update(path, _strip, root=store.root, lock_timeout=FILE_LOCK_TIMEOUT_SECONDS)
        store.drop_old_generations(provider)
        store._pools.pop(provider, None)
        _log.info("moved %d key(s) of %s to %s", len(moved), provider, company)


def _stored_values(store) -> set[str]:
    """Every key value stored anywhere: the company files and the way lists."""
    values: set[str] = set()
    base = store.base_dir()
    from .company_keys import KEYS_DIR
    for f in (base / KEYS_DIR).glob("*.json") if (base / KEYS_DIR).is_dir() else []:
        values.update((k.get("value") or "").strip() for k in store.company_keys(f.stem))
    for f in base.glob("*/credentials.json"):
        try:
            doc = json.loads(f.read_bytes())
        except Exception:
            continue
        for row in doc.get("credentials") or []:
            values.add(((row.get("payload") or {}).get("auth_value") or "").strip())
    values.discard("")
    return values


def _move_config_keys(store) -> None:
    from openprogram.state.config import read_config, update_config
    from .services import company_for_env_var

    try:
        if store.root.resolve() != _state_dir().resolve():
            return  # another instance's store, opened to read it
        keys = (read_config().get("api_keys") or {})
    except Exception:
        return
    if not isinstance(keys, dict) or not keys:
        return
    from .services import SETTINGS, save_setting, stored_key
    stored = _stored_values(store)
    done: list[str] = []
    for name, value in keys.items():
        value = (value or "").strip() if isinstance(value, str) else ""
        if not value:
            done.append(name)
            continue
        if name in SETTINGS:
            # No secret: a service's setting, kept with the other settings.
            if not stored_key(name):
                save_setting(name, value)
            done.append(name)
            continue
        if value in stored:
            done.append(name)
            continue
        company = company_for_env_var(name)
        if not company:
            continue
        store.add_company_key(company, value, source="web")
        stored.add(value)
        done.append(name)
    if not done:
        return

    def _drop(cfg: dict) -> None:
        left = {k: v for k, v in (cfg.get("api_keys") or {}).items() if k not in done}
        if left:
            cfg["api_keys"] = left
        else:
            cfg.pop("api_keys", None)

    update_config(_drop)
    # The copy kept before this write still holds the keys.
    from openprogram.state.config import get_config_path
    from openprogram.state.last_good import backup_path
    backup_path(get_config_path()).unlink(missing_ok=True)
    _log.info("moved %d key(s) out of config.json", len(done))


def _state_dir():
    from openprogram.state.paths import get_state_dir
    return get_state_dir()


__all__ = ["move_keys_to_companies"]

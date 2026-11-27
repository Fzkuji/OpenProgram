"""The one-time carry-over of the old credential layout.

The old layout kept one file per account, ``auth/<provider>/<account>.json``
(each a list of credentials), beside four side files: ``_active.json`` (the
account in use per provider), ``_rotation.json`` (rotation on/off and its
strategy), ``_rotation_excluded.json`` (accounts left out of rotation) and
``_account_priority.json`` (the order accounts were tried in). The current
layout is one ordered list per provider, ``auth/<provider>/credentials.json``.

Only an old account document is carried: a JSON object with a
``credentials`` list and the old account fields (``account_id``,
``provider_id``, ``active_credential_id`` or ``profile_id``). Any other file
in a provider folder (a hand-made settings file, ``usage.json``) is not
touched, and a file that cannot be read is logged by path and left where it
is; neither makes the carry-over run again on the next start.

The carry-over runs before the store first reads and does, in order:

1. Reads and checks every old account file first.
2. Copies the whole ``auth`` folder once to ``auth/.carry-over-backup``
   (inside the folder the sandbox never lets a tool read), checks the copy
   holds every file and only then writes its ``complete`` mark; a later run
   reuses a complete copy and replaces a partial one.
3. Flattens every account of a provider into one list: accounts in the
   saved priority order (else ``default`` first, then by name), the account
   in use first while rotation was off; within an account, its credentials
   in their order with its active credential first. An account name a
   person chose (not ``default``, ``key-N`` or ``account-N``) becomes the
   label. The rotation strategy becomes the provider's strategy; an account
   left out of rotation becomes "not in use". A credential without an id,
   or with an id another row already has, gets a new one.
4. Per provider, writes the new file, reads it back and checks that every
   credential arrived (each row's id) before it removes that provider's old
   files. A provider that fails keeps its old files (a new file written in
   this run is undone) without holding up the others; the side files go
   last, once every provider is through.

No credential that holds a secret is ever dropped or emptied. A key the old
layout read from an environment variable (old source ``env``) and stored a
copy of is carried as a stored key (source ``cli``, a new id) with that copy:
whether the variable is set depends on which process starts first (a shell,
the App from Finder, a Worker that put ``config.json``'s keys in its
environment), so it is never decided here. A stored key that a variable
also holds stays stored; the list shows it once at read time
(``store.env_rows``). An old environment row with no stored value has
nothing to keep (the variable gives its own row) and is left out, logged.
An old refusal whose error says the balance or credit ran out is carried as
a spent quota (held a day from when it happened; ready when it says no
time), not as a refused key. A sign-in takes its account's email and plan
from its id token (an imported one from the file it reads). Every row left
out is logged by provider, id and reason, never by value.

A credential already in a provider's new file is not added twice, so running
again after a partial removal changes nothing. Neither is an API key the list
already holds under another id: an older OpenProgram installed elsewhere on
the machine still writes the old layout (one file per start, from the
environment variable it reads), and each of those is the same key again. The
list keeps one row per stored key and endpoint, the earliest; rows that
repeat it are left out, logged by id.

:func:`repair` puts the same rules on lists an earlier carry-over already
wrote, once (``.repaired`` in the backup), and tightens the folders to 0700.
It changes states and names only, never a secret, so it keeps every last
good copy (``.bak``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

SIDE_FILES = (
    "_active.json", "_rotation.json", "_rotation_excluded.json",
    "_account_priority.json", "_order.json", "_disabled.json",
)
_GENERIC_NAME = re.compile(r"^(default|key-\d+|account-\d+)$")
_KIND = {"cli_delegated": "import", "external_process": "credential_process"}
_STRATEGY = {"fill_first": "in_order", "round_robin": "evenly", "random": "evenly",
             "least_used": "least_used", "fixed": "in_order"}
_QUOTA_HOLD_MS = 24 * 60 * 60 * 1000


@dataclass
class CarryOverReport:
    """What one run did: per provider, credentials found in the old files and in the new one."""

    ran: bool = False
    backup: Optional[Path] = None
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)


BACKUP_DIR = ".carry-over-backup"
_BACKUP_MARK = ".complete"
_OLD_ACCOUNT_FIELDS = ("account_id", "provider_id", "active_credential_id", "profile_id")


def _is_provider_dir(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith((".", "_")) and ".bak" not in path.name \
        and ".backup" not in path.name


def _is_old_account(doc: object) -> bool:
    """An old per-account credential document (and nothing else)."""
    return isinstance(doc, dict) and isinstance(doc.get("credentials"), list) \
        and any(k in doc for k in _OLD_ACCOUNT_FIELDS)


def _read_old(path: Path) -> Optional[dict]:
    """The old account document in ``path``, or ``None`` when it is not one
    (logged by path, never by content, when it cannot be read)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("credential carry-over: %s cannot be read (%s); it stays where it is",
                     path, type(exc).__name__)
        return None
    return doc if _is_old_account(doc) else None


def _old_pool_files(auth_dir: Path) -> list[tuple[Path, dict]]:
    """Every old account file under ``auth_dir`` with its document."""
    out: list[tuple[Path, dict]] = []
    for d in sorted(auth_dir.iterdir()):
        if not _is_provider_dir(d):
            continue
        for p in sorted(d.glob("*.json")):
            if p.name in ("credentials.json", "usage.json"):
                continue
            doc = _read_old(p)
            if doc is not None:
                out.append((p, doc))
    return out


def needs_carry_over(auth_dir: Path) -> bool:
    if not auth_dir.is_dir():
        return False
    return any((auth_dir / n).exists() for n in SIDE_FILES) or bool(_old_pool_files(auth_dir))


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        _log.warning("credential carry-over: side file %s cannot be read (%s); ignored",
                     path, type(exc).__name__)
        return {}
    return data if isinstance(data, dict) else {}


def _resolve(provider: str) -> str:
    try:
        from .account.aliases import resolve
        return resolve(provider)
    except Exception:
        return provider


# -- old payload shapes --------------------------------------------------------

_TYPE_TO_KIND = {
    "ApiKeyPayload": "api_key", "OAuthPayload": "oauth", "DeviceCodePayload": "device_code",
    "CliDelegatedPayload": "cli_delegated", "ExternalProcessPayload": "credential_process",
}
_AUTH_FIELD = {"ApiKeyPayload": "api_key", "OAuthPayload": "access_token",
               "DeviceCodePayload": "access_token"}


def _payload(old: dict, kind: str) -> dict:
    """An old payload in the current ``CredentialData`` shape."""
    if "__type__" not in old:
        return {"kind": _KIND.get(old.get("kind") or kind, old.get("kind") or kind),
                "auth_value": old.get("auth_value") or "", "base_url": old.get("base_url") or "",
                "headers": dict(old.get("headers") or {}), "data": dict(old.get("data") or {})}
    tname = old["__type__"]
    auth_field = _AUTH_FIELD.get(tname)
    data = {k: v for k, v in old.items() if k not in ("__type__", auth_field)}
    new_kind = _TYPE_TO_KIND.get(tname, kind)
    return {"kind": _KIND.get(new_kind, new_kind),
            "auth_value": old.get(auth_field, "") if auth_field else "",
            "base_url": "", "headers": {}, "data": data}


# -- one credential ------------------------------------------------------------

def _source(old: str) -> str:
    low = (old or "").lower()
    if low == "env":
        return "env"
    if "import" in low or "cli_import" in low or low.endswith("_cli"):
        return "import"
    if low.startswith("cli") or low in ("manual",):
        return "cli"
    return "web"


# 402 as an HTTP status only: "HTTP 402", "status 402", "402:" at the start,
# never a 402 inside an id or a message.
_HTTP_402 = re.compile(r"(?:^\s*402\b|\bHTTP\s*402\b|\bstatus(?:\s*code)?\s*[:=]?\s*402\b)",
                       re.IGNORECASE)
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _is_billing(text: object, code: object = "") -> bool:
    """An error that says the balance or credit ran out (an HTTP 402, or a
    body the runtime's classifier reads as a spent balance)."""
    from .provider_port import is_billing_exhausted
    text = str(text or "")
    if str(code or "").strip() == "402":
        return True
    return bool(text) and (is_billing_exhausted(text) or bool(_HTTP_402.search(text)))


def _quota_hold(at_ms: int, now_ms: int) -> tuple[str, int]:
    """A spent balance held a day from when it was seen; with no time to go
    by, it is carried as ready (its next refusal holds it again)."""
    if not at_ms:
        return "ready", 0
    hold = at_ms + _QUOTA_HOLD_MS
    return ("quota", hold) if hold > now_ms else ("ready", 0)


def _last_error(error: object, status: str, at_ms: int) -> Optional[dict]:
    """An old ``"code: message"`` error as ``{code, message, at}``; text
    with no short code before a colon is all message, its code the state."""
    if not error:
        return None
    text = str(error)
    head, sep, rest = text.partition(":")
    if sep and _ERROR_CODE.match(head.strip()):
        code, message = head.strip(), rest.strip()
    else:
        code, message = (status if status not in ("ready", "cooling") else "error"), text.strip()
    return {"code": code, "message": message, "at": at_ms}


def _status(old: dict, kind: str, now_ms: int) -> tuple[str, int]:
    status = old.get("status") or "valid"
    until = int(old.get("cooldown_until_ms") or 0)
    if status in ("needs_reauth", "revoked"):
        if _is_billing(old.get("last_error")):
            # A spent balance some providers answer with 401 (OpenCode's
            # CreditsError): the key is fine once the account is topped up.
            return _quota_hold(int(old.get("updated_at_ms") or 0), now_ms)
        return ("invalid_key" if kind == "api_key" else "needs_login"), 0
    if status == "billing_blocked":
        return _quota_hold(int(old.get("updated_at_ms") or 0), now_ms)
    if until > now_ms:
        return "cooling", until
    return "ready", 0


def _identity(old: dict, kind: str, payload: dict) -> dict:
    metadata = old.get("metadata") if isinstance(old.get("metadata"), dict) else {}
    out = {}
    for key in ("email", "plan", "org", "account_id"):
        if metadata.get(key):
            out[key] = metadata[key]
    if not out.get("email") and metadata.get("account") and "@" in str(metadata["account"]):
        out["email"] = metadata["account"]
    return _with_token_identity(out, kind, payload)


def _with_token_identity(identity: dict, kind: str, payload: dict) -> dict:
    """``identity`` with the email and plan a sign-in's id token names filled
    in where missing (an import's from the file it reads)."""
    from .account.account_labels import file_identity, token_identity
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if kind == "import":
        found = file_identity(data.get("store_path")) if not identity.get("email") \
            or not identity.get("plan") else {}
    elif kind in ("oauth", "device_code"):
        found = token_identity(data.get("id_token"))
    else:
        return identity
    if found.get("email") and identity.get("email") \
            and found["email"].lower() != str(identity["email"]).lower():
        return identity  # the token is another account's: take nothing from it
    return {**found, **identity}


def _as_stored_key(row: dict, provider: str) -> dict:
    """An old environment row that stored a copy of its key, as that key:
    a stored row with a new id, the copy kept (never emptied)."""
    from .types import _new_id
    out = {**row, "id": _new_id("cred"), "source": "cli", "read_only": False}
    _log.info("credential carry-over: %s row %s read an environment variable and stored "
              "its key; kept as stored key %s", provider, row.get("id"), out["id"])
    return out


def _label(account: str, old: dict) -> str:
    if not _GENERIC_NAME.match(account):
        return account
    metadata = old.get("metadata") if isinstance(old.get("metadata"), dict) else {}
    label = str(metadata.get("label") or "").strip()
    defaults = {account, str(metadata.get("email") or ""), str(metadata.get("display_name") or "")}
    if label and label not in defaults and not _GENERIC_NAME.match(label):
        return label
    return ""


def _credential(provider: str, account: str, old: dict, *, enabled: bool,
                now_ms: int) -> Optional[dict]:
    """One old credential as a row of the new list; ``None`` only for an
    old environment row with no stored value (nothing to keep)."""
    kind = _KIND.get(old.get("kind") or "api_key", old.get("kind") or "api_key")
    status, until = _status(old, kind, now_ms)
    metadata = {k: v for k, v in (old.get("metadata") or {}).items()
                if k not in ("label", "email", "plan", "org", "account_id", "display_name")}
    error = old.get("last_error")
    payload = _payload(old.get("payload") or {}, kind)
    value = payload.get("auth_value") or ""
    row = {
        "id": old.get("credential_id") or old.get("id"),
        "provider_id": provider,
        "kind": kind,
        "label": _label(account, old),
        "source": _source(old.get("source") or ""),
        "identity": _identity(old, kind, payload),
        "enabled": enabled,
        "status": status,
        "cooldown_until_ms": until,
        "blocked_models": {},
        "last_error": _last_error(error, status, int(old.get("updated_at_ms") or 0)),
        "last_used_at_ms": int(old.get("last_used_at_ms") or 0),
        "use_count": int(old.get("use_count") or 0),
        "strikes": 0,
        "created_at_ms": int(old.get("created_at_ms") or 0),
        "updated_at_ms": int(old.get("updated_at_ms") or 0),
        "read_only": bool(old.get("read_only", False)),
        "metadata": metadata,
        "key_tail": value.strip()[-4:] if kind in ("api_key", "token") and len(value.strip()) >= 8 else "",
        "payload": payload,
    }
    if row["source"] == "env":
        if not value.strip():
            _log.info("credential carry-over: %s row %s read an environment variable and "
                      "stored no key; left out (the variable gives its own row)",
                      provider, row["id"])
            return None
        return _as_stored_key(row, provider)
    return row


# -- one provider --------------------------------------------------------------

def _account_order(accounts: list[str], provider: str, side: dict) -> list[str]:
    saved = side["_account_priority.json"].get(provider)
    saved = [str(x) for x in saved] if isinstance(saved, list) else []
    index = {name: i for i, name in enumerate(saved)}
    ordered = sorted(accounts, key=lambda a: (0, index[a], "") if a in index
                     else (1, 0 if a == "default" else 1, a))
    rotation = side["_rotation.json"].get(provider) or {}
    active = side["_active.json"].get(provider)
    if not rotation.get("enabled") and active in ordered:
        ordered.remove(active)
        ordered.insert(0, active)
    return ordered


def _flatten(provider: str, pools: dict[str, dict], side: dict, now_ms: int) -> dict:
    rotation = side["_rotation.json"].get(provider) or {}
    excluded = side["_rotation_excluded.json"].get(provider)
    excluded = {str(x) for x in excluded} if isinstance(excluded, list) else set()
    rows: list[dict] = []
    for account in _account_order(list(pools), provider, side):
        doc = pools[account]
        creds = [c for c in (doc.get("credentials") or []) if isinstance(c, dict)]
        active = doc.get("active_credential_id")
        creds.sort(key=lambda c: 0 if active and c.get("credential_id") == active else 1)
        for old in creds:
            row = _credential(provider, account, old, enabled=account not in excluded, now_ms=now_ms)
            if row is not None:
                rows.append(row)
    if rows and not any(r["enabled"] for r in rows):
        for r in rows:
            r["enabled"] = True
    strategy = _STRATEGY.get(rotation.get("strategy") or "", "in_order") if rotation.get("enabled") \
        else "in_order"
    return {"strategy": strategy, "credentials": rows}


# -- the run -------------------------------------------------------------------

def _readable(path: Path) -> bool:
    return os.access(path, os.R_OK)


def _files(base: Path, *, skip: str = "") -> list[Path]:
    """Every readable file under ``base`` (one that cannot be read is not
    carried either, so it is not copied)."""
    return sorted(p.relative_to(base) for p in base.rglob("*")
                  if p.is_file() and _readable(p) and not (skip and p.relative_to(base).parts[0] == skip))


def _not_copied(auth_dir: Path):
    def ignore(directory: str, names: list[str]) -> list[str]:
        here = Path(directory)
        out = [BACKUP_DIR] if here == auth_dir else []
        out += [n for n in names if (here / n).is_file() and not _readable(here / n)]
        return out
    return ignore


def _backup(auth_dir: Path) -> Path:
    """One complete copy of ``auth_dir`` at ``auth_dir/.carry-over-backup``,
    made once: a complete one is reused, a partial one replaced."""
    target = auth_dir / BACKUP_DIR
    if (target / _BACKUP_MARK).is_file():
        return target
    if target.exists():
        shutil.rmtree(target)
    try:
        shutil.copytree(auth_dir, target, symlinks=True, ignore=_not_copied(auth_dir))
        if _files(auth_dir, skip=BACKUP_DIR) != _files(target):
            raise RuntimeError(f"backup {target} is incomplete")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    _tighten(target)
    (target / _BACKUP_MARK).write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    return target


def _existing(target: Path) -> tuple[bool, Optional[bytes], Optional[dict]]:
    """``(readable, bytes, document)`` of a provider's current file, read
    through its last good copy; unreadable with no good copy is not empty."""
    from openprogram.state.last_good import load_json
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return True, None, None
    except OSError:
        return False, None, None
    doc = load_json(target, raw)
    if not isinstance(doc, dict):
        return False, raw, None
    return True, raw, doc


def _stored_key(row: dict) -> Optional[tuple[str, str]]:
    """What makes two rows the same API key: the key and where it is sent.
    ``None`` for a sign-in or a row without a stored key."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    value = str(payload.get("auth_value") or "").strip()
    if row.get("kind") != "api_key" or not value:
        return None
    return value, str(payload.get("base_url") or "")


def _one_row_per_key(provider: str, rows: list[dict]) -> list[dict]:
    """``rows`` with each stored API key once, the first row kept (it has the
    key's history). The key itself stays stored, in the row that is kept."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = _stored_key(row)
        if key and key in seen:
            _log.info("credential carry-over: %s row %s repeats a key an earlier row holds; "
                      "the earlier row is kept", provider, row.get("id"))
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


def _carry_provider(auth_dir: Path, root: Path, provider: str, pools: dict[str, dict],
                    side: dict, now_ms: int, report: CarryOverReport) -> bool:
    """Write one provider's list; ``True`` when its old files may go."""
    from openprogram.state.private_files import _private_atomic_write, _private_unlink
    from .types import CREDENTIAL_SCHEMA_VERSION, CredentialPool, _new_id

    flat = _flatten(provider, pools, side, now_ms)
    report.before[provider] = sum(
        1 for doc in pools.values() for c in (doc.get("credentials") or []) if isinstance(c, dict))
    target = auth_dir / provider / "credentials.json"
    readable, previous, existing = _existing(target)
    if not readable:
        _log.warning("credential carry-over: %s cannot be read; %s's old files stay", target, provider)
        return False
    if not flat["credentials"]:
        report.after[provider] = 0
        return True
    rows = _one_row_per_key(
        provider, [r for r in (existing or {}).get("credentials") or [] if isinstance(r, dict)])
    keys = {k for k in map(_stored_key, rows) if k}
    have: set[str] = set()
    for r in rows:
        if not r.get("id") or r["id"] in have:
            r["id"] = _new_id("cred")
        have.add(r["id"])
    added: list[dict] = []
    for row in flat["credentials"]:
        if row["id"] and row["id"] in have:
            if any(r.get("id") == row["id"] and r.get("payload") == row["payload"]
                   for r in rows + added):
                continue  # carried already (an earlier run, or a copied file)
            row["id"] = None  # an id another credential already has
        key = _stored_key(row)
        if key and key in keys:
            _log.info("credential carry-over: %s row %s holds a key the list already has; "
                      "the list keeps its row", provider, row.get("id"))
            continue
        if not row["id"]:
            row["id"] = _new_id("cred")
        have.add(row["id"])
        added.append(row)
    rows.extend(added)
    doc = {"v": CREDENTIAL_SCHEMA_VERSION, "provider_id": provider,
           "strategy": (existing or {}).get("strategy") or flat["strategy"],
           "cursor": int((existing or {}).get("cursor") or 0), "credentials": rows}
    data = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
    try:
        _private_atomic_write(target, lambda h: h.write(data), root=root)
        back = [c.id for c in CredentialPool.from_dict(
            json.loads(target.read_text(encoding="utf-8"))).credentials]
        if back != [r["id"] for r in rows]:
            raise RuntimeError(f"{target} did not read back every credential")
    except Exception:
        _log.warning("credential carry-over of %s failed; its old files stay", provider, exc_info=True)
        try:
            if previous is None:
                _private_unlink(target, root=root)
            else:
                _private_atomic_write(target, lambda h: h.write(previous), root=root)
        except Exception:
            _log.warning("could not undo %s", target, exc_info=True)
        return False
    report.after[provider] = sum(1 for r in flat["credentials"] if r["id"] in back)
    return True


def carry_over(auth_dir: Path, *, state_root: Optional[Path] = None) -> CarryOverReport:
    """Carry the old layout under ``auth_dir`` over once (module docstring)."""
    report = CarryOverReport()
    if not needs_carry_over(auth_dir):
        return report
    root = state_root or auth_dir.parent
    from openprogram.state.private_files import _private_file_lock, _private_unlink

    with _private_file_lock(auth_dir / "_carry_over", root=root, timeout=60):
        old_files = _old_pool_files(auth_dir)
        if not old_files and not any((auth_dir / n).exists() for n in SIDE_FILES):
            return report
        report.ran = True
        report.backup = _backup(auth_dir)
        now_ms = int(time.time() * 1000)
        side = {name: _read_json(auth_dir / name) for name in SIDE_FILES}

        grouped: dict[str, dict[str, dict]] = {}
        sources: dict[str, list[Path]] = {}
        for path, doc in old_files:
            provider = _resolve(str(doc.get("provider_id") or path.parent.name))
            account = str(doc.get("account_id") or doc.get("profile_id") or path.stem)
            pools = grouped.setdefault(provider, {})
            if account in pools:
                pools[account] = {**pools[account], "credentials":
                                  (pools[account].get("credentials") or []) + (doc.get("credentials") or [])}
            else:
                pools[account] = doc
            sources.setdefault(provider, []).append(path)

        failed: list[str] = []
        for provider, pools in sorted(grouped.items()):
            try:
                done = _carry_provider(auth_dir, root, provider, pools, side, now_ms, report)
            except Exception:
                _log.warning("credential carry-over of %s failed; its old files stay",
                             provider, exc_info=True)
                done = False
            if not done:
                failed.append(provider)
                continue
            for path in sources[provider]:
                for leftover in (path, path.with_name(path.name + ".lock"), path.with_name(path.name + ".bak")):
                    _private_unlink(leftover, root=root)
        _drop_lock_only_dirs(auth_dir, root)
        if not failed:
            for name in SIDE_FILES:
                for leftover in (auth_dir / name, auth_dir / (name + ".lock"), auth_dir / (name + ".tmp")):
                    _private_unlink(leftover, root=root)
        _log.info("carried %d credentials of %d providers over to the per-provider layout "
                  "(backup %s%s)", sum(report.after.values()), len(report.after), report.backup,
                  f"; not carried: {', '.join(failed)}" if failed else "")
    return report


def _drop_lock_only_dirs(auth_dir: Path, root: Path) -> None:
    """Remove lock files without their file, and provider folders left empty.

    A lock file is removed only while this process holds it (taken without
    waiting, skipped when another process has it), so a writer that holds
    it is never cut off; a writer waiting on it sees it was removed and
    takes the new one (``state.private_files._private_file_lock``)."""
    from openprogram.state.private_files import drop_unused_lock
    del root
    for d in auth_dir.iterdir():
        if not _is_provider_dir(d):
            continue
        for lock in d.glob("*.json.lock"):
            drop_unused_lock(lock, d / lock.name.removesuffix(".lock"))
        try:
            d.rmdir()  # only an empty folder goes
        except OSError:
            pass


def _tighten(top: Path) -> None:
    """Every folder under ``top`` (and ``top``) readable by its owner only.
    A symlinked folder is not followed, and a folder whose mode cannot be
    changed (another owner, a filesystem without modes) is left as it is."""
    if top.is_symlink():
        return
    for here, _dirs, _files_ in os.walk(top):
        try:
            if not os.path.islink(here):
                os.chmod(here, 0o700)
        except OSError:
            _log.debug("could not tighten %s", here, exc_info=True)


# -- repairing a list an earlier carry-over wrote ---------------------------

REPAIR_MARK = ".repaired"


def _repair_rows(provider: str, rows: list[dict], now_ms: int,
                 last_good: Optional[list[dict]] = None) -> list[dict]:
    """``rows`` under the rules the carry-over now follows. Every row is
    kept with its secret: only ids, sources, states and names change.
    ``last_good`` is the previous generation's rows (``.bak``), read for an
    error a later check wrote over."""
    earlier = {r.get("id"): r for r in last_good or [] if isinstance(r, dict)}
    out: list[dict] = []
    for row in rows:
        row = dict(row)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row.get("id") == "env" and row.get("source") != "env":
            # An old environment row an earlier carry-over stored with its key.
            row = _as_stored_key(row, provider)
        error = row.get("last_error") if isinstance(row.get("last_error"), dict) else {}
        if error and not _ERROR_CODE.match(str(error.get("code") or "")):
            # A whole sentence carried as the code.
            row["last_error"] = error = {**error, "code": row.get("status") or "error"}
        if row.get("status") in ("invalid_key", "needs_login"):
            before = earlier.get(row.get("id")) or {}
            before_error = before.get("last_error") if isinstance(before.get("last_error"), dict) else {}
            billing = next((e for e in (error, before_error) if e and _is_billing(
                f"{e.get('code') or ''}: {e.get('message') or ''}", e.get("code"))), None)
            if billing is not None:
                row["status"], row["cooldown_until_ms"] = _quota_hold(int(billing.get("at") or 0), now_ms)
                if billing is not error:
                    row["last_error"] = billing
        identity = row.get("identity") if isinstance(row.get("identity"), dict) else {}
        filled = _with_token_identity(dict(identity), str(row.get("kind") or ""), payload)
        if filled != identity:
            row["identity"] = filled
        out.append(row)
    return out


def repair(auth_dir: Path, *, state_root: Optional[Path] = None) -> list[str]:
    """Put the carry-over's rules on lists an earlier carry-over wrote (env
    rows stored as keys, a spent balance carried as a refused key, an
    imported sign-in with no email), tighten the folders and drop lock-only
    ones. Runs once after a carry-over (its backup gets ``.repaired``);
    returns the providers whose list changed."""
    backup = auth_dir / BACKUP_DIR
    if not (backup / _BACKUP_MARK).is_file() or (backup / REPAIR_MARK).is_file():
        return []
    root = state_root or auth_dir.parent
    from openprogram.state.last_good import backup_path
    from openprogram.state.private_files import _private_atomic_update, _private_file_lock

    changed: list[str] = []
    now_ms = int(time.time() * 1000)
    with _private_file_lock(auth_dir / "_carry_over", root=root, timeout=60):
        if (backup / REPAIR_MARK).is_file():
            return []
        for d in sorted(auth_dir.iterdir()):
            target = d / "credentials.json"
            if not _is_provider_dir(d) or not target.is_file():
                continue
            last_good = _rows_of(backup_path(target))

            def _fix(raw: Optional[bytes], provider: str = d.name, target: Path = target,
                     last_good: list = last_good) -> bytes:
                from openprogram.state.last_good import load_json
                doc = load_json(target, raw) if raw is not None else None
                if not isinstance(doc, dict):
                    raise ValueError("unreadable")
                rows = [r for r in doc.get("credentials") or [] if isinstance(r, dict)]
                fixed = _repair_rows(provider, rows, now_ms, last_good)
                if fixed == rows:
                    raise _Unchanged()
                return json.dumps({**doc, "credentials": fixed}, indent=2,
                                  ensure_ascii=False).encode("utf-8")

            try:
                _private_atomic_update(target, _fix, root=root)
            except Exception as exc:
                if not isinstance(exc, _Unchanged) and not isinstance(exc.__cause__, _Unchanged):
                    _log.warning("credential repair of %s skipped", d.name, exc_info=True)
                continue
            changed.append(d.name)
        try:
            _drop_lock_only_dirs(auth_dir, root)
            _tighten(auth_dir)
        except OSError:
            # Housekeeping only: it never makes the repair run again.
            _log.warning("credential repair: tidying %s failed", auth_dir, exc_info=True)
        (backup / REPAIR_MARK).write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    if changed:
        _log.info("repaired the carried credential lists of %s", ", ".join(changed))
    return changed


def _rows_of(path: Path) -> list[dict]:
    """The credential rows of a list file (a last good copy), else none."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("credentials") if isinstance(doc, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


class _Unchanged(Exception):
    pass


__all__ = ["BACKUP_DIR", "CarryOverReport", "REPAIR_MARK", "SIDE_FILES", "carry_over",
           "needs_carry_over", "repair"]

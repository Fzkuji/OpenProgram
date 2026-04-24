"""Multi-account channel credential store.

Prior design had one account per platform stuffed into ``config.json``
under ``channels.<platform>``. The multi-agent model needs many
accounts per platform (e.g. one WhatsApp for personal, another for
work — each bound to different agents), and a dedicated storage
location per account so credentials don't collide.

Layout:

    <state>/channels/<channel>/accounts/<account_id>/
        account.json        # {channel, account_id, created_at, ...metadata}
        credentials.json    # bot token / ilink creds / etc. (0600)

For backward UX the ``account_id`` ``default`` is reserved for the
"the one account per platform" case — CLI / setup can pretend the
account dimension doesn't exist for single-account installs by always
resolving to ``default``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


SUPPORTED_CHANNELS = ("wechat", "telegram", "discord", "slack")
DEFAULT_ACCOUNT_ID = "default"
_VALID_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")

_lock = threading.RLock()


@dataclass
class ChannelAccount:
    channel: str                       # "wechat" | "telegram" | ...
    account_id: str                    # "default" | "personal" | ...
    name: str = ""                     # optional human label
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChannelAccount":
        return cls(
            channel=str(raw.get("channel") or ""),
            account_id=str(raw.get("account_id") or ""),
            name=str(raw.get("name") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
        )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _channels_root() -> Path:
    from openprogram.paths import get_state_dir
    root = get_state_dir() / "channels"
    root.mkdir(parents=True, exist_ok=True)
    return root


def channel_dir(channel: str) -> Path:
    d = _channels_root() / channel
    d.mkdir(parents=True, exist_ok=True)
    return d


def account_dir(channel: str, account_id: str) -> Path:
    d = channel_dir(channel) / "accounts" / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def account_meta_path(channel: str, account_id: str) -> Path:
    return account_dir(channel, account_id) / "account.json"


def account_credentials_path(channel: str, account_id: str) -> Path:
    return account_dir(channel, account_id) / "credentials.json"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_channels() -> list[str]:
    root = _channels_root()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name in SUPPORTED_CHANNELS:
            out.append(entry.name)
    return out


def list_accounts(channel: str) -> list[ChannelAccount]:
    accounts_root = channel_dir(channel) / "accounts"
    if not accounts_root.is_dir():
        return []
    out = []
    for entry in sorted(accounts_root.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "account.json"
        if not meta_path.exists():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            out.append(ChannelAccount.from_dict(raw))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def list_all_accounts() -> list[ChannelAccount]:
    """Every account across every channel."""
    out = []
    for ch in list_channels():
        out.extend(list_accounts(ch))
    return out


def get(channel: str, account_id: str) -> Optional[ChannelAccount]:
    meta_path = account_meta_path(channel, account_id)
    if not meta_path.exists():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ChannelAccount.from_dict(raw)


def create(channel: str, account_id: str = DEFAULT_ACCOUNT_ID,
           *, name: str = "") -> ChannelAccount:
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError(
            f"Unknown channel {channel!r}. Supported: {SUPPORTED_CHANNELS}"
        )
    if not _VALID_ID.match(account_id):
        raise ValueError(
            f"Invalid account id {account_id!r} — must start with a "
            f"letter, contain only [a-z0-9_-], ≤40 chars."
        )
    with _lock:
        if get(channel, account_id) is not None:
            raise ValueError(
                f"Account {channel}:{account_id} already exists."
            )
        now = time.time()
        acct = ChannelAccount(
            channel=channel,
            account_id=account_id,
            name=name or account_id,
            created_at=now,
            updated_at=now,
        )
        meta = account_meta_path(channel, account_id)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(
            json.dumps(acct.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # Empty credentials.json with 0600 so later secrets don't
        # briefly live world-readable.
        cred_path = account_credentials_path(channel, account_id)
        if not cred_path.exists():
            cred_path.write_text("{}\n", encoding="utf-8")
            try:
                os.chmod(cred_path, 0o600)
            except OSError:
                pass
        return acct


def delete(channel: str, account_id: str) -> None:
    with _lock:
        folder = account_dir(channel, account_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)


def load_credentials(channel: str, account_id: str) -> dict[str, Any]:
    """Read credentials.json for an account. Returns {} if missing."""
    path = account_credentials_path(channel, account_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(channel: str, account_id: str,
                     creds: dict[str, Any]) -> None:
    """Atomically replace credentials.json with ``creds``."""
    with _lock:
        path = account_credentials_path(channel, account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(creds, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def update_credentials(channel: str, account_id: str,
                       patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into credentials.json (shallow merge)."""
    with _lock:
        creds = load_credentials(channel, account_id)
        creds.update(patch)
        save_credentials(channel, account_id, creds)
        return creds


def is_configured(channel: str, account_id: str) -> bool:
    """Heuristic: does this account have enough credentials to start?

    Rules per channel:
      telegram — bot_token non-empty
      discord  — bot_token non-empty
      slack    — bot_token AND app_token non-empty (Socket Mode)
      wechat   — ilink_bot_id + bot_token (populated after QR login)
    """
    creds = load_credentials(channel, account_id)
    if channel == "telegram":
        return bool(creds.get("bot_token"))
    if channel == "discord":
        return bool(creds.get("bot_token"))
    if channel == "slack":
        return bool(creds.get("bot_token")) and bool(creds.get("app_token"))
    if channel == "wechat":
        return bool(creds.get("bot_token")) and bool(creds.get("ilink_bot_id"))
    return False


def is_enabled(channel: str, account_id: str) -> bool:
    """Account meta may have ``enabled: false`` to silence an account
    without deleting it. Missing/empty meta defaults to enabled."""
    acct = get(channel, account_id)
    if acct is None:
        return False
    raw_path = account_meta_path(channel, account_id)
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return bool(raw.get("enabled", True))


def set_enabled(channel: str, account_id: str, enabled: bool) -> None:
    with _lock:
        raw_path = account_meta_path(channel, account_id)
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        raw["enabled"] = bool(enabled)
        raw["updated_at"] = time.time()
        raw_path.write_text(
            json.dumps(raw, indent=2, sort_keys=True),
            encoding="utf-8",
        )

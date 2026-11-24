"""Paired devices: one-time pairing codes and long-lived device credentials.

The owner token (``owner_auth.OwnerAuthState``) is new on every Web start,
which suits this computer's own clients: the App and the CLI read it from
the state directory. Another device cannot, so it pairs once instead:

* An authenticated client asks for a **pairing code** (``POST
  /api/auth/pairings``). It is single-use, lives ten minutes and only in
  this process, and travels in a link fragment (``/#pair=<code>``) or a QR
  code of that link.
* The other device redeems it (``POST /api/auth/pair``) for a **device
  credential** ``opd1.<id>.<secret>``. A browser receives it as an HttpOnly
  cookie, a native client in the response body for ``Authorization:
  Bearer``. Only ``sha256(secret)`` is stored, in ``<state>/web/devices.json``.
* The credential survives Web restarts. A device unused for thirty days is
  forgotten; the owner revokes one at any time, which also closes its open
  WebSockets.

A device acts as the same single owner as every other client: pairing
grants the owner's authority, and the device id is recorded only to list,
audit and revoke it. Design: docs/reference/design/ui/remote-web-access.html.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PAIRING_TTL_SECONDS = 10 * 60
IDLE_LIMIT_SECONDS = 30 * 24 * 60 * 60
# last_used is written to disk at most this often per device, so an active
# phone does not rewrite the file on every request.
_TOUCH_INTERVAL_SECONDS = 60 * 60
_MAX_PENDING_PAIRINGS = 16
_MAX_LABEL = 64

DEVICE_TOKEN_PREFIX = "opd1."
_DEVICE_TOKEN_RE = re.compile(r"^opd1\.([0-9a-f]{16})\.([A-Za-z0-9_-]{43})$")
_PAIRING_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def clean_label(value: object) -> str:
    """A display label: one line, at most 64 characters, never empty."""
    text = _CONTROL_RE.sub(" ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)[:_MAX_LABEL].strip()
    return text or "Device"


def label_from_user_agent(user_agent: str | None) -> str:
    """``Safari on iPhone``-style label for a browser that paired itself."""
    ua = user_agent or ""
    if "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Android" in ua:
        system = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "Mac"
    elif "Windows" in ua:
        system = "Windows"
    elif "Linux" in ua or "X11" in ua:
        system = "Linux"
    else:
        system = ""
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = ""
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Device"


@dataclass
class Device:
    id: str
    label: str
    created_at: float
    last_used_at: float
    secret_hash: str

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


@dataclass
class _Pairing:
    label: str | None
    expires_at: float


class DeviceRegistry:
    """Paired devices of one profile, persisted when ``path`` is given."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time):
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._lock = threading.Lock()
        self._devices: dict[str, Device] = {}
        self._pairings: dict[str, _Pairing] = {}
        self._watchers: dict[str, set[Callable[[], None]]] = {}
        self._persisted_use: dict[str, float] = {}
        self._load()

    # ── storage ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        if self._path is None:
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return
        entries = payload.get("devices")
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                device = Device(
                    id=str(entry["id"]),
                    label=clean_label(entry["label"]),
                    created_at=float(entry["created_at"]),
                    last_used_at=float(entry["last_used_at"]),
                    secret_hash=str(entry["secret_hash"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if _DEVICE_ID_RE.match(device.id) and re.fullmatch(r"[0-9a-f]{64}", device.secret_hash):
                self._devices[device.id] = device
                self._persisted_use[device.id] = device.last_used_at

    def _save(self) -> None:
        """Write the device list atomically, readable by this user only."""
        if self._path is None:
            return
        content = json.dumps(
            {
                "version": 1,
                "devices": [
                    {
                        "id": d.id,
                        "label": d.label,
                        "created_at": d.created_at,
                        "last_used_at": d.last_used_at,
                        "secret_hash": d.secret_hash,
                    }
                    for d in sorted(self._devices.values(), key=lambda d: d.created_at)
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        from openprogram.host._platform.filesystem import replace_file, restrict_to_user

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            replace_file(tmp, self._path)
            restrict_to_user(self._path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        self._persisted_use = {d.id: d.last_used_at for d in self._devices.values()}

    # ── expiry ───────────────────────────────────────────────────────────
    def _expire(self, now: float) -> bool:
        """Drop stale pairings and idle devices; True when devices changed."""
        for code_hash in [k for k, p in self._pairings.items() if p.expires_at <= now]:
            del self._pairings[code_hash]
        idle = [d.id for d in self._devices.values() if now - d.last_used_at > IDLE_LIMIT_SECONDS]
        for device_id in idle:
            del self._devices[device_id]
        return bool(idle)

    # ── pairing ──────────────────────────────────────────────────────────
    def create_pairing(self, label: str | None = None) -> tuple[str, float]:
        """Mint a single-use code; return it with its expiry time."""
        code = _b64(secrets.token_bytes(32))
        now = self._clock()
        with self._lock:
            if self._expire(now):
                self._save()
            if len(self._pairings) >= _MAX_PENDING_PAIRINGS:
                oldest = min(self._pairings, key=lambda k: self._pairings[k].expires_at)
                del self._pairings[oldest]
            expires_at = now + PAIRING_TTL_SECONDS
            self._pairings[_digest(code)] = _Pairing(
                label=clean_label(label) if label else None,
                expires_at=expires_at,
            )
        return code, expires_at

    def redeem(self, code: str, *, label_hint: str | None = None) -> tuple[Device, str] | None:
        """Spend a pairing code; return the new device and its credential."""
        if not isinstance(code, str) or not _PAIRING_CODE_RE.match(code):
            return None
        now = self._clock()
        with self._lock:
            self._expire(now)
            pairing = self._pairings.pop(_digest(code), None)
            if pairing is None:
                return None
            device_id = secrets.token_hex(8)
            while device_id in self._devices:
                device_id = secrets.token_hex(8)
            secret = _b64(secrets.token_bytes(32))
            device = Device(
                id=device_id,
                label=pairing.label or clean_label(label_hint),
                created_at=now,
                last_used_at=now,
                secret_hash=_digest(secret),
            )
            self._devices[device_id] = device
            self._save()
        return device, f"{DEVICE_TOKEN_PREFIX}{device_id}.{secret}"

    # ── verification ─────────────────────────────────────────────────────
    def verify(self, token: str | None) -> str | None:
        """The device id a credential names, when it is live; else None."""
        if not token or not token.startswith(DEVICE_TOKEN_PREFIX):
            return None
        match = _DEVICE_TOKEN_RE.match(token)
        if match is None:
            return None
        device_id, secret = match.groups()
        now = self._clock()
        with self._lock:
            changed = self._expire(now)
            device = self._devices.get(device_id)
            if device is None or not hmac.compare_digest(device.secret_hash, _digest(secret)):
                if changed:
                    self._save()
                return None
            device.last_used_at = now
            if changed or now - self._persisted_use.get(device_id, 0.0) >= _TOUCH_INTERVAL_SECONDS:
                self._save()
        return device_id

    # ── management ───────────────────────────────────────────────────────
    def list(self) -> list[dict]:
        with self._lock:
            if self._expire(self._clock()):
                self._save()
            devices = sorted(self._devices.values(), key=lambda d: d.created_at)
            return [d.public() for d in devices]

    def rename(self, device_id: str, label: str) -> bool:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return False
            device.label = clean_label(label)
            self._save()
        return True

    def revoke(self, device_id: str) -> bool:
        """Forget a device and close its live connections."""
        with self._lock:
            if self._devices.pop(device_id, None) is None:
                return False
            self._save()
            watchers = list(self._watchers.pop(device_id, ()))
        for notify in watchers:
            try:
                notify()
            except Exception:
                pass
        return True

    def watch(self, device_id: str, notify: Callable[[], None]) -> Callable[[], None]:
        """Call ``notify`` once if ``device_id`` is revoked; return an unwatch."""
        with self._lock:
            self._watchers.setdefault(device_id, set()).add(notify)

        def unwatch() -> None:
            with self._lock:
                watchers = self._watchers.get(device_id)
                if watchers is not None:
                    watchers.discard(notify)
                    if not watchers:
                        self._watchers.pop(device_id, None)

        return unwatch


__all__ = [
    "DEVICE_TOKEN_PREFIX",
    "Device",
    "DeviceRegistry",
    "IDLE_LIMIT_SECONDS",
    "PAIRING_TTL_SECONDS",
    "clean_label",
    "label_from_user_agent",
]

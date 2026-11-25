"""Web Push from this computer to the owner's phones and browsers.

A phone that opens OpenProgram over https, usually added to its Home Screen,
subscribes here; when a top-level conversation needs the owner, has a reply
ready or stops with an error, this computer encrypts a short notice and
posts it to that browser's push service (Apple, Google, Mozilla, Microsoft).
No server of ours sits in between: the key pair that signs the requests
(VAPID, RFC 8292) is made on this computer, and the payload is encrypted
for the subscribing browser alone (RFC 8291, ``aes128gcm``). The design is
dsh-mobile's Web Push: generic, localized text with no conversation content,
per-category switches, and a subscription that ends with its device.

State lives in ``<state dir>/web_push/``: ``vapid.pem`` (the private key)
and ``subscriptions.json``; both are the owner's only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)

CATEGORIES = ("attention", "replies", "failures")
_TEXT = {
    "attention": ("A conversation needs you", "有对话在等你"),
    "replies": ("A reply is ready", "回复好了"),
    "failures": ("A conversation stopped with an error", "有对话出错停下了"),
    "test": ("Notifications are on", "通知已打开"),
}
# Push services browsers subscribe to. A subscription naming any other host is
# refused, so a page cannot make this computer post to an address it chose.
_PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")
_SUBJECT = "https://github.com/Fzkuji/OpenProgram"
_TTL_SECONDS = 1800
_RECORD_SIZE = 4096
_MAX_PAYLOAD = 3000

_lock = threading.Lock()
_sent: dict[str, int] = {}
_device_alive: Callable[[str], bool] | None = None


def set_device_check(alive: Callable[[str], bool] | None) -> None:
    """How to tell a paired device still exists; its subscriptions go with it."""
    global _device_alive
    _device_alive = alive


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _dir() -> Path:
    from openprogram.state.paths import get_state_dir

    path = get_state_dir() / "web_push"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _write_private(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    replace_file(temporary, path)


# ── keys ─────────────────────────────────────────────────────────────

def _private_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = _dir() / "vapid.pem"
    with _lock:
        try:
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError):
            key = ec.generate_private_key(ec.SECP256R1())
            _write_private(path, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            return key


def _point(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """The application server key a browser subscribes with (base64url)."""
    return _b64(_point(_private_key().public_key()))


def _vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": _SUBJECT}
    signing_input = (_b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
                     + "." + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    key = _private_key()
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    token = signing_input + "." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={_b64(_point(key.public_key()))}"


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """``aes128gcm`` content for one subscription (RFC 8291), one record."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    receiver = _unb64(p256dh)
    secret = _unb64(auth)
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    sender = _point(ephemeral.public_key())
    shared = ephemeral.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver))
    ikm = HKDF(hashes.SHA256(), 32, secret, b"WebPush: info\x00" + receiver + sender).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", _RECORD_SIZE, len(sender)) + sender + body


# ── subscriptions ────────────────────────────────────────────────────

def valid_endpoint(endpoint: object) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return False
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and parts.port in (None, 443) and not parts.username
            and any(host == allowed or host.endswith("." + allowed) for allowed in _PUSH_HOSTS))


def subscription_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]


def _load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((_dir() / "subscriptions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(subscriptions: dict[str, dict[str, Any]]) -> None:
    _write_private(_dir() / "subscriptions.json", json.dumps(subscriptions, indent=1).encode("utf-8"))


def save_subscription(subscription: dict, *, device_id: str | None, lang: str,
                      categories: Iterable[str] | None) -> dict[str, Any]:
    """Add or update this browser's subscription; returns what it receives."""
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    if not valid_endpoint(endpoint):
        raise ValueError("unsupported_push_service")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and len(_unb64(p256dh)) == 65 and len(_unb64(auth)) == 16):
        raise ValueError("invalid_subscription")
    with _lock:
        subscriptions = _load()
        key = subscription_id(endpoint)
        previous = subscriptions.get(key, {})
        chosen = list(previous.get("categories", CATEGORIES)) if categories is None else [
            name for name in CATEGORIES if name in set(categories)]
        subscriptions[key] = {
            "endpoint": endpoint, "p256dh": p256dh, "auth": auth, "device_id": device_id,
            "lang": "zh" if lang.startswith("zh") else "en", "categories": chosen,
            "created_at": previous.get("created_at", time.time()),
        }
        _save(subscriptions)
    return {"categories": chosen}


def remove_subscription(endpoint: str) -> bool:
    with _lock:
        subscriptions = _load()
        if subscriptions.pop(subscription_id(endpoint), None) is None:
            return False
        _save(subscriptions)
        return True


def forget_devices(alive: Callable[[str], bool]) -> None:
    """Drop the subscriptions of revoked or expired paired devices."""
    with _lock:
        subscriptions = _load()
        kept = {key: value for key, value in subscriptions.items()
                if value.get("device_id") is None or alive(str(value["device_id"]))}
        if len(kept) != len(subscriptions):
            _save(kept)


# ── sending ──────────────────────────────────────────────────────────

def _payload(kind: str, lang: str, session_id: str | None, tag: str) -> bytes:
    english, chinese = _TEXT[kind]
    body = {
        "title": "OpenProgram",
        "body": chinese if lang == "zh" else english,
        "url": f"/s/{session_id}" if session_id else "/chat",
        "tag": tag,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")[:_MAX_PAYLOAD]


def _post(record: dict[str, Any], kind: str, session_id: str | None, tag: str) -> int:
    import httpx

    payload = encrypt(_payload(kind, record.get("lang", "en"), session_id, tag), record["p256dh"], record["auth"])
    headers = {
        "Authorization": _vapid_header(record["endpoint"]),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(_TTL_SECONDS),
        "Urgency": "high" if kind in {"attention", "failures"} else "normal",
        "Topic": hashlib.sha256(tag.encode()).hexdigest()[:32],
    }
    with httpx.Client(trust_env=False, timeout=10.0, follow_redirects=False) as client:
        return client.post(record["endpoint"], content=payload, headers=headers).status_code


def send(kind: str, *, session_id: str | None = None, tag: str | None = None,
         only_endpoint: str | None = None) -> int:
    """Post one notice to every subscription that takes ``kind``; returns how many accepted."""
    if _device_alive is not None:
        forget_devices(_device_alive)
    with _lock:
        subscriptions = _load()
    targets = [
        record for record in subscriptions.values()
        if valid_endpoint(record.get("endpoint"))
        and (record["endpoint"] == only_endpoint if only_endpoint else kind in record.get("categories", ()))
    ]
    accepted, gone = 0, []
    for record in targets:
        try:
            status = _post(record, kind, session_id, tag or f"{kind}:{session_id or ''}")
        except Exception as exc:  # noqa: BLE001 - one phone's failure never stops the others
            _log.info("web push to %s failed: %s", urlsplit(record["endpoint"]).hostname, type(exc).__name__)
            continue
        if status in (404, 410):
            gone.append(record["endpoint"])
        elif 200 <= status < 300:
            accepted += 1
        else:
            _log.info("web push to %s answered %s", urlsplit(record["endpoint"]).hostname, status)
    for endpoint in gone:
        remove_subscription(endpoint)
    return accepted


def kind_of(frame: dict) -> tuple[str, str, str] | None:
    """What an ``execution.updated`` frame tells the owner, if anything:
    ``(kind, session_id, dedupe key)``. Only top-level conversations count."""
    if not isinstance(frame, dict) or frame.get("type") != "execution.updated":
        return None
    execution = frame.get("execution") or {}
    if execution.get("parent_execution_id") or not execution.get("session_id"):
        return None
    status, reason = execution.get("status"), execution.get("reason_code")
    if status == "paused" and reason in {"wait_open", "system_access_required"}:
        kind = "attention"
    elif status == "completed":
        kind = "replies"
    elif status == "failed":
        kind = "failures"
    else:
        return None
    return kind, str(execution["session_id"]), f"{execution.get('execution_id')}:{execution.get('status_version')}"


def on_frame(frame: dict) -> None:
    """Event-bus hook: send in the background, once per execution version."""
    found = kind_of(frame)
    if found is None:
        return
    kind, session_id, key = found
    with _lock:
        if key in _sent:
            return
        if len(_sent) > 2048:
            _sent.clear()
        _sent[key] = 1
        if not _load():
            return
    threading.Thread(target=send, args=(kind,), kwargs={"session_id": session_id},
                     daemon=True, name="web-push").start()


__all__ = ["CATEGORIES", "encrypt", "forget_devices", "kind_of", "on_frame", "public_key",
           "remove_subscription", "save_subscription", "send", "set_device_check", "subscription_id", "valid_endpoint"]

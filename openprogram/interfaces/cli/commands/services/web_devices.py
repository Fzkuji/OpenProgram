"""``openprogram web pair`` and ``openprogram web devices``.

Pairing signs another device (a phone, a second computer's browser) in to
this profile's Web server for good: the printed link carries a single-use
code in its fragment, valid ten minutes. The worker mints and checks the
codes (``interfaces/server/device_access.py``); this module only asks it.
"""
from __future__ import annotations

import json
import sys
import time

from openprogram.interfaces.cli import worker_client

_NO_REMOTE_ORIGIN = """\
This Web server only listens on this computer, so another device cannot open it.
Make it reachable first, then pair again:
  - same network or VPN (Tailscale): set web.host and web.allowed_origins,
    for example  openprogram config set web.host 0.0.0.0
  - your own HTTPS proxy: add its Origin to web.allowed_origins
Then restart:  openprogram worker restart
Or pass --base-url with an Origin the server already accepts."""


def _print_qr(payload: str) -> bool:
    try:
        import qrcode
    except ImportError:
        return False
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True


def cmd_web_pair(base_url: str | None, label: str | None) -> int:
    from openprogram.host.security.backend_endpoint import (
        OwnerAuthError,
        canonicalize_origin,
    )

    endpoint = worker_client.require_backend_endpoint()
    body = {"label": label} if label else {}
    status, payload = worker_client.request(endpoint, "POST", "/api/auth/pairings", body)
    if status != 200 or not isinstance(payload, dict):
        print(f"error: the worker refused to create a pairing ({status})", file=sys.stderr)
        return 1
    origins = payload.get("origins") or []
    if base_url:
        try:
            origin = canonicalize_origin(base_url)
        except OwnerAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        from openprogram.host.security.backend_endpoint import read_active_web_access

        if origin not in read_active_web_access().effective_origins:
            print(f"error: {origin} is not an Origin this Web server accepts", file=sys.stderr)
            return 1
    elif origins:
        origin = origins[0]
    else:
        print(_NO_REMOTE_ORIGIN, file=sys.stderr)
        return 1

    url = f"{origin}/#pair={payload['code']}"
    minutes = max(1, round((float(payload["expires_at"]) - time.time()) / 60))
    print("Scan with the other device's camera, or open the link there.")
    print()
    _print_qr(url)
    print(url)
    print()
    print(f"Works once, for {minutes} minutes. Treat it like a password.")
    print("Paired devices stay signed in; see or revoke them with `openprogram web devices`.")
    return 0


def _ago(timestamp: float) -> str:
    seconds = max(0, int(time.time() - float(timestamp)))
    if seconds < 90:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def cmd_web_devices(verb: str | None, device_id: str | None, *, as_json: bool) -> int:
    endpoint = worker_client.require_backend_endpoint()
    if verb == "revoke":
        status, _payload = worker_client.request(
            endpoint, "DELETE", f"/api/auth/devices/{device_id}"
        )
        if status == 204:
            print(f"Revoked {device_id}.")
            return 0
        if status == 404:
            print(f"error: no paired device {device_id}", file=sys.stderr)
            return 1
        print(f"error: the worker refused ({status})", file=sys.stderr)
        return 1

    status, payload = worker_client.request(endpoint, "GET", "/api/auth/devices")
    if status != 200 or not isinstance(payload, dict):
        print(f"error: the worker refused ({status})", file=sys.stderr)
        return 1
    devices = payload.get("devices") or []
    if as_json:
        print(json.dumps(devices, indent=2))
        return 0
    if not devices:
        print("No paired devices. Pair one with `openprogram web pair`.")
        return 0
    width = max(5, max(len(d["label"]) for d in devices))
    print(f"{'ID':<16}  {'NAME':<{width}}  LAST USED")
    for device in devices:
        print(f"{device['id']:<16}  {device['label']:<{width}}  {_ago(device['last_used_at'])}")
    return 0

"""JSON requests from CLI commands to the running worker."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any, Optional


def require_backend_endpoint():
    """Resolve the challenge-verified backend endpoint, or exit.

    The endpoint carries the owner token, so it is only produced after the
    listener proves, through the nonce/HMAC challenge, that it belongs to
    this profile (see ``backend_endpoint.resolve_backend_endpoint``).
    """
    from openprogram.host.security.backend_endpoint import (
        OwnerAuthError,
        resolve_backend_endpoint,
    )

    try:
        return resolve_backend_endpoint()
    except OwnerAuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Start it with: openprogram worker start", file=sys.stderr)
        sys.exit(1)


def request(endpoint, method: str, path: str,
            body: Optional[dict] = None, *, exit_if_unreachable: bool = True) -> tuple[int, Any]:
    """Send one JSON request to ``endpoint``; return ``(status, payload)``.

    An HTTP error answers with its status and body; an unreachable
    worker exits with a message, or answers ``(0, None)`` when
    ``exit_if_unreachable`` is false (a command that can do without it).
    """
    url = endpoint.base_url + path
    data: Optional[bytes] = None
    headers = {
        "Accept": "application/json",
        "Authorization": endpoint.authorization_header,
        "Host": endpoint.host,
        "X-Forwarded-Proto": endpoint.scheme,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=120) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"detail": text}
        return e.code, payload
    except urllib.error.URLError as e:
        if not exit_if_unreachable:
            return 0, None
        print(f"Error contacting worker at {url}: {e}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError):
        # A timeout or an unreadable answer; callers that must have the
        # worker keep seeing it as before.
        if not exit_if_unreachable:
            return 0, None
        raise

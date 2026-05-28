"""Shared HTTP helpers for web_search provider implementations.

Every provider was duplicating the same urllib request + HTTPError
unwrap pattern (read the error body so the agent sees the upstream's
message instead of a bare ``HTTP 401``). That's ~15 lines per file ×
16 providers = 240 lines of the same try/except scaffolding.

The two helpers here cover the request shapes we actually use:

  * ``get_json(url, headers, params=None, timeout=…)`` — GET ``url``
    (with an optional query-string dict appended), parse JSON, raise a
    ``ProviderHTTPError`` on non-2xx with the upstream body included.
  * ``post_json(url, headers, body=None, timeout=…)`` — same idea for
    POST + JSON body.

Both keep ``urllib.error.HTTPError``'s status code on the raised
exception so caller-level retry logic (and the provider's own error
message format) can still branch on it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ProviderHTTPError(RuntimeError):
    """HTTP error from a search provider — wraps the upstream status +
    body so the agent sees what the API actually said. Use the helpers
    below instead of catching ``urllib.error.HTTPError`` directly.
    """

    def __init__(self, provider_label: str, status: int, body: str) -> None:
        self.provider = provider_label
        self.status = status
        self.body = body
        # Truncate the body in the str() so the agent prompt doesn't
        # balloon when a provider returns a 1MB HTML error page.
        super().__init__(f"{provider_label} HTTP {status}: {body[:300]}")


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
    provider_label: str = "provider",
) -> Any:
    """GET ``url`` with optional query params + headers, parse JSON.

    Raises ``ProviderHTTPError`` on non-2xx with the upstream body
    included. Network-level failures (DNS, connection refused, …) get
    re-raised as ``RuntimeError`` with the same provider label.
    """
    if params:
        sep = "&" if ("?" in url) else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers or {})
    return _execute(req, timeout=timeout, provider_label=provider_label)


def post_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20.0,
    provider_label: str = "provider",
) -> Any:
    """POST ``url`` with a JSON body, parse the JSON response.

    Auto-sets ``Content-Type: application/json`` when ``body`` is given
    and the caller didn't already provide one.
    """
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return _execute(req, timeout=timeout, provider_label=provider_label)


def get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 25.0,
    provider_label: str = "provider",
) -> bytes:
    """GET ``url`` and return the raw response body — for providers that
    return XML / RSS / Atom (ArXiv) rather than JSON. Same error-
    unwrapping semantics as ``get_json``.
    """
    if params:
        sep = "&" if ("?" in url) else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ProviderHTTPError(provider_label, e.code, _safe_body(e)) from e


def _execute(
    req: urllib.request.Request,
    *,
    timeout: float,
    provider_label: str,
) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ProviderHTTPError(provider_label, e.code, _safe_body(e)) from e
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _safe_body(e: urllib.error.HTTPError) -> str:
    """Best-effort decode of an HTTPError's body without raising."""
    try:
        return e.read().decode("utf-8", errors="replace")
    except Exception:
        return str(e)

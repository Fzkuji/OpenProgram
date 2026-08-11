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

from typing import Any

import httpx

from openprogram.security import safe_http
from openprogram.security.url_policy import OwnerURLException, normalize_origin


class ProviderHTTPError(RuntimeError):
    """HTTP error reduced to a stable status and normalized origin."""

    def __init__(self, provider_label: str, status: int, url: str) -> None:
        self.provider = provider_label
        self.status = status
        self.body = ""
        super().__init__(
            f"{provider_label} HTTP {status} for {normalize_origin(url)}"
        )


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
    provider_label: str = "provider",
    configured_url: str | None = None,
    consumer: str = "tool.web_search.fixed_api",
) -> Any:
    """GET ``url`` with optional query params + headers, parse JSON.

    Raises ``ProviderHTTPError`` on non-2xx. Network failures are reduced to
    their stable exception type and normalized origin.
    """
    return _execute(
        "GET",
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        provider_label=provider_label,
        configured_url=configured_url,
        consumer=consumer,
    )


def post_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20.0,
    provider_label: str = "provider",
    configured_url: str | None = None,
    consumer: str = "tool.web_search.fixed_api",
) -> Any:
    """POST ``url`` with a JSON body, parse the JSON response.

    Auto-sets ``Content-Type: application/json`` when ``body`` is given
    and the caller didn't already provide one.
    """
    return _execute(
        "POST",
        url,
        headers=headers,
        json=body,
        timeout=timeout,
        provider_label=provider_label,
        configured_url=configured_url,
        consumer=consumer,
    )


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
    client = safe_http.safe_client("tool.web_search.fixed_api")
    try:
        with client:
            response = client.get(
                url, headers=headers or {}, params=params, timeout=timeout
            )
            _raise_status(response, provider_label)
            return response.content
    except httpx.RequestError as exc:
        raise _safe_request_error(provider_label, url, exc) from None


def _execute(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    timeout: float,
    provider_label: str,
    configured_url: str | None,
    consumer: str,
    **kwargs,
) -> Any:
    client = (
        safe_http.configured_safe_client(
            "tool.web_search.configured_api",
            configured_url,
            owner_exception=OwnerURLException(
                consumer="tool.web_search.configured_api",
                origin=normalize_origin(configured_url),
            ),
        )
        if configured_url is not None
        else safe_http.safe_client(consumer)
    )
    try:
        with client:
            send = client.get if method == "GET" else client.post
            response = send(url, headers=headers or {}, timeout=timeout, **kwargs)
            _raise_status(response, provider_label)
    except httpx.RequestError as exc:
        raise _safe_request_error(provider_label, url, exc) from None
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{provider_label} {type(exc).__name__} for {normalize_origin(url)}"
        ) from None


def _raise_status(response: httpx.Response, provider_label: str) -> None:
    if response.status_code >= 400:
        raise ProviderHTTPError(
            provider_label, response.status_code, str(response.url)
        )


def _safe_request_error(
    provider_label: str, url: str, exc: httpx.RequestError
) -> RuntimeError:
    return RuntimeError(
        f"{provider_label} {type(exc).__name__} for {normalize_origin(url)}"
    )

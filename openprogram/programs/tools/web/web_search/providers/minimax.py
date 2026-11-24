"""MiniMax Coding Plan web search provider.

Structured results via MiniMax's ``/v1/coding_plan/search`` endpoint
(the search backend that powers MiniMax's coding agents). Auth is a
bearer token from a MiniMax Coding Plan subscription. Two regional
endpoints exist — global (``api.minimax.io``) and CN
(``api.minimaxi.com``) — auto-selected by host inference on the
``MINIMAX_API_HOST`` env var, else the global one first and the China one
when the global one refuses the key.

The key is MiniMax's (``services.service_key("minimax-search")``): the keys
stored for the company, else ``MINIMAX_CODE_PLAN_KEY``,
``MINIMAX_CODING_API_KEY``, ``MINIMAX_API_KEY``.

Docs: https://docs.openclaw.ai/tools/minimax-search
Signup: https://platform.minimax.io/user-center/basic-information/interface-key
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

from openprogram.models.auth.services import service_key

from .._http import ProviderHTTPError, post_json
from ..registry import SearchResult


API_URL_GLOBAL = "https://api.minimax.io/v1/coding_plan/search"
API_URL_CN = "https://api.minimaxi.com/v1/coding_plan/search"
TIMEOUT = 20.0
_KEY_ENV_VARS = ("MINIMAX_CODE_PLAN_KEY", "MINIMAX_CODING_API_KEY", "MINIMAX_API_KEY")
#: MiniMax's answers to a key it does not know (in an HTTP 200 envelope).
_AUTH_CODES = {1004, 2049}
#: The address a key last worked at, so later searches go there first.
_worked_at: dict[str, str] = {}


def _resolve_api_key() -> str:
    """MiniMax's key (``services.service_key``): the company's stored keys
    first, then the variables."""
    return service_key("minimax-search")


def _endpoints() -> list[str]:
    """Where to send a search, in order. A key is MiniMax's, not a region's:
    a China key is refused at the global address and works at the China one,
    so the other address is tried when the first refuses the key."""
    host_override = os.environ.get("MINIMAX_API_HOST", "")
    if host_override:
        try:
            hostname = urllib.parse.urlparse(host_override).hostname or ""
        except Exception:
            hostname = ""
        if hostname.endswith("minimaxi.com") or "minimaxi.com" in host_override:
            return [API_URL_CN]
        return [API_URL_GLOBAL]
    return [API_URL_GLOBAL, API_URL_CN]


@dataclass
class MiniMaxProvider:
    name: str = "minimax"
    priority: int = 65
    # ProviderBase.is_available() ANDs every env var; for MiniMax we want
    # OR semantics (any one of three keys). Override below.
    requires_env: tuple = ("MINIMAX_CODE_PLAN_KEY",)

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def unavailable_reason(self) -> str:
        return f"not configured (needs one of {' / '.join(_KEY_ENV_VARS)})"

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = _resolve_api_key()
        if not key:
            raise RuntimeError(
                "MiniMax web_search needs a MiniMax key: add one on the MiniMax "
                "provider, or set MINIMAX_API_KEY."
            )
        endpoints = _endpoints()
        tail = key[-8:]
        if _worked_at.get(tail) in endpoints:
            endpoints.remove(_worked_at[tail])
            endpoints.insert(0, _worked_at[tail])
        data: dict = {}
        for i, endpoint in enumerate(endpoints):
            last = i == len(endpoints) - 1
            try:
                data = post_json(
                    endpoint,
                    body={"q": query},
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    }, timeout=TIMEOUT, provider_label="MiniMax",
                )
            except ProviderHTTPError as exc:
                if exc.status in (401, 403) and not last:
                    continue
                raise
            # MiniMax returns a base_resp envelope even on HTTP 200.
            base_resp = data.get("base_resp") or {}
            status_code = base_resp.get("status_code")
            if status_code in _AUTH_CODES and not last:
                continue
            if status_code and status_code != 0:
                if isinstance(status_code, int) and not isinstance(status_code, bool):
                    raise RuntimeError(f"MiniMax API error ({status_code})") from None
                raise RuntimeError("MiniMax API error") from None
            _worked_at[tail] = endpoint
            break

        limit = max(1, min(int(num_results), 20))
        organic = data.get("organic") or []
        related = [
            str(r.get("query", ""))
            for r in (data.get("related_searches") or [])
            if r.get("query")
        ]
        results: list[SearchResult] = []
        for r in organic[:limit]:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("link", "")),
                snippet=str(r.get("snippet", "")),
                extras={
                    "published": r.get("date"),
                    "related_searches": related or None,
                },
            ))
        return results

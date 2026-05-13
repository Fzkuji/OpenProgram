"""MiniMax Coding Plan web search provider.

Structured results via MiniMax's ``/v1/coding_plan/search`` endpoint
(the search backend that powers MiniMax's coding agents). Auth is a
bearer token from a MiniMax Coding Plan subscription. Two regional
endpoints exist — global (``api.minimax.io``) and CN
(``api.minimaxi.com``) — auto-selected by host inference on the
``MINIMAX_API_HOST`` env var.

Env keys checked, in order: ``MINIMAX_CODE_PLAN_KEY``,
``MINIMAX_CODING_API_KEY``, ``MINIMAX_API_KEY``.

Docs: https://docs.openclaw.ai/tools/minimax-search
Signup: https://platform.minimax.io/user-center/basic-information/interface-key
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL_GLOBAL = "https://api.minimax.io/v1/coding_plan/search"
API_URL_CN = "https://api.minimaxi.com/v1/coding_plan/search"
TIMEOUT = 20.0
_KEY_ENV_VARS = ("MINIMAX_CODE_PLAN_KEY", "MINIMAX_CODING_API_KEY", "MINIMAX_API_KEY")


def _resolve_api_key() -> str:
    for var in _KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def _resolve_endpoint() -> str:
    # If the user pointed at minimaxi.com (CN host) anywhere, pick CN.
    host_override = os.environ.get("MINIMAX_API_HOST", "")
    if host_override:
        try:
            hostname = urllib.parse.urlparse(host_override).hostname or ""
        except Exception:
            hostname = ""
        if hostname.endswith("minimaxi.com") or "minimaxi.com" in host_override:
            return API_URL_CN
    return API_URL_GLOBAL


@dataclass
class MiniMaxProvider:
    name: str = "minimax"
    priority: int = 65
    # ProviderBase.is_available() ANDs every env var; for MiniMax we want
    # OR semantics (any one of three keys). Override below.
    requires_env: tuple = ("MINIMAX_CODE_PLAN_KEY",)

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = _resolve_api_key()
        if not key:
            raise RuntimeError(
                "MiniMax web_search needs MINIMAX_CODE_PLAN_KEY, "
                "MINIMAX_CODING_API_KEY, or MINIMAX_API_KEY in the environment."
            )
        endpoint = _resolve_endpoint()
        payload = json.dumps({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"MiniMax HTTP {e.code}: {body}") from e

        # MiniMax returns a base_resp envelope even on HTTP 200.
        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code and status_code != 0:
            msg = base_resp.get("status_msg", "unknown error")
            raise RuntimeError(f"MiniMax API error ({status_code}): {msg}")

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

"""Ollama experimental web search provider.

Calls the experimental ``/api/experimental/web_search`` endpoint
exposed by an Ollama host. Two deployment modes:

1. **Local Ollama** — talk to ``http://127.0.0.1:11434`` (or whatever
   the user pointed ``OLLAMA_BASE_URL`` at). No API key required, but
   the host itself needs ``ollama signin`` to upstream the query to
   Ollama's cloud-backed search.
2. **Ollama Cloud** — talk to ``https://ollama.com`` directly with an
   ``OLLAMA_API_KEY`` bearer token.

Availability heuristic: this provider is *always* considered available
because we can't probe the local socket cheaply at registration time;
errors at search() time become RuntimeError with a clear message.
If you don't want it to win the auto-select, set
``OPENPROGRAM_WEBSEARCH_DISABLE=ollama``.

Docs: https://ollama.com/
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
CLOUD_BASE_URL = "https://ollama.com"
SEARCH_PATH = "/api/experimental/web_search"
TIMEOUT = 15.0
SNIPPET_MAX_CHARS = 300


def _resolve_base_url() -> str:
    raw = (os.environ.get("OLLAMA_BASE_URL") or "").strip()
    if not raw:
        # If the user only set OLLAMA_API_KEY, assume cloud.
        if os.environ.get("OLLAMA_API_KEY", "").strip():
            return CLOUD_BASE_URL
        return DEFAULT_BASE_URL
    return raw.rstrip("/")


def _resolve_api_key() -> str:
    return (os.environ.get("OLLAMA_API_KEY") or "").strip()


@dataclass
class OllamaProvider:
    name: str = "ollama"
    priority: int = 55
    # No env var is strictly required — local Ollama works without auth.
    # Cloud usage benefits from OLLAMA_API_KEY but we don't enforce it
    # via requires_env because that would gate is_available().
    requires_env: tuple = ()

    def is_available(self) -> bool:
        # We can't ping the local socket without paying a syscall on
        # every catalog read; report available and let search() raise
        # if Ollama isn't actually running.
        return True

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        base_url = _resolve_base_url()
        url = f"{base_url}{SEARCH_PATH}"
        count = max(1, min(int(num_results), 10))
        payload = json.dumps({"query": query, "max_results": count}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = _resolve_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            if e.code == 401:
                raise RuntimeError(
                    "Ollama web search auth failed (401). Run `ollama signin` "
                    "on the host or set OLLAMA_API_KEY."
                ) from e
            if e.code == 403:
                raise RuntimeError(
                    "Ollama web search unavailable (403). Ensure cloud-backed "
                    "web search is enabled on the Ollama host."
                ) from e
            raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama web search could not reach {base_url}: {e.reason}. "
                "Is Ollama running?"
            ) from e

        results: list[SearchResult] = []
        for r in (data.get("results") or [])[:count]:
            if not isinstance(r, dict):
                continue
            hit_url = str(r.get("url", "")).strip()
            if not hit_url:
                continue
            content = str(r.get("content", ""))
            snippet = content[:SNIPPET_MAX_CHARS]
            if len(content) > SNIPPET_MAX_CHARS:
                snippet += "…"
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=hit_url,
                snippet=snippet,
            ))
        return results

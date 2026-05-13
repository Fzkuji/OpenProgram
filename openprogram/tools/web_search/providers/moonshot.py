"""Moonshot / Kimi web search provider.

Kimi (Moonshot's assistant) exposes web search via a *native tool call*
on the chat-completions endpoint: ask the model with a single user
message, advertise the builtin ``$web_search`` function, then run a
tool-call loop where Kimi calls $web_search internally and the server
returns ``search_results[]`` in the response envelope.

This port mirrors the OpenClaw TypeScript implementation (up to 3
tool-call rounds), extracting hit metadata from ``search_results`` plus
URLs from parsed tool-call arguments. Compared with direct SERP
providers (Brave/Google/Tavily) Kimi returns AI-synthesised snippets,
not raw web extracts — useful when you want an answer-style response
with citations.

Env keys: ``KIMI_API_KEY`` or ``MOONSHOT_API_KEY``.
Endpoint: ``https://api.moonshot.ai/v1/chat/completions`` (global) or
``https://api.moonshot.cn/v1/chat/completions`` (CN, set via
``MOONSHOT_BASE_URL``).

Docs: https://platform.moonshot.cn/
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_MODEL = "kimi-k2-0905-preview"
THINKING_DISABLED_MODELS = {"kimi-k2.5"}
TIMEOUT = 30.0
MAX_ROUNDS = 3
_KEY_ENV_VARS = ("KIMI_API_KEY", "MOONSHOT_API_KEY")
_WEB_SEARCH_TOOL = {
    "type": "builtin_function",
    "function": {"name": "$web_search"},
}


def _resolve_api_key() -> str:
    for var in _KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def _resolve_base_url() -> str:
    raw = os.environ.get("MOONSHOT_BASE_URL", "").strip() or DEFAULT_BASE_URL
    return raw.rstrip("/")


def _resolve_model() -> str:
    return os.environ.get("MOONSHOT_SEARCH_MODEL", "").strip() or DEFAULT_MODEL


def _post(url: str, body: dict, key: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        raise RuntimeError(f"Kimi HTTP {e.code}: {detail}") from e


@dataclass
class MoonshotProvider:
    name: str = "moonshot"
    priority: int = 60
    # ProviderBase ANDs requires_env; we want OR — override is_available.
    requires_env: tuple = ("KIMI_API_KEY",)

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = _resolve_api_key()
        if not key:
            raise RuntimeError(
                "Kimi/Moonshot web_search needs KIMI_API_KEY or MOONSHOT_API_KEY."
            )
        base_url = _resolve_base_url()
        model = _resolve_model()
        endpoint = f"{base_url}/chat/completions"

        messages: list[dict] = [{"role": "user", "content": query}]
        citations_seen: list[str] = []
        last_search_results: list[dict] = []
        last_content = ""

        for _ in range(MAX_ROUNDS):
            body: dict = {
                "model": model,
                "messages": messages,
                "tools": [_WEB_SEARCH_TOOL],
            }
            if model in THINKING_DISABLED_MODELS:
                body["thinking"] = {"type": "disabled"}

            data = _post(endpoint, body, key)

            # Top-level search_results show up once Kimi has actually
            # called $web_search — accumulate across rounds.
            for entry in data.get("search_results") or []:
                if isinstance(entry, dict):
                    last_search_results.append(entry)
                    url = (entry.get("url") or "").strip()
                    if url and url not in citations_seen:
                        citations_seen.append(url)

            choices = data.get("choices") or []
            if not choices:
                break
            choice = choices[0] or {}
            message = choice.get("message") or {}
            content = (message.get("content") or "").strip()
            if content:
                last_content = content
            tool_calls = message.get("tool_calls") or []

            # Also harvest URLs from tool-call arguments (some Kimi
            # variants surface citations there).
            for call in tool_calls:
                raw_args = ((call or {}).get("function") or {}).get("arguments")
                if not isinstance(raw_args, str) or not raw_args.strip():
                    continue
                try:
                    parsed = json.loads(raw_args)
                except Exception:
                    continue
                parsed_url = (parsed.get("url") or "").strip()
                if parsed_url and parsed_url not in citations_seen:
                    citations_seen.append(parsed_url)
                for sub in parsed.get("search_results") or []:
                    if not isinstance(sub, dict):
                        continue
                    last_search_results.append(sub)
                    u = (sub.get("url") or "").strip()
                    if u and u not in citations_seen:
                        citations_seen.append(u)

            finish = choice.get("finish_reason")
            if finish != "tool_calls" or not tool_calls:
                break

            # Echo assistant message back, then a tool message per call,
            # so Kimi can produce the next reasoning round.
            assistant_msg = {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
            if message.get("reasoning_content"):
                assistant_msg["reasoning_content"] = message["reasoning_content"]
            messages.append(assistant_msg)

            pushed = False
            for call in tool_calls:
                call_id = ((call or {}).get("id") or "").strip()
                name = (((call or {}).get("function") or {}).get("name") or "").strip()
                args_str = (((call or {}).get("function") or {}).get("arguments") or "")
                if not call_id or not name or not args_str.strip():
                    continue
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": args_str,
                })
                pushed = True
            if not pushed:
                break

        limit = max(1, min(int(num_results), 20))
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for entry in last_search_results:
            url = (entry.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(SearchResult(
                title=str(entry.get("title", "")),
                url=url,
                snippet=str(entry.get("content", "") or entry.get("snippet", "")),
                extras={"model": model},
            ))
            if len(results) >= limit:
                break

        # If Kimi answered without populating search_results (e.g. the
        # model decided not to call the tool), surface the synthesised
        # answer as a single result with no URL so callers still see
        # something to use.
        if not results and last_content:
            results.append(SearchResult(
                title=f"Kimi answer for: {query}",
                url="",
                snippet=last_content,
                extras={"model": model, "citations": citations_seen or None},
            ))
        return results

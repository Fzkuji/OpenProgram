"""
OpenAICodexRuntime — HTTP-direct provider for the ChatGPT/Codex subscription.

Single route:
    URL:   https://chatgpt.com/backend-api/codex/responses
    Auth:  Bearer <OAuth access_token> + chatgpt-account-id header
    Creds: ~/.codex/auth.json (must have auth_mode=chatgpt),
           auto-refreshed against https://auth.openai.com/oauth/token
           when the access token is close to expiring.

This provider is subscription-only. API-key access to OpenAI models goes
through OpenAIRuntime (openprogram.legacy_providers.openai), which hits
api.openai.com with OPENAI_API_KEY. The two are intentionally separate:
use `openai-codex` when you want to burn ChatGPT subscription credit,
use `openai` when you want to burn API credit.

No codex CLI subprocess is involved at call time. The codex CLI is only
needed once, for `codex login --device-auth`, to create auth.json.

Protocol + OAuth parameters translated from @mariozechner/pi-ai
(packages/ai/src/providers/openai-codex-responses.ts and
packages/ai/src/utils/oauth/openai-codex.ts, MIT license).

Usage:
    from openprogram.legacy_providers.openai_codex import OpenAICodexRuntime

    rt = OpenAICodexRuntime(model="gpt-5.4-mini")
    reply = rt.exec(content=[{"type": "text", "text": "hi"}])
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from openprogram.agentic_programming.runtime import Runtime


# ----------------------------------------------------------------------------
# Protocol constants (from pi-ai)
# ----------------------------------------------------------------------------

CHATGPT_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
JWT_CLAIM_PATH = "https://api.openai.com/auth"

ORIGINATOR = "openprogram"

# Known Codex-route model ids as of 2026-04. Curated from OpenClaw's catalog
# (augmentModelCatalog + resolveCodexForwardCompatModel). The actual set
# available to a specific ChatGPT account depends on subscription tier.
_KNOWN_CODEX_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-pro",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
]


# ----------------------------------------------------------------------------
# auth.json reading + JWT decoding
# ----------------------------------------------------------------------------

def _codex_home() -> Path:
    """Honor $CODEX_HOME, default ~/.codex (matches Codex CLI)."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    if not configured:
        return Path.home() / ".codex"
    if configured == "~":
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    return Path(configured).resolve()


def _auth_path() -> Path:
    return _codex_home() / "auth.json"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload section (second segment)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT: not 3 segments")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _extract_account_id(access_token: str) -> str:
    """JWT.auth.chatgpt_account_id — required for chatgpt-account-id header."""
    payload = _decode_jwt_payload(access_token)
    auth = payload.get(JWT_CLAIM_PATH) or {}
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError("JWT has no chatgpt_account_id — re-run `codex login --device-auth`")
    return account_id.strip()


def _jwt_expiry_epoch(access_token: str) -> Optional[int]:
    try:
        exp = _decode_jwt_payload(access_token).get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# OAuth token refresh (when access_token is close to expiring)
# ----------------------------------------------------------------------------

def _refresh_oauth_token(refresh_token: str, timeout: float = 30.0) -> dict[str, Any]:
    """POST to auth.openai.com/oauth/token, return new {access_token, refresh_token, expires_in}."""
    r = httpx.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OAuth refresh failed {r.status_code}: {r.text[:200]}")
    data = r.json()
    for k in ("access_token", "refresh_token", "expires_in"):
        if k not in data:
            raise RuntimeError(f"OAuth refresh response missing {k!r}")
    return data


def _write_auth_json_atomic(data: dict[str, Any]) -> None:
    """Rewrite ~/.codex/auth.json atomically (same dir rename)."""
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Credential resolution (what token do we use, and against which endpoint)
# ----------------------------------------------------------------------------

class _Credentials:
    """Resolved ChatGPT OAuth credentials for one call."""

    def __init__(self, token: str, account_id: str):
        self.token = token
        self.account_id = account_id


class _AuthState:
    """Cached ~/.codex/auth.json (chatgpt mode) + refresh logic.

    Shared across calls from the same runtime. Thread-safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._auth: Optional[dict[str, Any]] = None

    def _load(self) -> dict[str, Any]:
        path = _auth_path()
        if not path.exists():
            raise RuntimeError(
                f"{path} not found. OpenAICodexRuntime requires the ChatGPT "
                "subscription. Run: codex login --device-auth"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def resolve(self) -> _Credentials:
        """Return the current ChatGPT OAuth credentials, refreshing if near expiry.

        This provider is **subscription-only** — it never falls back to
        OPENAI_API_KEY. If you want to use API billing, use OpenAIRuntime
        (the `openai` provider) instead.
        """
        with self._lock:
            if self._auth is None:
                self._auth = self._load()
            auth = self._auth

            if auth.get("auth_mode") != "chatgpt":
                raise RuntimeError(
                    f"{_auth_path()} has auth_mode={auth.get('auth_mode')!r}, "
                    "need 'chatgpt'. OpenAICodexRuntime is subscription-only. "
                    "Run: codex login --device-auth   "
                    "(For API-key access, use the `openai` provider instead.)"
                )

            tokens = auth.get("tokens") or {}
            access = tokens.get("access_token")
            refresh = tokens.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError(
                    f"{_auth_path()} is in chatgpt mode but missing access_token "
                    "or refresh_token. Run: codex login --device-auth"
                )

            exp = _jwt_expiry_epoch(access)
            if exp is not None and exp - 60 < time.time():
                new_tokens = _refresh_oauth_token(refresh)
                access = new_tokens["access_token"]
                auth["tokens"]["access_token"] = access
                auth["tokens"]["refresh_token"] = new_tokens["refresh_token"]
                if "id_token" in new_tokens:
                    auth["tokens"]["id_token"] = new_tokens["id_token"]
                _write_auth_json_atomic(auth)
                self._auth = auth

            account_id = tokens.get("account_id") or _extract_account_id(access)
            return _Credentials(access, account_id)


# ----------------------------------------------------------------------------
# Request construction
# ----------------------------------------------------------------------------

def _convert_content_to_input(content: list[dict]) -> list[dict]:
    """OpenProgram content blocks → Responses API `input` (single user message).

    Supported block types:
      {"type": "text", "text": "..."}
      {"type": "image", "path": "..."}                (read + base64-encode)
      {"type": "image", "data": "...", "media_type": "image/png"}  (pre-encoded)
    """
    parts: list[dict] = []
    for block in content:
        btype = block.get("type", "text")
        if btype == "text":
            parts.append({"type": "input_text", "text": block.get("text", "")})
        elif btype == "image":
            if "data" in block:
                mt = block.get("media_type", "image/png")
                parts.append({
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mt};base64,{block['data']}",
                })
            elif "path" in block:
                path = block["path"]
                mt = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                parts.append({
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mt};base64,{b64}",
                })
        # Silently skip unknown block types for now (audio/video/file)
    return [{"role": "user", "content": parts}]


def _build_headers(creds: _Credentials) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {creds.token}",
        "chatgpt-account-id": creds.account_id,
        "originator": ORIGINATOR,
        "User-Agent": f"openprogram ({sys.platform})",
        "content-type": "application/json",
        "accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
    }


def _to_openai_tool_spec(spec: dict) -> dict:
    """Normalize a neutral tool spec to the OpenAI Responses API shape.

    Accepts either the OpenAI shape (already wrapped in `type: function`) or a
    neutral one: {name, description, parameters}. Extra fields are passed
    through.
    """
    if spec.get("type") == "function":
        return spec
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec.get("description", ""),
        "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        **{k: v for k, v in spec.items() if k not in ("name", "description", "parameters")},
    }


def _build_body(
    model: str,
    content: Optional[list[dict]],
    instructions: Optional[str],
    reasoning_effort: Optional[str],
    response_format: Optional[dict],
    input_items: Optional[list[dict]] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: Any = "auto",
    parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    # input_items wins over content: tool-loop rounds pass the full growing
    # transcript (user msg + function_call + function_call_output + ...)
    if input_items is None:
        if content is None:
            raise ValueError("_build_body needs either content or input_items")
        input_items = _convert_content_to_input(content)

    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        # ChatGPT backend rejects bodies without `instructions` (HTTP 400
        # "Instructions are required"). Send a minimal placeholder when the
        # caller didn't supply a system prompt so the request still validates.
        "instructions": instructions or "You are a helpful assistant.",
        "input": input_items,
        "tool_choice": tool_choice,
        "parallel_tool_calls": parallel_tool_calls,
    }
    if tools:
        body["tools"] = [_to_openai_tool_spec(t) for t in tools]
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    if response_format:
        # Nudge model toward a JSON schema reply — approximation of what
        # OpenAI's "structured outputs" does. Actual schema enforcement
        # happens in the Responses API via text.format for strict mode.
        body.setdefault("text", {})["format"] = {
            "type": "json_schema",
            "name": response_format.get("name", "response"),
            "schema": response_format.get("schema", response_format),
            "strict": True,
        }
    return body


# ----------------------------------------------------------------------------
# SSE parsing — accumulate response.output_text.delta, stop at completed/failed
# ----------------------------------------------------------------------------

def _parse_sse_response(response: httpx.Response) -> dict[str, Any]:
    """Consume the SSE stream. Return {text, tool_calls, usage}.

    tool_calls: list of {call_id, name, arguments} dicts, arguments is a raw
    JSON string exactly as emitted by the model (decoded by the caller).
    """
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    # output_index -> partial function_call item
    fcalls: dict[int, dict[str, str]] = {}
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        if raw_line.startswith("data: "):
            payload = raw_line[6:]
        elif raw_line.startswith("data:"):
            payload = raw_line[5:]
        else:
            continue  # skip "event:", comments, etc.

        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        if etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                text_parts.append(delta)

        elif etype == "response.output_text.done":
            # Final assembled text for one output block — prefer it over
            # accumulated deltas when present (avoids split tokens).
            full = event.get("text")
            if isinstance(full, str) and full:
                text_parts = [full]

        elif etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                idx = event.get("output_index")
                fcalls[idx] = {
                    "call_id": item.get("call_id") or "",
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "",
                }

        elif etype == "response.function_call_arguments.delta":
            idx = event.get("output_index")
            if idx in fcalls:
                fcalls[idx]["arguments"] += event.get("delta", "")

        elif etype == "response.function_call_arguments.done":
            idx = event.get("output_index")
            if idx in fcalls and "arguments" in event:
                # Final assembled arguments string — authoritative over deltas
                fcalls[idx]["arguments"] = event["arguments"]

        elif etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                idx = event.get("output_index")
                fcalls[idx] = {
                    "call_id": item.get("call_id") or fcalls.get(idx, {}).get("call_id", ""),
                    "name": item.get("name") or fcalls.get(idx, {}).get("name", ""),
                    "arguments": item.get("arguments") or fcalls.get(idx, {}).get("arguments", ""),
                }

        elif etype in ("response.completed", "response.done", "response.incomplete"):
            resp = event.get("response") or {}
            usage = resp.get("usage") or usage
            # Grab final assembled items from response.output — authoritative
            output_items = resp.get("output") or []
            collected_text: list[str] = []
            collected_calls: list[dict[str, str]] = []
            for item in output_items:
                itype = item.get("type")
                if itype == "message":
                    for block in item.get("content") or []:
                        if block.get("type") == "output_text":
                            t = block.get("text")
                            if isinstance(t, str):
                                collected_text.append(t)
                elif itype == "function_call":
                    collected_calls.append({
                        "call_id": item.get("call_id") or "",
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    })
            if collected_text:
                text_parts = collected_text
            if collected_calls:
                fcalls = {i: c for i, c in enumerate(collected_calls)}
            break

        elif etype == "response.failed":
            err = (event.get("response") or {}).get("error") or {}
            raise RuntimeError(f"Codex failed: {err.get('message') or err.get('code') or event}")

        elif etype == "error":
            raise RuntimeError(f"Codex error: {event.get('message') or event.get('code') or event}")

    tool_calls = [fcalls[i] for i in sorted(fcalls.keys())]
    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "usage": usage,
    }


# ----------------------------------------------------------------------------
# Runtime
# ----------------------------------------------------------------------------

class OpenAICodexRuntime(Runtime):
    """
    Args:
        model:     Default model id (e.g. "gpt-5.4-mini", "gpt-5.4").
        timeout:   HTTP request timeout in seconds (default: 300).
        system:    Optional system prompt, sent as `instructions`.

    Other kwargs are accepted-and-ignored for backward compatibility with
    the pre-HTTP subprocess version (sandbox, full_auto, session_id, ...).
    They don't apply when talking directly to the HTTP API.
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        timeout: int = 300,
        system: Optional[str] = None,
        **ignored_legacy_kwargs: Any,
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.system = system
        self._auth = _AuthState()
        self._client: Optional[httpx.Client] = None
        self._reasoning_effort: Optional[str] = None  # set externally by webui
        self.last_usage: Optional[dict[str, Any]] = None
        self.has_session = False  # we don't manage a server-side session

    # ----- lifecycle ---------------------------------------------------------

    def list_models(self) -> list[str]:
        """Curated Codex-route model ids. Actual access depends on your tier."""
        return list(_KNOWN_CODEX_MODELS)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=30.0))
        return self._client

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        super().close()

    # ----- the call ----------------------------------------------------------

    def _call_once(
        self,
        input_items: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Any = "auto",
        model: Optional[str] = None,
        response_format: Optional[dict] = None,
    ) -> dict[str, Any]:
        """One HTTP round trip. Returns {text, tool_calls, usage}.

        input_items is the growing Responses-API transcript (user msg +
        function_call + function_call_output + ...). Callers building a tool
        loop pass the full list; one-shot callers pass just the user message.
        """
        creds = self._auth.resolve()
        chosen_model = model or self.model
        body = _build_body(
            model=chosen_model,
            content=None,
            instructions=self.system,
            reasoning_effort=self._reasoning_effort,
            response_format=response_format,
            input_items=input_items,
            tools=tools,
            tool_choice=tool_choice,
        )
        headers = _build_headers(creds)

        client = self._get_client()
        with client.stream("POST", CHATGPT_BACKEND_URL, headers=headers, json=body) as r:
            if r.status_code != 200:
                err_body = r.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Codex HTTP {r.status_code} from {CHATGPT_BACKEND_URL}: "
                    f"{err_body[:500]}"
                )
            result = _parse_sse_response(r)

        usage = result.get("usage") or {}
        if usage:
            self.last_usage = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
                "cache_create": 0,
            }
        return result

    def _call(
        self,
        content: list[dict],
        model: str = None,
        response_format: Optional[dict] = None,
    ) -> str:
        input_items = _convert_content_to_input(content)
        result = self._call_once(
            input_items=input_items,
            tools=None,
            model=model,
            response_format=response_format,
        )
        return result["text"]

    # ----- tool-use loop -----------------------------------------------------

    def exec_with_tools(
        self,
        content: list[dict],
        tools: list[Any],
        tool_choice: Any = "auto",
        parallel_tool_calls: bool = True,
        max_iterations: int = 20,
        model: Optional[str] = None,
    ) -> str:
        """Run the LLM with tools available, looping until a text reply.

        tools: list of tool objects or specs. Each tool may be:
          - a neutral spec dict {name, description, parameters}
          - an OpenAI-shape spec {type:"function", name, ...}
          - an object with .spec (dict) and .execute (callable) attributes
          - a dict {"spec": {...}, "execute": callable}

        Unknown tool_calls from the model (name not in our registry) raise;
        provide only tools you actually handle.
        """
        # Normalize to (spec_list, name_to_executor)
        specs: list[dict] = []
        executors: dict[str, Any] = {}
        for t in tools:
            spec, ex = _resolve_tool(t)
            specs.append(spec)
            executors[spec["name"]] = ex

        input_items = _convert_content_to_input(content)

        for _ in range(max_iterations):
            result = self._call_once(
                input_items=input_items,
                tools=specs,
                tool_choice=tool_choice,
                model=model,
            )
            calls = result.get("tool_calls") or []
            if not calls:
                return result.get("text") or ""

            # Append function_call items + their outputs, then loop.
            for tc in calls:
                input_items.append({
                    "type": "function_call",
                    "call_id": tc["call_id"],
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                })
                ex = executors.get(tc["name"])
                if ex is None:
                    output = f"Error: tool {tc['name']!r} is not registered"
                else:
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError as e:
                        output = f"Error: invalid JSON arguments: {e}"
                    else:
                        try:
                            raw_out = ex(**args)
                            output = raw_out if isinstance(raw_out, str) else json.dumps(raw_out, ensure_ascii=False, default=str)
                        except Exception as e:
                            output = f"Error: {type(e).__name__}: {e}"
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tc["call_id"],
                    "output": output,
                })

        raise RuntimeError(f"exec_with_tools exceeded {max_iterations} iterations without a final reply")


def _resolve_tool(t: Any) -> tuple[dict, Any]:
    """Normalize a tool entry to (spec_dict, executor_callable)."""
    if isinstance(t, dict) and "spec" in t and "execute" in t:
        return t["spec"], t["execute"]
    if hasattr(t, "spec") and hasattr(t, "execute"):
        return t.spec, t.execute
    if isinstance(t, dict) and "name" in t:
        # Bare spec with no executor — caller is expected to supply executors separately
        raise ValueError(
            f"Tool {t.get('name')!r} has no executor. Pass a dict {{'spec':..., 'execute':...}} "
            "or an object with .spec and .execute."
        )
    raise TypeError(f"Cannot resolve tool: {t!r}")

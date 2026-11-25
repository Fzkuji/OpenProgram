"""What the three client protocols share on the way in and out.

* Model lookup: a request names its model as an explicit ``provider/model``
  reference (``catalog.models.split_model_ref``); nothing is aliased.
* Reasoning signatures: an upstream's own opaque value (Anthropic's thinking
  signature, a Responses reasoning item) goes to the client wrapped with the
  model that made it, and comes back to that model only. A signature is
  never made up, and a thinking block without one never goes to Anthropic.
* Tool choice and reasoning effort mapped onto ``SimpleStreamOptions``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from openprogram.models.providers.types import (
    AssistantMessage,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)

#: The prefix an OpenProgram model ref may carry in a harness's model list.
REF_PREFIX = "openprogram:"
_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
_SIGNATURE_PREFIX = "opgw1."
#: APIs that read Anthropic's own thinking signatures.
ANTHROPIC_APIS = frozenset({"anthropic-messages"})


class GatewayError(Exception):
    """A request refused before or while it was answered.

    ``kind`` is one of: invalid_request, authentication, permission,
    not_found, rate_limit, overloaded, context_length, api_error. Each
    protocol writes it in its own error shape.
    """

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(12)}"


# Model lookup

def model_ref(model: Model) -> str:
    return f"{model.provider}/{model.id}"


def resolve_model(ref: Any) -> Model:
    """The registered Model ``ref`` names, or ``GatewayError`` 404."""
    if not isinstance(ref, str) or not ref.strip():
        raise GatewayError(400, "invalid_request", "model is required")
    value = ref.strip()
    if value.startswith(REF_PREFIX):
        value = value[len(REF_PREFIX):]
    from openprogram.models.providers.catalog.models import get_model, split_model_ref
    provider, model_id = split_model_ref(value)
    if not provider or not model_id:
        raise GatewayError(404, "not_found",
                           f"model {ref!r} is not a provider/model reference set up in OpenProgram")
    try:
        from openprogram.models.providers.catalog.register import register_builtins
        register_builtins()
    except Exception:  # noqa: BLE001 - a partial registry still answers lookups
        pass
    model = get_model(provider, model_id)
    if model is None:
        try:
            from openprogram.models.providers.catalog.enabled_models import register_model_from_config
            if register_model_from_config(provider, model_id):
                model = get_model(provider, model_id)
        except Exception:  # noqa: BLE001 - not a configured custom model
            model = None
    if model is None:
        raise GatewayError(404, "not_found", f"model {value!r} is not set up in OpenProgram")
    return model


def enabled_model_refs() -> list[dict[str, Any]]:
    """The user's enabled models as ``{"id": "provider/model", ...}`` rows."""
    from openprogram.models import listing
    rows = []
    for row in listing.list_enabled_models():
        provider, model_id = row.get("provider") or "", row.get("id") or ""
        if provider and model_id:
            rows.append({"id": f"{provider}/{model_id}", "provider": provider,
                         "name": row.get("name") or model_id,
                         "context_window": row.get("context_window"),
                         "thinking_levels": row.get("thinking_levels") or [],
                         "default_thinking_level": row.get("default_thinking_level")})
    return rows


# Reasoning signatures

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def wrap_signature(model: Model, signature: str) -> str:
    """``signature`` as the client keeps it: tagged with the model that made it."""
    payload = json.dumps({"m": model_ref(model), "s": signature}, separators=(",", ":"))
    return _SIGNATURE_PREFIX + _b64(payload.encode("utf-8"))


def unwrap_signature(value: str | None) -> tuple[str | None, str | None]:
    """``(model ref, signature)`` of a wrapped value; ``(None, value)`` for a raw one."""
    if not value:
        return None, None
    if not value.startswith(_SIGNATURE_PREFIX):
        return None, value
    try:
        payload = json.loads(_unb64(value[len(_SIGNATURE_PREFIX):]))
        return str(payload["m"]), str(payload["s"])
    except (ValueError, KeyError, TypeError):
        return None, None


def client_signature(model: Model, signature: str | None, *, native_anthropic: bool) -> str | None:
    """The signature written to a client, or ``None`` when the upstream gave none.

    An Anthropic client of an Anthropic model gets the model's own signature,
    which stays valid if the client later talks to Anthropic directly.
    """
    if not signature:
        return None
    if native_anthropic and model.api in ANTHROPIC_APIS:
        return signature
    return wrap_signature(model, signature)


def thinking_for(model: Model, text: str, signature: str | None, *,
                 redacted: bool = False, raw_is_anthropic: bool) -> ThinkingContent | None:
    """A client's thinking block as the upstream may receive it, or ``None``.

    A wrapped signature is kept only for the model that made it. A raw one is
    Anthropic's (``raw_is_anthropic``) and kept only for an Anthropic model.
    An unsigned block never goes to Anthropic, which would refuse it.
    """
    origin, raw = unwrap_signature(signature)
    if signature and raw is None:
        return None
    if origin is not None:
        if origin != model_ref(model):
            return None
    elif raw:
        if not (raw_is_anthropic and model.api in ANTHROPIC_APIS):
            return None
    elif model.api in ANTHROPIC_APIS or redacted:
        return None
    return ThinkingContent(thinking=text or "", thinking_signature=raw or None,
                           redacted=True if redacted else None)


def assistant_message(model: Model, content: list[Any]) -> AssistantMessage:
    """A client's earlier assistant turn, stamped as the upstream model's."""
    return AssistantMessage(content=content, api=model.api, provider=model.provider,
                            model=model.id, usage=Usage(), stop_reason="stop", timestamp=now_ms())


# Content helpers

def image_from_data_url(url: str) -> ImageContent | None:
    """An ``data:<mime>;base64,<data>`` URL as an image block; ``None`` otherwise."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    head, _, data = url.partition(",")
    if ";base64" not in head or not data:
        return None
    return ImageContent(data=data, mime_type=head[5:].split(";", 1)[0] or "image/png")


def text_block(text: str, cache_control: Any = None) -> TextContent:
    return TextContent(text=text, cache_control=cache_control if isinstance(cache_control, dict) else None)


def json_arguments(value: Any) -> dict[str, Any]:
    """Tool-call arguments sent as a JSON string or an object."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    return {}


def arguments_json(call: ToolCall) -> str:
    return json.dumps(call.arguments or {}, ensure_ascii=False, separators=(",", ":"))


# Options

def reasoning_level(model: Model, requested: str | None) -> str | None:
    """``requested`` clamped to a level ``model`` offers; ``None`` for its default."""
    if not requested or not getattr(model, "reasoning", False):
        return None
    if requested in ("none", "off"):
        # Thinking off reaches only a provider with a wire value for it, as in the agent loop.
        from openprogram.models.providers.settings.thinking_spec import off_wire_value
        return "off" if off_wire_value(model.provider, model.id, getattr(model, "api", None)) is not None else None
    offered = [level for level in _LEVELS if level in (model.thinking_levels or [])] or list(_LEVELS[1:4])
    if requested in offered:
        return requested
    if requested not in _LEVELS:
        return None
    want = _LEVELS.index(requested)
    below = [level for level in offered if _LEVELS.index(level) <= want]
    return below[-1] if below else offered[0]


def budget_level(budget: Any) -> str:
    """Anthropic's ``budget_tokens`` as a reasoning level."""
    try:
        tokens = int(budget)
    except (TypeError, ValueError):
        return "medium"
    if tokens <= 2048:
        return "low"
    if tokens <= 12000:
        return "medium"
    if tokens <= 32000:
        return "high"
    return "xhigh"


def session_key(value: Any) -> str | None:
    """A short stable cache key from a client's conversation identifier."""
    if not value:
        return None
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return "gw-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


@dataclass
class UsageCounts:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def of(cls, usage: Usage | None) -> "UsageCounts":
        if usage is None:
            return cls()
        return cls(int(usage.input or 0), int(usage.output or 0),
                   int(usage.cache_read or 0), int(usage.cache_write or 0))


def estimate_tokens(texts: list[str], images: int = 0) -> int:
    """A local token estimate: a CJK character counts 1, anything else 1/4."""
    from openprogram.state.store.session.weight import weight
    return int(sum(weight(text) for text in texts)) + 1600 * images + 4 * len(texts)

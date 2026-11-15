"""Provider/model failover classification.

Decides whether an LLM-call failure is worth retrying on a *different*
provider/model (rather than the same one). Mirrors OpenClaw's
``failover-matches.ts`` and hermes' fallback chain: transient capacity /
availability problems (rate limit, overload, server error, timeout,
network) are failover-worthy; request-level problems (auth, invalid
request, context overflow, content policy) would fail on any provider, so
they are not.

``failover_category`` / ``should_failover`` are a PURE classifier — they
have no opinion on *which* fallback to use. The runtime fallback chain
(:func:`resolve_fallback_models` and below) layers on top.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
from typing import Any, Callable, Optional

from .errors import ErrorReason, classify_error

_log = logging.getLogger(__name__)


class FailoverCategory(str, enum.Enum):
    """Why a failure might warrant trying another provider/model."""

    NONE = "none"            # request-level / permanent — failover won't help
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER = "server"
    TIMEOUT = "timeout"
    NETWORK = "network"


_OVERLOAD_MARKERS = (
    "overloaded",
    "at capacity",
    "high demand",
    "service unavailable",
    "service_unavailable",
)


def failover_category(
    exc: BaseException,
    *,
    http_status: Optional[int] = None,
    error_text: str = "",
) -> FailoverCategory:
    """Map a failure to a :class:`FailoverCategory`.

    Reuses :func:`errors.classify_error` for the base reason, then refines
    provider-internal failures into ``overloaded`` vs ``server`` by body
    text (an overloaded provider is the canonical failover trigger).
    """
    # Auto-extract the structured fields a ProviderStreamError carries so
    # callers don't have to thread them through (429 etc. classify right
    # even when only the exception is passed).
    if http_status is None:
        http_status = getattr(exc, "http_status", None)
    if not error_text:
        error_text = getattr(exc, "error_text", "") or ""
    reason, _ = classify_error(exc, http_status=http_status, error_text=error_text)
    msg = (str(exc) + " " + (error_text or "")).lower()

    if reason == ErrorReason.RATE_LIMIT:
        return FailoverCategory.RATE_LIMIT
    if reason == ErrorReason.PROVIDER_INTERNAL:
        if any(m in msg for m in _OVERLOAD_MARKERS):
            return FailoverCategory.OVERLOADED
        return FailoverCategory.SERVER
    if reason == ErrorReason.TIMEOUT:
        return FailoverCategory.TIMEOUT
    if reason == ErrorReason.TRANSPORT:
        return FailoverCategory.NETWORK
    if reason in (
        ErrorReason.AUTHENTICATION,
        ErrorReason.AUTHORIZATION,
        ErrorReason.INVALID_REQUEST,
        ErrorReason.CONTEXT_LENGTH,
        ErrorReason.CONTENT_POLICY,
    ):
        return FailoverCategory.NONE
    # Stream adapters carry the most precise verdict on their exception.
    # Some pre-content EOFs have no HTTP status or marker for the generic
    # taxonomy to recognize, but ``retry_stream`` has already classified
    # them as safe to replay. They are equally safe to try on a fallback
    # model; ignoring this bit made the advertised fallback chain inert for
    # Codex's common "ended before terminal response" failure.
    if getattr(exc, "retryable", None) is True:
        return FailoverCategory.NETWORK
    return FailoverCategory.NONE


def should_failover(
    exc: BaseException,
    *,
    http_status: Optional[int] = None,
    error_text: str = "",
) -> bool:
    """True when retrying on a different provider/model could plausibly help."""
    return failover_category(
        exc, http_status=http_status, error_text=error_text
    ) is not FailoverCategory.NONE


# ---------------------------------------------------------------------------
# Orchestration — try a primary model, fall back to others on a
# failover-worthy *pre-content* failure. Off by default: a spent plan
# surfaces as 429 instead of answering on another model. The Settings
# page's ``model_fallback`` block (or OPENPROGRAM_FALLBACK_MODELS, which
# overrides it) picks ``same-provider`` for up to two other enabled models
# of the same provider, or a provider/model list to cross providers.
# ---------------------------------------------------------------------------

# Event types that mean real output has begun — once any of these has been
# forwarded we are "committed" and must NOT switch models (it would dupe or
# drop tokens). A text/thinking ``*_start`` is deliberately not substantive:
# Responses providers announce an empty block before sending any delta, and a
# stream can die immediately afterwards. Treating that placeholder as output
# disabled the fallback chain exactly when it was most useful.
_CONTENT_EVENT_TYPES = frozenset({
    "text_delta", "text_end",
    "thinking_delta", "thinking_end",
    "toolcall_start", "toolcall_delta", "toolcall_end",
})


# How many implicit (same-provider) fallbacks to try. Small on purpose: a
# rate-limited provider shouldn't turn one failure into a long serial retry
# storm across every model the user enabled.
_IMPLICIT_FALLBACK_LIMIT = 2


def _same_provider_fallbacks(primary_model: Any) -> list[Any]:
    """The user's other enabled models on the SAME provider as the primary.

    Same provider = same credential the call was already using, so this can
    never route a prompt to a provider the user never configured. Order is
    ENABLED_MODELS insertion order (the user's config row order), capped at
    ``_IMPLICIT_FALLBACK_LIMIT``.
    """
    from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

    prov = getattr(primary_model, "provider", "") or ""
    pid = getattr(primary_model, "id", "") or ""
    if not prov:
        return []
    out: list[Any] = []
    for m in ENABLED_MODELS.values():
        if getattr(m, "provider", "") != prov or getattr(m, "id", "") == pid:
            continue
        out.append(m)
        if len(out) >= _IMPLICIT_FALLBACK_LIMIT:
            break
    return out


def _fallback_policy() -> tuple[str, list[str]]:
    """``(mode, specs)`` from ``OPENPROGRAM_FALLBACK_MODELS`` when it is set,
    else from the ``model_fallback`` config block the Settings page writes.

    ``mode`` is ``off``, ``same-provider`` or ``list``; ``specs`` holds the
    ``provider/model`` ids of a ``list`` policy.
    """
    raw = os.environ.get("OPENPROGRAM_FALLBACK_MODELS", "").strip()
    if raw:
        low = raw.lower()
        if low in ("off", "none"):
            return "off", []
        if low in ("same-provider", "same_provider"):
            return "same-provider", []
        return "list", [s.strip() for s in raw.split(",") if s.strip()]
    try:
        from openprogram.state import config as user_config

        block = user_config.read_config().get("model_fallback") or {}
    except Exception:
        return "off", []
    mode = str(block.get("mode") or "off").strip().lower().replace("_", "-")
    specs = [str(s).strip() for s in (block.get("models") or []) if str(s).strip()]
    if mode not in ("same-provider", "list"):
        return "off", []
    return mode, specs


def resolve_fallback_models(primary_model: Any) -> list[Any]:
    """Resolve the fallback models for ``primary_model``.

    The policy comes from ``OPENPROGRAM_FALLBACK_MODELS`` when set, else from
    the ``model_fallback`` setting (:func:`_fallback_policy`):

    - ``off`` (the default) → ``[]``. The picked model is the only one that
      runs; a spent plan surfaces as 429 instead of answering on another
      model.
    - ``same-provider`` → up to ``_IMPLICIT_FALLBACK_LIMIT`` other enabled
      models of the primary's own provider.
    - ``list`` → the given ``provider/model`` ids in order. Unresolvable
      entries and the primary itself are skipped.

    Never raises: any import/lookup failure resolves to ``[]``.
    """
    mode, specs = _fallback_policy()
    if mode == "off":
        return []
    if mode == "same-provider":
        try:
            return _same_provider_fallbacks(primary_model)
        except Exception:
            return []
    try:
        from openprogram.models.providers import get_model
    except Exception:
        return []
    prim = f"{getattr(primary_model, 'provider', '')}/{getattr(primary_model, 'id', '')}"
    out: list[Any] = []
    for spec in specs:
        # spec 是 "provider/model"，get_model 签名是 (provider, model_id)。
        prov, _, mid = spec.partition("/")
        try:
            m = get_model(prov, mid) if prov and mid else None
        except Exception:
            continue  # unconfigured / unknown fallback — skip, never crash
        if m is None:
            continue
        if f"{getattr(m, 'provider', '')}/{getattr(m, 'id', '')}" == prim:
            continue
        out.append(m)
    return out


def stream_with_failover(
    base_stream_fn: Callable[..., Any],
    model: Any,
    context: Any,
    options: Any,
    fallback_models: list[Any],
):
    """An EventStream that tries ``model`` then each fallback on a
    failover-worthy failure that happens *before any content* streamed.

    Forwards every event from the live candidate to the returned stream. The
    first ``start`` event is forwarded once (so the consumer doesn't append a
    second partial message when we switch candidates). Once a content event
    has been forwarded we are committed — any later failure propagates
    unchanged (no model switch). With an empty ``fallback_models`` this still
    works but never actually switches, so it's safe as a transparent wrapper.
    """
    from .event_stream import AssistantMessageEventStream, close_stream

    out = AssistantMessageEventStream()
    candidates = [model] + list(fallback_models)

    async def _run() -> None:
        started_forwarded = False
        last_exc: Optional[BaseException] = None
        skipped: list[dict[str, str]] = []
        for idx, cand in enumerate(candidates):
            committed = False
            is_last = idx == len(candidates) - 1
            inner = None
            try:
                inner = base_stream_fn(cand, context, options)
                async for ev in inner:
                    etype = getattr(ev, "type", None) or (
                        ev.get("type") if isinstance(ev, dict) else None
                    )
                    if etype == "start":
                        if started_forwarded:
                            continue  # avoid a duplicate partial in the consumer
                        started_forwarded = True
                    elif etype in _CONTENT_EVENT_TYPES:
                        committed = True
                    elif etype == "retry" and getattr(ev, "restart", False):
                        # The candidate re-sends its request and the consumer
                        # drops what it streamed: nothing is committed again.
                        committed = False
                    elif etype == "done" and skipped:
                        message = getattr(ev, "message", None)
                        if message is not None and hasattr(message, "failover"):
                            message.failover = list(skipped)
                    out.push(ev)
                    if etype in ("done", "error"):
                        return  # terminal event already ends `out`
                # Inner exhausted without a terminal event — defensive.
                out.fail(RuntimeError("stream ended without a final message"))
                return
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if isinstance(exc, asyncio.CancelledError):
                    # A stop: the candidate's provider request ends with it.
                    out.fail(exc)
                    await close_stream(inner, timeout=0.5)
                    raise
                # Switch models only when nothing streamed yet, the failure is
                # failover-worthy, and another candidate remains.
                if committed or is_last or not should_failover(exc):
                    out.fail(exc)
                    return
                nxt = candidates[idx + 1]
                skipped.append({
                    "provider": str(getattr(cand, "provider", "") or ""),
                    "model": str(getattr(cand, "id", "") or ""),
                    "reason": failover_category(exc).value,
                })
                _record_switch(skipped[-1], nxt)
                _log.info(
                    "%s/%s failed (%s: %s) — trying %s/%s",
                    getattr(cand, "provider", "?"), getattr(cand, "id", "?"),
                    type(exc).__name__, str(exc)[:80],
                    getattr(nxt, "provider", "?"), getattr(nxt, "id", "?"),
                )
                continue
        if last_exc is not None:
            out.fail(last_exc)

    # Owned by the stream: a consumer that stops reading cancels this task
    # through ``close_stream``, and the task closes the live candidate's.
    out.attach_producer(asyncio.ensure_future(_run()))
    return out


def _record_switch(skipped: dict[str, str], nxt: Any) -> None:
    """Keep the switch in the usage ledger for the routing overview. A
    bookkeeping failure never stops the request from moving on."""
    try:
        from openprogram.models.usage.context import current_usage_context
        from openprogram.models.usage.ledger import default_ledger
        scope = current_usage_context()
        default_ledger.append_route_event(
            from_provider=skipped["provider"],
            from_model=skipped["model"],
            to_provider=str(getattr(nxt, "provider", "") or ""),
            to_model=str(getattr(nxt, "id", "") or ""),
            reason=skipped["reason"],
            session_id=scope.session_id,
            parent_session_id=scope.parent_session_id,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("could not record the switch: %s", exc, exc_info=True)


def failover_stream_fn(base_stream_fn: Callable[..., Any], fallback_models: list[Any]):
    """Wrap a ``stream_fn`` (``(model, context, options) -> EventStream``) so it
    fails over across ``fallback_models``. Returns ``base_stream_fn`` unchanged
    when there are no fallbacks (so the default path is never wrapped)."""
    if not fallback_models:
        return base_stream_fn

    def _fn(model: Any, context: Any, options: Any):
        return stream_with_failover(base_stream_fn, model, context, options, fallback_models)

    return _fn


__all__ = [
    "FailoverCategory",
    "failover_category",
    "should_failover",
    "resolve_fallback_models",
    "stream_with_failover",
    "failover_stream_fn",
]

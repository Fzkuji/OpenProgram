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
import os
import sys
from typing import Any, Callable, Optional

from .errors import ErrorReason, classify_error


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
# failover-worthy *pre-content* failure. ON by default, conservatively: with
# nothing configured the chain is the user's other enabled models of the SAME
# provider (max 2), so a failover never contacts a provider the user has not
# configured and never opens a new billing relationship.
# OPENPROGRAM_FALLBACK_MODELS="provider/model,provider2/model2,..." overrides
# it with an explicit list (may cross providers); "off"/"none" disables.
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
    from openprogram.providers.enabled_models import ENABLED_MODELS

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


def resolve_fallback_models(primary_model: Any) -> list[Any]:
    """Resolve the fallback models for ``primary_model``.

    ``OPENPROGRAM_FALLBACK_MODELS`` decides:

    - unset/empty → the implicit default chain: up to
      ``_IMPLICIT_FALLBACK_LIMIT`` other enabled models of the primary's own
      provider (see :func:`_same_provider_fallbacks`).
    - ``"off"`` / ``"none"`` (case-insensitive) → ``[]``, failover disabled.
    - anything else → an explicit comma-separated list of ``provider/model``
      ids, which may cross providers (the user asked for it). Unresolvable
      entries and the primary itself are skipped.

    Never raises: any import/lookup failure resolves to ``[]``.
    """
    raw = os.environ.get("OPENPROGRAM_FALLBACK_MODELS", "").strip()
    if raw.lower() in ("off", "none"):
        return []
    if not raw:
        try:
            return _same_provider_fallbacks(primary_model)
        except Exception:
            return []
    try:
        from openprogram.providers import get_model
    except Exception:
        return []
    prim = f"{getattr(primary_model, 'provider', '')}/{getattr(primary_model, 'id', '')}"
    out: list[Any] = []
    for spec in (s.strip() for s in raw.split(",")):
        if not spec:
            continue
        # spec 是 "provider/model"，get_model 签名是 (provider, model_id)：
        # 以前整串单参传入恒 TypeError 被吞，failover 名单永远为空。
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
    from .event_stream import AssistantMessageEventStream

    out = AssistantMessageEventStream()
    candidates = [model] + list(fallback_models)

    async def _run() -> None:
        started_forwarded = False
        last_exc: Optional[BaseException] = None
        for idx, cand in enumerate(candidates):
            committed = False
            is_last = idx == len(candidates) - 1
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
                    out.push(ev)
                    if etype in ("done", "error"):
                        return  # terminal event already ends `out`
                # Inner exhausted without a terminal event — defensive.
                out.fail(RuntimeError("stream ended without a final message"))
                return
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                # Switch models only when nothing streamed yet, the failure is
                # failover-worthy, and another candidate remains.
                if committed or is_last or not should_failover(exc):
                    out.fail(exc)
                    return
                nxt = candidates[idx + 1]
                print(
                    f"[failover] {getattr(cand, 'provider', '?')}/{getattr(cand, 'id', '?')} "
                    f"failed ({type(exc).__name__}: {str(exc)[:80]}) — trying "
                    f"{getattr(nxt, 'provider', '?')}/{getattr(nxt, 'id', '?')}",
                    file=sys.stderr, flush=True,
                )
                continue
        if last_exc is not None:
            out.fail(last_exc)

    asyncio.ensure_future(_run())
    return out


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

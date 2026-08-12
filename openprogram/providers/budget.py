"""Budget enforcement for one provider request.

``stream()`` / ``stream_simple()`` are the single seam every LLM call this
module serves passes through, so reserve/start/settle lives here rather than
in each adapter.

A call is *budgeted* only when a governed task is bound to this context
(``current_task_resource_context()``). Everything else — CLI, tests, headless
usage — stays on the historical best-effort recording path untouched.

The order matters and is the whole point of the module:

1. estimate a conservative input upper bound and reserve token/cost exposure
   BEFORE credentials are resolved or any socket is opened;
2. clamp ``max_tokens`` to the reserved output cap so the provider cannot
   return more than was paid for;
3. mark the reservation started immediately before provider I/O;
4. settle the real provider-reported usage atomically with appending the
   UsageEvent, releasing only the unused exposure.
"""
from __future__ import annotations

from typing import Any

class QuotaExceeded(RuntimeError):
    """A budgeted call was refused. ``reason_code`` is the stable taxonomy."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        self.retryable = reason_code in _RETRYABLE_REASONS
        super().__init__(message or reason_code)


# Only accounting outages are worth retrying; an exhausted budget is not.
_RETRYABLE_REASONS = frozenset({"quota.accounting_unavailable"})

# No request cap declared means the model may run to its own ceiling. Use a
# high floor so the reservation cannot silently under-count the exposure.
_UNCAPPED_OUTPUT_FLOOR = 4096
# Anthropic's own fallback when a provider declares no budget_map entry.
_DEFAULT_REASONING_BUDGET = 8192
_BYTE_BOUNDED_APIS = frozenset({
    "openai-completions", "mistral-conversations", "openai-responses",
    "azure-openai-responses", "openai-codex", "anthropic-messages",
    "bedrock-converse-stream", "google-generative-ai", "gemini-subscription",
})


def estimate_input_upper_bound(context, options, model=None) -> int:
    """Return a tokenizer-independent byte upper bound for supported adapters."""
    window = getattr(model, "context_window", None)
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise QuotaExceeded(
            "quota.accounting_unavailable",
            "provider/model has no safe input token ceiling",
        )
    if getattr(model, "api", None) not in _BYTE_BOUNDED_APIS:
        raise QuotaExceeded(
            "quota.accounting_unavailable",
            "provider adapter has no audited safe input token bound",
        )
    for message in getattr(context, "messages", None) or []:
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", None) in {"image", "video", "audio"}:
                raise QuotaExceeded(
                    "quota.accounting_unavailable",
                    "multimodal input has no audited provider token upper bound",
                )
    import json

    context_payload = (
        context.model_dump(mode="json")
        if callable(getattr(context, "model_dump", None))
        else context
    )
    option_payload = (
        options.model_dump(
            mode="json",
            exclude={"api_key", "signal", "on_payload", "headers", "session_id"},
            exclude_none=True,
        )
        if options is not None and callable(getattr(options, "model_dump", None))
        else {}
    )
    for name, value in list(option_payload.items()):
        if callable(getattr(value, "model_dump", None)):
            option_payload[name] = value.model_dump(mode="json")
    try:
        encoded = json.dumps(
            {"context": context_payload, "options": option_payload},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
    except Exception as exc:
        raise QuotaExceeded(
            "quota.accounting_unavailable", "request cannot be safely counted",
        ) from exc
    # Every adapter reachable from this common stream layer uses a byte-backed
    # tokenizer; therefore token count cannot exceed serialized UTF-8 bytes.
    # The fixed per-item allowance covers adapter-added role/request framing.
    wrapper = 1024 + 64 * (
        len(getattr(context, "messages", None) or [])
        + len(getattr(context, "tools", None) or [])
    )
    return min(window, len(encoded) + wrapper)


def reasoning_budget(options, model) -> int:
    """Thinking tokens this request may bill on top of ``max_tokens``.

    Anthropic raises ``max_tokens`` by the thinking budget when the declared
    cap is below it, so a reservation that ignored reasoning would under-count
    the real exposure.
    """
    reasoning = getattr(options, "reasoning", None) if options else None
    if not reasoning:
        return 0
    budgets = getattr(options, "thinking_budgets", None)
    custom = getattr(budgets, reasoning, None) if budgets else None
    if isinstance(custom, int) and not isinstance(custom, bool) and custom > 0:
        return custom
    try:
        from openprogram.providers.thinking_spec import get_thinking_spec
        spec = get_thinking_spec(getattr(model, "provider", "") or "")
        budget = (spec.get("budget_map") or {}).get(reasoning)
    except Exception:
        budget = None
    if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
        return budget
    return _DEFAULT_REASONING_BUDGET


def requested_output_cap(options, model) -> int:
    """The output ceiling this request could reach before budget clamping.

    Includes reasoning tokens: they are billed as output even though they
    are not part of the declared ``max_tokens``.
    """
    declared = None
    for candidate in (
        getattr(options, "max_tokens", None) if options else None,
        getattr(model, "max_tokens", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            declared = candidate
            break
    if declared is None:
        declared = _UNCAPPED_OUTPUT_FLOOR
    return declared + reasoning_budget(options, model)


def provider_retry_attempts(default_attempts: int) -> int:
    """Disable adapter retries when one durable reservation owns one attempt."""
    try:
        from openprogram.agent.task.runner import current_task_resource_context
        return 1 if current_task_resource_context() is not None else default_attempts
    except Exception:
        return default_attempts


def provider_sdk_retries(default_retries: int) -> int:
    return max(0, provider_retry_attempts(default_retries + 1) - 1)


class BudgetedRequest:
    """Reserve/start/settle around one provider request.

    ``None`` from :func:`begin` means the call is unbudgeted and must keep
    its existing best-effort recording behaviour.
    """

    def __init__(self, task_id: str, governor: Any, reservation: Any) -> None:
        self._task_id = task_id
        self._governor = governor
        self.reservation = reservation
        self._settled = False

    @classmethod
    def begin(cls, model, context, options) -> "BudgetedRequest | None":
        """Reserve exposure for this request, or return None if unbudgeted.

        Raises :class:`QuotaExceeded` before any credential or network work
        when the request cannot be afforded or cannot be accounted for.
        """
        try:
            from openprogram.agent.task.runner import current_task_resource_context
            bound = current_task_resource_context()
        except Exception:
            bound = None
        if bound is None:
            return None
        task_id, governor = bound

        if getattr(options, "on_payload", None) is not None:
            raise QuotaExceeded(
                "quota.accounting_unavailable",
                "budgeted requests cannot safely account for on_payload mutation",
            )
        input_bound = estimate_input_upper_bound(context, options, model)
        try:
            reservation = governor.reserve_provider_request(
                task_id,
                input_token_upper_bound=input_bound,
                requested_max_output_tokens=requested_output_cap(options, model),
                model=model,
            )
        except Exception as exc:
            # The ledger is the budget authority. If it cannot answer, a
            # budgeted call must fail rather than proceed unmetered.
            raise QuotaExceeded(
                "quota.accounting_unavailable",
                f"resource accounting unavailable: {exc}",
            ) from exc
        if not reservation.allowed:
            raise QuotaExceeded(
                reservation.reason_code or "quota.accounting_unavailable",
            )
        return cls(task_id, governor, reservation)

    def clamp(self, options, model=None):
        """Return options whose output cap cannot exceed the reservation.

        The reservation covers declared output plus reasoning, so the cap
        handed to the provider is the reservation minus the reasoning the
        provider will add back on top of it.
        """
        cap = self.reservation.output_token_cap - reasoning_budget(options, model)
        if cap <= 0:
            raise QuotaExceeded("quota.token_exhausted")
        current = getattr(options, "max_tokens", None)
        if isinstance(current, int) and not isinstance(current, bool) and 0 < current <= cap:
            return options
        return options.model_copy(update={"max_tokens": cap})

    def start(self) -> None:
        """Mark the reservation started immediately before provider I/O."""
        try:
            self._governor.start_provider_request(self.reservation.reservation_id)
        except Exception as exc:
            raise QuotaExceeded(
                "quota.accounting_unavailable",
                f"could not start resource reservation: {exc}",
            ) from exc

    def settle(self, model, final, options) -> None:
        """Settle actual usage and append the event in one transaction.

        Idempotent: a provider that emits two terminal events settles once.
        """
        if self._settled:
            return
        from openprogram.usage.recorder import build_message_event, run_usage_hooks
        event = build_message_event(
            model, final,
            session_id=getattr(options, "session_id", None) if options else None,
        )
        if event is None:
            # Reached the provider but reported no usage. Keep the
            # conservative reservation held rather than releasing exposure
            # we cannot prove went unused.
            return
        try:
            attributed = self._governor.settle_provider_request(
                self.reservation.reservation_id, event,
            )
        except Exception as exc:
            raise QuotaExceeded(
                "quota.accounting_unavailable",
                f"could not settle resource reservation: {exc}",
            ) from exc
        self._settled = True
        if attributed is not None:
            run_usage_hooks(attributed)

    def release(self) -> None:
        """Release exposure for a request that never reached the provider."""
        if self._settled:
            return
        try:
            self._governor.release_provider_request(
                self.reservation.reservation_id,
            )
        except Exception:
            # Expiry-based recovery reclaims a reservation we failed to
            # release here, so this must not mask the original failure.
            pass


__all__ = [
    "BudgetedRequest", "QuotaExceeded", "estimate_input_upper_bound",
    "provider_retry_attempts", "provider_sdk_retries", "requested_output_cap",
]

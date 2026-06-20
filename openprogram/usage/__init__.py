"""Usage metering — records every LLM call's tokens / model / cost.

Public API:

  usage_scope(call_kind=...)      label the source of LLM calls in a block
  record_message(model, message)  record one finished call (called at the
                                  stream.py chokepoint; rarely called directly)
  default_ledger.query(...)       aggregate recorded usage for panels/CLI
  register_usage_hook(fn)         subscribe to events (budget/alerting)

See docs/design/usage-metering.md.
"""
from __future__ import annotations

from .context import (
    UsageContext,
    apply_snapshot,
    current_usage_context,
    snapshot,
    usage_scope,
)
from .event import UsageEvent
from .ledger import AggregateRow, UsageLedger, default_ledger
from .recorder import record_message, register_usage_hook

__all__ = [
    "UsageEvent",
    "UsageContext",
    "usage_scope",
    "current_usage_context",
    "snapshot",
    "apply_snapshot",
    "UsageLedger",
    "AggregateRow",
    "default_ledger",
    "record_message",
    "register_usage_hook",
]

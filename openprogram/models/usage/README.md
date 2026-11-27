# `openprogram/models/usage/`

> Usage metering — records every LLM call's tokens / model / cost.

## Overview

Public API:

  usage_scope(call_kind=...)      label the source of LLM calls in a block
  record_message(model, message)  record one finished call (called at the
                                  stream.py chokepoint; rarely called directly)
  default_ledger.query(...)       aggregate recorded usage for panels/CLI
  register_usage_hook(fn)         subscribe to events (budget/alerting)
  prefix_fingerprint.expect_break announce a prompt-cache break made on purpose

See docs/reference/design/providers/usage-metering.html.

## Files in this directory

- **`context.py`** — Call-source context for usage metering
- **`event.py`** — UsageEvent
- **`goal_port.py`** — What usage metering needs from the Goal tool
- **`ledger.py`** — UsageLedger
- **`prefix_fingerprint.py`** — Where a request's cached prefix broke, and why
- **`probe_log.py`** — The requests OpenProgram sends a provider on its own behalf
- **`quota.py`** — Provider quota: how much of a provider's limits is left, per key
- **`recorder.py`** — UsageRecorder
- **`request.py`** — Durable Goal request identity and replayable terminal usage receipts
- **`reservation.py`** — Safe bounds for one provider request before it is sent

## Sub-packages

- **`harnesses/`** — Third-party harnesses: Claude Code and Codex sessions on this computer, read only
- **`quota_fetchers/`** — Read-only quota endpoints, behind one interface, and when they are read

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._

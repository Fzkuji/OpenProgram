# `openprogram/usage/`

> Usage metering — records every LLM call's tokens / model / cost.

## Overview

Public API:

  usage_scope(call_kind=...)      label the source of LLM calls in a block
  record_message(model, message)  record one finished call (called at the
                                  stream.py chokepoint; rarely called directly)
  default_ledger.query(...)       aggregate recorded usage for panels/CLI
  register_usage_hook(fn)         subscribe to events (budget/alerting)

See docs/design/usage-metering.md.

## Files in this directory

- **`context.py`** — Call-source context for usage metering
- **`event.py`** — UsageEvent
- **`ledger.py`** — UsageLedger
- **`recorder.py`** — UsageRecorder

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._

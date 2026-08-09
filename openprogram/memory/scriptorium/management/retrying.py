"""Retry counts for model calls that fail intermittently.

Reaching a model through a gateway makes some failures transient: structured
output goes missing, or a reconciliation comes back violating its contract.
Retrying costs one more call; not retrying discarded a build that was already
an hour in.

The counts live here so they are visible and adjustable in one place. The retry
loops themselves stay at their call sites, because they react to different
things — one to a response that lacks structured output, the other to an
exception raised while validating one — and a single helper covering both would
be harder to follow than the two loops it replaced.
"""

from __future__ import annotations

# A missing structured output is retried outright: nothing about the request
# changes, and the next response usually has it.
STRUCTURED_OUTPUT_ATTEMPTS = 3

# Reconciliation is asked again with the same inputs, so a second failure means
# the model cannot satisfy the contract rather than that it slipped.
RECONCILIATION_ATTEMPTS = 2

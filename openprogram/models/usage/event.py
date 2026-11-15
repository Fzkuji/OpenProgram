"""UsageEvent — one immutable record per LLM call.

A UsageEvent is the unit the metering subsystem records and aggregates:
who made the call (session / agent / call_kind), against which model,
how many tokens, at what cost, when. See docs/design/usage-metering.md.

The schema is deliberately FLAT (cost fields not nested) so the SQLite
ledger can column-ize it and ``SUM(cost_total)`` without JSON parsing.
``call_kind`` is a free string, not an Enum, so new call sites can label
themselves without touching this module.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2


# Known call-kind labels. NOT exhaustive and NOT enforced — a string so
# new call sites extend it freely. Listed here for discoverability and so
# the UI can give friendly names to the common ones.
CALL_KIND_CHAT = "chat"            # interactive user turn (engine main path)
CALL_KIND_EXEC = "exec"            # Agent method runtime turn
CALL_KIND_COMPACTION = "compaction"
CALL_KIND_SUMMARIZE = "summarize"
CALL_KIND_MEMORY = "memory"
CALL_KIND_SUBAGENT = "subagent"
CALL_KIND_TOOL = "tool"            # LLM call from inside a tool (e.g. moa)
CALL_KIND_TITLE = "title"
CALL_KIND_PREDICTION = "prediction"  # suggested next message in the composer
CALL_KIND_PERMISSION = "permission"  # safety check on a tool call
CALL_KIND_DIRECT = "direct"        # code called a model with no turn or Runtime around it


class UsageEvent(BaseModel):
    """A single recorded LLM call. Frozen — events are facts, never edited."""

    model_config = {"frozen": True}

    # identity
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = 0.0  # unix epoch seconds; stamped by the recorder

    # attribution
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    agent_id: Optional[str] = None
    call_kind: str = CALL_KIND_DIRECT
    call_label: Optional[str] = None
    origin_pid: int = Field(default_factory=os.getpid)
    job_id: Optional[str] = None
    budget_scope_id: Optional[str] = None
    reservation_id: Optional[str] = None
    request_id: Optional[str] = None
    goal_id: Optional[str] = None
    goal_revision: Optional[int] = None
    goal_session_id: Optional[str] = None
    execution_id: Optional[str] = None
    tokens_known: bool = True

    # model
    provider: str = ""
    api: Optional[str] = None
    model_id: str = ""

    # Token subtotals; tokens_known distinguishes an absent report from zero.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0

    # cost (USD, flattened)
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_write: float = 0.0
    cost_total: float = 0.0
    cost_source: str = "unknown"  # model_catalog | configured | provider_reported | unknown

    # latency: time to the reply's first content and first text (ms)
    first_token_ms: Optional[int] = None
    first_text_ms: Optional[int] = None

    # prompt cache: the request's prefix hash and, when its cached prefix
    # broke, the first unit that differed, why, and the announced cause
    # that explains it (models/usage/prefix_fingerprint.py)
    prefix_hash: Optional[str] = None
    break_at: Optional[int] = None
    break_cause: Optional[str] = None
    expected: Optional[str] = None

    # provenance
    token_source: str = "provider_usage"  # provider_usage | anthropic_count_api | estimate
    schema_version: int = SCHEMA_VERSION

    def has_tokens(self) -> bool:
        return bool(
            self.input_tokens or self.output_tokens
            or self.cache_read_tokens or self.cache_write_tokens
        )


__all__ = [
    "UsageEvent", "SCHEMA_VERSION",
    "CALL_KIND_CHAT", "CALL_KIND_EXEC", "CALL_KIND_COMPACTION",
    "CALL_KIND_SUMMARIZE", "CALL_KIND_MEMORY", "CALL_KIND_SUBAGENT",
    "CALL_KIND_TOOL", "CALL_KIND_TITLE", "CALL_KIND_PERMISSION", "CALL_KIND_DIRECT",
]

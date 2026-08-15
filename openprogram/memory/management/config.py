"""Memory writer configuration."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryConfig:
    core_max_tokens: int = 2_000
    few_shot_instructions: bool = False
    recent_limit: int = 50
    max_turns: int = 20
    max_budget_usd: float | None = None
    writer_enabled: bool = True
    writer_trigger_tokens: int = 16_000
    retrieval_method: str = "bm25"
    retrieval_top_k: int = 5
    retrieval_include_sources: bool = True
    core_inject: bool = True

    def __post_init__(self) -> None:
        if self.core_max_tokens < 0:
            raise ValueError("core_max_tokens must be non-negative")
        if self.recent_limit < 0:
            raise ValueError("recent_limit must be non-negative")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        if self.writer_trigger_tokens not in {8_000, 16_000, 32_000}:
            raise ValueError("writer_trigger_tokens must be 8000, 16000 or 32000")
        if self.retrieval_method not in {"bm25", "embedding", "hybrid"}:
            raise ValueError("unsupported retrieval_method")
        if not 1 <= self.retrieval_top_k <= 10:
            raise ValueError("retrieval_top_k must be in 1..10")


def _section(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key, {})
    return value if isinstance(value, dict) else {}


def _integer(
    value: Any,
    default: int,
    *,
    allowed: set[int] | None = None,
    minimum: int = 1,
    maximum: int = 500,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if allowed is not None:
        return parsed if parsed in allowed else default
    return parsed if minimum <= parsed <= maximum else default


def load_memory_config(config: dict[str, Any] | None = None) -> MemoryConfig:
    """Resolve live Memory settings, tolerating hand-edited invalid config."""
    if config is None:
        from openprogram import setup

        config = setup._read_config()
    if not isinstance(config, dict):
        config = {}
    memory = _section(config, "memory")
    writer = _section(memory, "writer")
    retrieval = _section(memory, "retrieval")
    core = _section(memory, "core")
    recent = _section(memory, "recent")
    method = retrieval.get("method", "bm25")
    if not isinstance(method, str) or method not in {
        "bm25", "embedding", "hybrid",
    }:
        method = "bm25"
    return MemoryConfig(
        recent_limit=_integer(recent.get("limit"), 50),
        writer_enabled=(
            writer.get("enabled")
            if isinstance(writer.get("enabled"), bool)
            else True
        ),
        writer_trigger_tokens=_integer(
            writer.get("trigger_tokens"), 16_000,
            allowed={8_000, 16_000, 32_000},
        ),
        retrieval_method=method,
        retrieval_top_k=_integer(
            retrieval.get("top_k"), 5, maximum=10,
        ),
        retrieval_include_sources=(
            retrieval.get("include_sources")
            if isinstance(retrieval.get("include_sources"), bool)
            else True
        ),
        core_inject=(
            core.get("inject")
            if isinstance(core.get("inject"), bool)
            else True
        ),
    )

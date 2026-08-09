"""Memory writer configuration."""

from dataclasses import dataclass

@dataclass(frozen=True)
class MemoryConfig:
    core_max_tokens: int = 2_000
    few_shot_instructions: bool = False
    recent_limit: int = 50
    max_turns: int = 20
    max_budget_usd: float | None = None

    def __post_init__(self) -> None:
        if self.core_max_tokens < 0:
            raise ValueError("core_max_tokens must be non-negative")
        if self.recent_limit < 0:
            raise ValueError("recent_limit must be non-negative")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")

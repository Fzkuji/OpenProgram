"""Validated retrieval settings."""

from dataclasses import dataclass


SEARCH_TOOL_SETS = {
    # One search entry point that runs BM25 and embedding together and merges
    # them with reciprocal rank fusion. Measured on 40 conv-50 questions this
    # answered 34 against the split pair's 30, with 24% fewer output tokens,
    # because the model stops issuing both searches and merging them itself.
    "fused": ("memory_search",),
    # The two backends as separate tools, which is what the fused tool
    # replaced. Kept so the ablation can attribute the difference.
    "split": ("bm25_search", "embedding_search"),
}


@dataclass(frozen=True)
class QueryConfig:
    max_turns: int = 20
    max_budget_usd: float | None = None
    verify_sources: bool = True
    search_tools: str = "fused"

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        if self.search_tools not in SEARCH_TOOL_SETS:
            raise ValueError(
                "search_tools must be one of "
                + ", ".join(sorted(SEARCH_TOOL_SETS))
            )

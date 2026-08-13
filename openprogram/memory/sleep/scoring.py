"""Scoring helpers shared by the sleep phases.

Signals (OpenClaw-derived defaults, tuned light for our smaller scale):

  frequency        weight 0.24   how many journal entries point at a fact
  recall_count     weight 0.20   times this fact has been queried via prefetch
  query_diversity  weight 0.15   distinct queries that surfaced it
  recency          weight 0.15   time-decay over a 14-day half-life
  consolidation    weight 0.20   cross-day recurrence
  conceptual       weight 0.06   tag / type richness

A candidate must clear ALL of:

  min_score          0.7
  min_recall_count   3
  min_unique_queries 2

before promotion to wiki. Below threshold, it stays in journal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


WEIGHTS = {
    "frequency": 0.24,
    "recall_count": 0.20,
    "query_diversity": 0.15,
    "recency": 0.15,
    "consolidation": 0.20,
    "conceptual": 0.06,
}

THRESHOLDS = {
    "min_score": 0.7,
    "min_recall_count": 3,
    "min_unique_queries": 2,
    "recency_half_life_days": 14.0,
    "max_age_days": 30.0,
}


@dataclass
class CandidateScore:
    text: str
    score: float
    n_occurrences: int
    n_distinct_days: int
    recall_count: int
    distinct_queries: int
    age_days: float
    type: str
    tags: list[str]
    sources: list[str]            # journal file references


def recency_factor(age_days: float, half_life: float = 14.0) -> float:
    if age_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life)


def conceptual_richness(type: str, tags: list[str]) -> float:
    """0.0–1.0: how informative the type+tag combination is."""
    base = 0.3 if type and type != "observation" else 0.0
    return min(1.0, base + min(0.7, len(tags) * 0.2))


def consolidation_factor(n_distinct_days: int) -> float:
    """Cross-day recurrence — capped at 1.0 around 4 days."""
    return min(1.0, n_distinct_days / 4.0)


def passes_thresholds(c: CandidateScore) -> bool:
    return (
        c.score >= THRESHOLDS["min_score"]
        and c.recall_count >= THRESHOLDS["min_recall_count"]
        and c.distinct_queries >= THRESHOLDS["min_unique_queries"]
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

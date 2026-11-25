"""Baseline lineage: uncited works that report stronger results on the paper's
benchmarks (missing_baselines)."""

from .workflow import extractor, missing_baselines

ROLES = {"extractor": extractor}

__all__ = ["ROLES", "missing_baselines"]

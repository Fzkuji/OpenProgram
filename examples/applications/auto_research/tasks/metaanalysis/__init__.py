"""Meta-analysis of published results for one question: dual blind screening with
Cohen's kappa, quoted extraction, a risk-of-bias checklist for ML experiments,
DerSimonian-Laird pooling and a GRADE certainty rating (meta_analysis)."""

from .workflow import ROLES, meta_analysis

__all__ = ["ROLES", "meta_analysis"]

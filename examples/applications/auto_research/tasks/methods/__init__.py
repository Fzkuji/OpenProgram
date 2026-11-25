"""Checking the experiments' method: methodology errors in the experiment code
(method_lint) and whether each claim's comparison holds beyond seed noise
(claim_stats, with POPPER's sequential falsification in popper mode)."""

from .claims import claim_stats, falsifier
from .lint import method_lint, reviewer

ROLES = {"reviewer": reviewer, "falsifier": falsifier}

__all__ = ["ROLES", "claim_stats", "method_lint"]

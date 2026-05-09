"""Sleep — background memory consolidation.

Three cooperative phases run in order, typically once a day at 3am
(controlled by the worker cron):

  light  — dedupe + score short-term entries, write phase signals
  deep   — promote candidates to wiki, regenerate pages, refresh core.md
  rem    — scan wiki for themes / contradictions, append reflections

Run via :func:`run_sweep` or one phase at a time via the per-phase
modules.
"""
from .runner import run_sweep, run_phase

__all__ = ["run_sweep", "run_phase"]

"""
research — autonomous research workflow app.

A complete research pipeline from project initialization to submission,
built on the Agentic Programming framework.

Stages:
    0. init        — project directory structure
    1. literature  — literature survey and gap identification
    2. idea        — idea generation, novelty check, ranking
    3. experiment  — experiment design and execution
    4. analysis    — result analysis and visualization
    5. writing     — paper writing (sections, polish, translate)
    6. review      — cross-model review and iterative improvement
    7. submission  — pre-submission checklist

All prompts are @agentic_function docstrings. No external prompt files.
"""

from openprogram.functions.agentics.research.pipeline import research_pipeline, STAGES
from openprogram.functions.agentics.research.evaluate import compete

__all__ = ["research_pipeline", "compete", "STAGES"]

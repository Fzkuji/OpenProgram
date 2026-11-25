"""Human evaluation of system outputs: a blind survey page with attention checks and
repeats, the raters it needs and a pilot of its guide (human_eval_build), and the
agreement, Bradley-Terry ranking and claim evidence of the answers (human_eval_analyze)."""

from .analyze import human_eval_analyze
from .survey import guide_writer, human_eval_build, pilot_rater

ROLES = {"guide_writer": guide_writer, "pilot_rater": pilot_rater}

__all__ = ["ROLES", "human_eval_analyze", "human_eval_build"]

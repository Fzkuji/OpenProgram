"""Research integrity checks before a paper goes out: hidden text aimed at model
reviewers, benchmark contamination in pretraining corpora, and reused figures."""

from .contamination import contamination_check
from .hidden import hidden_text_check
from .images import image_reuse

__all__ = ["contamination_check", "hidden_text_check", "image_reuse"]

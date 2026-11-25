"""Proofread a paper section by section and propose titles and abstracts (the paper_proofread task)."""

from .workflow import apply_edit, paper_proofread

__all__ = ["apply_edit", "paper_proofread"]

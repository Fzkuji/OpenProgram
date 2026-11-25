"""The paper's language: a whole-paper LaTeX translation (paper_translate) and a style
check against the author's earlier papers and Writing in the Sciences (writing_style)."""

from ..proofread.workflow import apply_edit
from .style import writing_style
from .translate import paper_translate

__all__ = ["apply_edit", "paper_translate", "writing_style"]

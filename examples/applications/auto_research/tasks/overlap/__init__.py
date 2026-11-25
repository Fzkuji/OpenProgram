"""Find passages of the paper that repeat its sources: the author's earlier papers,
the works it cites and the closest works on its topic."""

from .workflow import text_overlap

__all__ = ["text_overlap"]

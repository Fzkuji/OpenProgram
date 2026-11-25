"""Read the paper against its sources and its readers: the numbers it copies from other
works, related work per paragraph, the author's annotations, and what a skimming
reader takes away."""

from .annotations import import_annotations
from .related import related_by_paragraph
from .skim import skim_test
from .sota import baseline_numbers

__all__ = ["baseline_numbers", "import_annotations", "related_by_paragraph", "skim_test"]

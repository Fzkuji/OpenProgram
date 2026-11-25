"""Before submission: the pre-submission check, the arXiv source bundle and the AI-use disclosure draft."""

from .check import submission_check
from .disclosure import ai_disclosure
from .package import arxiv_package

__all__ = ["ai_disclosure", "arxiv_package", "submission_check"]

"""Figures from the paper's words and captions a reader understands: the method as an
editable TikZ diagram (method_diagram), and every caption and plotting script checked
(caption_check)."""

from .captions import caption_check
from .method import method_diagram

__all__ = ["caption_check", "method_diagram"]

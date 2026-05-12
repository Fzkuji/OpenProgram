"""
pdf_figures — extract figures from academic PDFs by caption anchor.

A small deterministic application: locate captions like ``"Figure 2:"``
in a PDF and crop each figure to a PNG. No LLM call required.

Single entry function ``extract_pdf_figures`` for batch extraction;
``extract_one_figure`` for the single-figure case. See the docstring
of ``extract_one_figure`` for the algorithm and its limitations.
"""

from openprogram.programs.applications.pdf_figures.main import (
    FigureCrop,
    extract_one_figure,
    extract_pdf_figures,
)

__all__ = ["FigureCrop", "extract_one_figure", "extract_pdf_figures"]

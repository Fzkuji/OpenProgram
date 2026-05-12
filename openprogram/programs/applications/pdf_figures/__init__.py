"""
pdf_figures — VLM-verified PDF figure extraction.

Wraps the deterministic ``openprogram.tools.pdf.extract_figure`` with
a VLM verification + retry loop. The agent inspects each candidate
crop and re-renders with a corrected bbox if needed, so the caller
doesn't have to hand-check or re-tune.

Entry: :func:`extract_pdf_figures`.
"""

from openprogram.programs.applications.pdf_figures.main import (
    extract_pdf_figures,
)

__all__ = ["extract_pdf_figures"]

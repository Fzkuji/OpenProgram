"""Agent-facing language-server tool family.

Three ``@function`` LLM-callable tools over
:mod:`openprogram.lsp`, one per subdirectory:

  * ``lsp_diagnostics`` — type-checker errors and warnings for a file
  * ``lsp_references``  — every real use of a symbol
  * ``lsp_definition``  — a symbol's true declaration site

Coordinates shared by the tools are 1-based; ``shared.py`` converts to
LSP's 0-based positions. Self-register via @function on import.
"""
from .lsp_definition import lsp_definition
from .lsp_diagnostics import lsp_diagnostics
from .lsp_references import lsp_references

__all__ = [
    "lsp_diagnostics",
    "lsp_references",
    "lsp_definition",
]

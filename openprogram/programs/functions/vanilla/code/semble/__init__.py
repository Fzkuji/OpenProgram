"""semble tool family — semantic + lexical code search.

Two tools sharing one per-repo index cache (``shared.py``):
``semble_search`` (query → ranked chunks) and ``semble_find_related``
(file:line → similar chunks). Self-register via @function on import.
"""
from .semble_search import semble_search
from .semble_find_related import semble_find_related

__all__ = ["semble_search", "semble_find_related"]

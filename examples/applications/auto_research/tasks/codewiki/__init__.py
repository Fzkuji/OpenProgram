"""Module-level architecture docs for the paper's code (code_wiki): a static site whose
pages tie the paper's equations, algorithms and method subsections to the functions that
implement them, every link checked in code."""

from .workflow import ROLES, code_wiki, mapper, writer

__all__ = ["ROLES", "code_wiki", "mapper", "writer"]

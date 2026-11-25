"""Whether the works supporting each claim are independent of one another: works by
the same authors, linked by self-citation or built on one dataset count once."""

from .audit import source_independence

__all__ = ["source_independence"]

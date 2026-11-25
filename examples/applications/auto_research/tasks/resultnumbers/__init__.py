"""Checking the paper's own result numbers against the runs that produced them
(result_numbers): stale numbers, numbers from another run, metric or split, and derived
claims that no longer hold."""

from .workflow import ROLES, mapper, result_numbers

__all__ = ["ROLES", "mapper", "result_numbers"]

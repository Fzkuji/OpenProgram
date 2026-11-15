"""Autouse fixtures shared by this test family."""
from . import _support

# Shared pytest fixtures, bound at module level so pytest collects them here.
_worker_lock_is_held = _support._worker_lock_is_held

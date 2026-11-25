"""Power analysis before the runs: how many seeds each claimed comparison needs
(power_analysis), estimated from the pilot runs. Plain code, no model call."""

from .workflow import power_analysis

ROLES: dict = {}

__all__ = ["ROLES", "power_analysis"]

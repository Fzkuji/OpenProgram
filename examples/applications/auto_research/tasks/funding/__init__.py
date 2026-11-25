"""Funding: open calls that fit the project (funding_calls), and one call's requirements
as a checklist checked against the grant draft (call_requirements)."""

from .calls import funding_calls
from .requirements import call_requirements

__all__ = ["call_requirements", "funding_calls"]

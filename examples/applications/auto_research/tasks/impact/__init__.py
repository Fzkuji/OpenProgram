"""How far the paper may reach: its citations forecast from comparable published works
(impact_forecast), and its technology readiness and patent landscape (tech_transfer)."""

from .forecast import impact_forecast
from .transfer import tech_transfer

__all__ = ["impact_forecast", "tech_transfer"]

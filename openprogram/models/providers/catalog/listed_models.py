"""A model a provider lists, read from the live listing.

A run that names its model for one turn may use a model the provider lists
but no one enabled (``enabled_models.use_model_in_process``). The listing
that reads it (``openprogram.models.listing``) sits above the catalogue, so
the catalogue asks it through this port, which the models layer fills when
it loads (``openprogram.models.lower_layers``, through
``models.part_ports``). Without a registered reader no listed model is known.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from openprogram.models import part_ports

_providers: dict[str, Callable[..., Any]] = part_ports.filled(__name__)


def register(*, spec_row_for: Callable[[str, str], Optional[dict[str, Any]]]) -> None:
    """Install the reader of a listed model's spec row."""
    _providers.update(spec_row_for=spec_row_for)


def spec_row_for(provider_id: str, model_id: str) -> Optional[dict[str, Any]]:
    """The full spec row of ``model_id`` as ``provider_id`` lists it, as
    ``providers.<p>.models`` stores it, or None."""
    reader = _providers.get("spec_row_for")
    return reader(provider_id, model_id) if reader else None


__all__ = ["register", "spec_row_for"]

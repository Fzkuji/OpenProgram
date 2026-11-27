"""What credentials need from the provider adapters and their catalogue.

Credentials sit below the providers: an adapter sends with the credential
this package chooses, and this package never imports the adapters. A few
answers still live with the providers — the
refresh hooks the adapters register, what the catalogue says about a
provider (its label, its key's environment variable, its default API,
whether it runs locally) and whether an error says a balance ran out. They
arrive through this port, which the models layer fills when it loads
(``openprogram.models.lower_layers``, through ``models.part_ports``); each
behaviour imports its provider module when it is called, so a test that
replaces a provider function is what credentials see.

Without a registered behaviour each answer is the one of a process where no
provider adapter ever loaded: nothing is registered, the catalogue knows nothing and no error reads as a spent balance.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from openprogram.models import part_ports

_providers: dict[str, Callable[..., Any]] = part_ports.filled(__name__)

BEHAVIOURS = frozenset({
    "register_auth_adapters",
    "is_local_provider",
    "label_for",
    "catalogue_env_var",
    "default_api_for",
    "catalogue_providers",
    "is_billing_exhausted",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the providers' answer for each named behaviour."""
    unknown = set(providers) - BEHAVIOURS
    if unknown:
        raise ValueError(f"unknown provider behaviours: {sorted(unknown)}")
    _providers.update(providers)


def register_auth_adapters() -> None:
    """Have the provider adapters register their refresh hooks
    (``credential_provider.register_provider_config``)."""
    provider = _providers.get("register_auth_adapters")
    if provider:
        provider()


def is_local_provider(provider_id: str) -> bool:
    """Whether ``provider_id`` is a server on this machine (Ollama, LM Studio…)."""
    provider = _providers.get("is_local_provider")
    return bool(provider(provider_id)) if provider else False


def label_for(provider_id: str) -> Optional[str]:
    """The catalogue's display name for ``provider_id``."""
    provider = _providers.get("label_for")
    return provider(provider_id) if provider else None


def catalogue_env_var(provider_id: str) -> Optional[str]:
    """The environment variable the catalogue names for ``provider_id``'s key."""
    provider = _providers.get("catalogue_env_var")
    return provider(provider_id) if provider else None


def default_api_for(provider_id: str) -> Optional[str]:
    """The wire API ``provider_id``'s models speak unless one says otherwise."""
    provider = _providers.get("default_api_for")
    return provider(provider_id) if provider else None


def catalogue_providers() -> list[dict]:
    """Every provider the model catalogue lists (models.dev rows)."""
    provider = _providers.get("catalogue_providers")
    return list(provider()) if provider else []


def is_billing_exhausted(text: str) -> bool:
    """Whether an error's text says the balance or credit ran out."""
    provider = _providers.get("is_billing_exhausted")
    return bool(provider(text)) if provider else False


__all__ = sorted(BEHAVIOURS | {"BEHAVIOURS", "register"})

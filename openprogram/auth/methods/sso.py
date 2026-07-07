"""Enterprise SSO placeholder.

Reserved as the canonical integration point for SAML / OIDC-through-
broker / corporate identity providers. Not implemented: the protocols
vary too much between vendors (Okta, Azure AD, Ping, Auth0 as IdP,
custom) for a single generic implementation to be useful, and the
feature has no real users today.

What this stub guarantees:

  * the ``sso`` credential kind is already registered in
    :mod:`auth.types`, so code that branches on ``cred.kind`` handles
    it with a :class:`NotImplementedError` today and a real branch later
  * the :class:`SsoStubMethod` class exists so provider plugins that
    want to advertise "we support SSO, call us to wire it" can do so
    without their settings UI breaking on a missing import
  * docstring + ``run()`` raise a clear error pointing callers at the
    issue tracker so the failure mode is "go tell us what IdP you use",
    not "framework bug"

When a real implementation lands it should keep the :class:`LoginMethod`
interface unchanged — the plugin layer (provider_auth config) is the
only place that should need updating.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..types import (
    Credential,
    LoginMethod,
    LoginUi,
)


@dataclass
class SsoConfig:
    """Stub config. Fields expected to flesh out when a customer drives
    a real implementation. Intentionally minimal so changing it later
    doesn't break pickled configs — there's nothing to break yet."""

    broker: str = ""
    issuer: str = ""
    audience: str = ""
    extra: dict = field(default_factory=dict)


class SsoStubMethod(LoginMethod):
    """Does nothing yet — raises a clear error.

    Kept in the public ``auth/methods`` export so:
      1. ``from openprogram.auth.methods import SsoStubMethod`` works
         in provider plugins that want to declare the method exists
      2. the error message points integrators at a contact route
    """

    method_id = "sso"

    def __init__(
        self,
        provider_id: str,
        config: SsoConfig | None = None,
        *,
        profile_id: str = "default",
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config or SsoConfig()
        self._profile_id = profile_id

    async def run(self, ui: LoginUi) -> Credential:
        raise NotImplementedError(
            "Enterprise SSO login is not implemented yet. "
            "If you need this, please open an issue describing your "
            "IdP (Okta / Azure AD / Auth0 / …) and broker. "
            "The type system already accepts `kind='sso'` credentials, "
            "so the remaining work is the protocol-specific flow."
        )

"""The owner's outbound URL policy as stored in ``config.json``.

``security.outbound_url`` holds owner-approved private-network exceptions
(each scoped to one consumer of ``safe_http``) and an optional policy proxy
that enforces target policy itself. This module parses that value strictly
into an immutable model and turns it into the ``OutboundSecurityConfig`` a
consumer's client runs with. The settings registry (``state.settings``)
validates and normalises the stored value through the rules this layer
registers for the key (``host.lower_layers``).
"""
from __future__ import annotations

import ipaddress
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from openprogram.host.security import http_proxy
from openprogram.host.security.url_policy import OwnerURLException, normalize_url
from openprogram.state import config as user_config

if TYPE_CHECKING:
    from openprogram.host.security.safe_http import OutboundSecurityConfig

_METADATA_ADDRESSES = tuple(
    ipaddress.ip_address(value)
    for value in (
        "100.100.100.200",
        "169.254.169.254",
        "169.254.170.2",
        "fd00:ec2::254",
    )
)
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }
)
_LINK_LOCAL_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)


class OwnerURLExceptionSetting(BaseModel):
    """One owner-approved, consumer-scoped private-network exception."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    consumer: str = Field(min_length=1, max_length=128)
    origin: str | None = Field(default=None, max_length=2048)
    cidr: str | None = Field(default=None, max_length=64)

    @field_validator("consumer")
    @classmethod
    def _registered_exception_consumer(cls, value: str) -> str:
        from openprogram.host.security.safe_http import CONSUMER_REGISTRY

        spec = CONSUMER_REGISTRY.get(value)
        if spec is None or not spec.allow_owner_exceptions:
            raise ValueError("consumer does not allow owner URL exceptions")
        return value

    @field_validator("origin")
    @classmethod
    def _exact_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        normalized = normalize_url(value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or value.strip().startswith(".")
            or "*" in value
        ):
            raise ValueError("origin must contain only an exact authority")
        if normalized.hostname in _METADATA_HOSTS:
            raise ValueError("metadata origin is forbidden")
        try:
            address = ipaddress.ip_address(normalized.hostname)
        except ValueError:
            pass
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address.is_link_local or address in _METADATA_ADDRESSES:
                raise ValueError("metadata or link-local origin is forbidden")
        return normalized.origin

    @field_validator("cidr")
    @classmethod
    def _bounded_network(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("invalid CIDR") from exc
        if network.prefixlen == 0:
            raise ValueError("default-route CIDR is forbidden")
        if network.is_global or network.is_multicast:
            raise ValueError("owner CIDR must be private or local")
        if any(
            network.version == blocked.version and network.overlaps(blocked)
            for blocked in _LINK_LOCAL_NETWORKS
        ):
            raise ValueError("link-local CIDR is forbidden")
        if any(
            address.version == network.version and address in network
            for address in _METADATA_ADDRESSES
        ):
            raise ValueError("metadata CIDR is forbidden")
        return network.with_prefixlen

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.origin is None) == (self.cidr is None):
            raise ValueError("exception requires exactly one of origin or cidr")
        return self


class PolicyProxySetting(BaseModel):
    """Owner assertion for a proxy that enforces target URL policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(max_length=2048)
    enforces_target_policy: StrictBool

    @field_validator("url")
    @classmethod
    def _exact_proxy_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        normalized = normalize_url(value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("proxy URL must contain only an exact authority")
        if normalized.hostname in _METADATA_HOSTS:
            raise ValueError("metadata proxy is forbidden")
        try:
            address = ipaddress.ip_address(normalized.hostname)
        except ValueError:
            pass
        else:
            if address.is_link_local or address in _METADATA_ADDRESSES:
                raise ValueError("metadata or link-local proxy is forbidden")
        return normalized.origin

    @model_validator(mode="after")
    def _requires_enforcement_assertion(self):
        if self.enforces_target_policy is not True:
            raise ValueError("policy proxy must assert target-policy enforcement")
        return self


class OutboundURLSettings(BaseModel):
    """Strict, immutable owner configuration for Runtime URL access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exceptions: tuple[OwnerURLExceptionSetting, ...] = Field(
        default=(), max_length=64
    )
    policy_proxy: PolicyProxySetting | None = None
    model_tools: Literal["open", "strict"] = "open"

    @model_validator(mode="after")
    def _unique_exceptions(self):
        keys = {
            (item.consumer, item.origin, item.cidr)
            for item in self.exceptions
        }
        if len(keys) != len(self.exceptions):
            raise ValueError("duplicate owner URL exception")
        return self

    def security_for(self, consumer: str) -> OutboundSecurityConfig:
        from openprogram.host.security.safe_http import (
            CONSUMER_REGISTRY,
            OutboundSecurityConfig,
            PolicyProxyConfig,
        )

        if consumer not in CONSUMER_REGISTRY:
            raise KeyError(consumer)
        exceptions = tuple(
            OwnerURLException(
                consumer=item.consumer,
                origin=item.origin,
                network=(
                    ipaddress.ip_network(item.cidr)
                    if item.cidr is not None
                    else None
                ),
            )
            for item in self.exceptions
            if item.consumer == consumer
        )
        if self.policy_proxy is not None:
            exceptions += (
                OwnerURLException(
                    consumer="runtime.local_probe",
                    origin=self.policy_proxy.url,
                ),
            )
        proxy = (
            PolicyProxyConfig(
                url=self.policy_proxy.url,
                enforces_target_policy=True,
            )
            if self.policy_proxy is not None
            else None
        )
        return OutboundSecurityConfig(
            owner_exceptions=exceptions,
            policy_proxy=proxy,
        )


def parse_outbound_url_settings(value: Any) -> OutboundURLSettings:
    """Parse owner URL policy without reflecting rejected input in errors."""

    try:
        return OutboundURLSettings.model_validate(value)
    except Exception:
        raise ValueError("invalid outbound URL security configuration") from None


def load_outbound_security_config(
    consumer: str,
    *,
    config: dict[str, Any] | None = None,
) -> OutboundSecurityConfig:
    cfg = user_config.read_config() if config is None else config
    security = cfg.get("security", {})
    if not isinstance(security, dict):
        raise ValueError("invalid outbound URL security configuration")
    raw = security.get("outbound_url", {})
    result = parse_outbound_url_settings(raw).security_for(consumer)
    from openprogram.host.security.safe_http import uses_ambient_proxy

    if uses_ambient_proxy(consumer) and result.policy_proxy is None:
        result = replace(
            result,
            service_proxy_mounts=tuple(sorted((http_proxy.get_proxy_mounts() or {}).items())),
        )
    return result


def validation_error(value: Any) -> str | None:
    """The settings registry's check for ``security.outbound_url``."""
    try:
        parse_outbound_url_settings(value)
    except ValueError:
        return "invalid outbound URL security configuration"
    return None


def normalized(value: Any) -> Any:
    """The stored form of a valid ``security.outbound_url`` value."""
    return parse_outbound_url_settings(value).model_dump(mode="json", exclude_none=True)


__all__ = [
    "OutboundURLSettings",
    "OwnerURLExceptionSetting",
    "PolicyProxySetting",
    "load_outbound_security_config",
    "normalized",
    "parse_outbound_url_settings",
    "validation_error",
]

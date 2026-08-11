"""Pure URL normalization and address policy for Runtime-owned HTTP."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
ResolverAnswer = str | IPAddress | tuple
Resolver = Callable[[str, int], Iterable[ResolverAnswer]]

_DEFAULT_PORTS = {"http": 80, "https": 443}
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_HOST = re.compile(r"(?i)^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$")
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }
)
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_SPECIAL_NON_GLOBAL_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(value)
    for value in (
        "100.64.0.0/10",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "2001:db8::/32",
    )
)


class URLTrustClass(str, Enum):
    UNTRUSTED_PUBLIC = "untrusted_public"
    FIXED_PUBLIC_SERVICE = "fixed_public_service"
    CONFIGURED_SERVICE = "configured_service"
    LOOPBACK_CALLBACK = "loopback_callback"


@dataclass(frozen=True)
class OwnerURLException:
    consumer: str
    origin: str | None = None
    network: IPNetwork | None = None


@dataclass(frozen=True)
class NormalizedURL:
    normalized_url: str
    origin: str
    scheme: str
    hostname: str
    port: int
    safe_url: str


@dataclass(frozen=True)
class URLDecision:
    consumer: str
    method: str
    normalized_url: str
    origin: str
    hostname: str
    port: int
    resolved_ips: tuple[IPAddress, ...]
    trust_class: URLTrustClass


class URLPolicyError(ValueError):
    def __init__(self, reason: str, safe_url: str):
        self.reason = reason
        self.safe_url = safe_url
        super().__init__(f"{reason}: {safe_url}")


def _invalid_safe_url(url: object) -> str:
    if not isinstance(url, str) or not url or "\\" in url:
        return "<invalid-url>"
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in url):
        return "<invalid-url>"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return "<invalid-url>"
    if not hostname:
        return "<invalid-url>"
    hostname = hostname.lower().removesuffix(".")
    try:
        canonical = ipaddress.ip_address(hostname).compressed
    except ValueError:
        try:
            canonical = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return "<invalid-url>"
    authority = f"[{canonical}]" if ":" in canonical else canonical
    try:
        port = parsed.port
    except ValueError:
        port = None
    scheme = parsed.scheme.lower()
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        authority = f"{authority}:{port}"
    return urlunsplit((scheme, authority, "", "", ""))


def _raise(reason: str, url: object, safe_url: str | None = None) -> None:
    raise URLPolicyError(reason, safe_url or _invalid_safe_url(url))


def _has_control_character(url: str) -> bool:
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in url):
        return True
    return any(
        int(match.group(1), 16) <= 0x1F or int(match.group(1), 16) == 0x7F
        for match in re.finditer(r"%([0-9a-fA-F]{2})", url)
    )


def _looks_ambiguous_host(hostname: str) -> bool:
    if not _NUMERIC_HOST.fullmatch(hostname):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return False


def _canonical_hostname(hostname: str, url: str) -> str:
    hostname = hostname.removesuffix(".")
    if not hostname:
        _raise("INVALID_HOST", url)
    if "%" in hostname:
        _raise("ZONE_ID_FORBIDDEN", url)
    if _looks_ambiguous_host(hostname):
        _raise("AMBIGUOUS_HOST", url)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            canonical = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            _raise("INVALID_HOST", url)
        if len(canonical) > 253 or any(
            not _DNS_LABEL.fullmatch(label) for label in canonical.split(".")
        ):
            _raise("INVALID_HOST", url)
        return canonical
    return literal.compressed.lower()


def _authority(scheme: str, hostname: str, port: int) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port == _DEFAULT_PORTS[scheme]:
        return host
    return f"{host}:{port}"


def normalize_url(url: str) -> NormalizedURL:
    if not isinstance(url, str) or not url:
        _raise("INVALID_URL", url)
    if "\\" in url:
        _raise("INVALID_URL", url)
    if _has_control_character(url):
        _raise("CONTROL_CHARACTER", url, "<invalid-url>")
    try:
        parsed = urlsplit(url)
    except ValueError:
        _raise("INVALID_URL", url)
    scheme = parsed.scheme.lower()
    if not scheme:
        _raise("INVALID_URL", url)
    if scheme not in _DEFAULT_PORTS:
        _raise("SCHEME_FORBIDDEN", url)
    if parsed.username is not None or parsed.password is not None:
        _raise("USERINFO_FORBIDDEN", url)
    try:
        hostname = parsed.hostname
    except ValueError:
        _raise("INVALID_URL", url)
    if hostname is None:
        _raise("INVALID_HOST", url)
    hostname = _canonical_hostname(hostname, url)
    try:
        port = parsed.port
    except ValueError:
        _raise("INVALID_PORT", url)
    authority_without_userinfo = parsed.netloc.rsplit("@", 1)[-1]
    if authority_without_userinfo.endswith(":") or port == 0:
        _raise("INVALID_PORT", url)
    port = _DEFAULT_PORTS[scheme] if port is None else port
    authority = _authority(scheme, hostname, port)
    normalized_url = urlunsplit(
        (scheme, authority, parsed.path, parsed.query, parsed.fragment)
    )
    origin = f"{scheme}://{authority}"
    safe_url = origin
    return NormalizedURL(
        normalized_url=normalized_url,
        origin=origin,
        scheme=scheme,
        hostname=hostname,
        port=port,
        safe_url=safe_url,
    )


def normalize_origin(url: str) -> str:
    return normalize_url(url).origin


def resolve_all(hostname: str, port: int) -> tuple[str, ...]:
    results = socket.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(result[4][0] for result in results)


def _canonical_ip(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _resolver_address(answer: ResolverAnswer) -> IPAddress:
    if isinstance(answer, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return _canonical_ip(answer)
    value: object = answer
    if isinstance(answer, tuple) and len(answer) >= 5 and isinstance(answer[4], tuple):
        value = answer[4][0]
    if not isinstance(value, str) or "%" in value:
        raise ValueError("invalid resolver answer")
    return _canonical_ip(ipaddress.ip_address(value))


def _resolve(normalized: NormalizedURL, resolver: Resolver) -> tuple[IPAddress, ...]:
    try:
        literal = ipaddress.ip_address(normalized.hostname)
    except ValueError:
        try:
            raw_answers = resolver(normalized.hostname, normalized.port)
            deduplicated = tuple(
                dict.fromkeys(_resolver_address(item) for item in raw_answers)
            )
        except Exception as exc:
            raise URLPolicyError("DNS_ERROR", normalized.safe_url) from exc
    else:
        deduplicated = (_canonical_ip(literal),)
    if not deduplicated:
        raise URLPolicyError("DNS_EMPTY_RESULT", normalized.safe_url)
    return deduplicated


def _is_non_global(address: IPAddress) -> bool:
    return (
        not address.is_global
        or address.is_link_local
        or address.is_loopback
        or address.is_multicast
        or address.is_private
        or address.is_reserved
        or address.is_unspecified
        or any(
            address.version == network.version and address in network
            for network in _SPECIAL_NON_GLOBAL_NETWORKS
        )
    )


def _exception_allows(
    exception: OwnerURLException,
    consumer: str,
    normalized: NormalizedURL,
    address: IPAddress,
) -> bool:
    if exception.consumer != consumer:
        return False
    if exception.origin is not None:
        try:
            if normalize_origin(exception.origin) == normalized.origin:
                return True
        except URLPolicyError:
            pass
    network = exception.network
    return bool(
        network is not None
        and network.version == address.version
        and address in network
    )


def _is_metadata(normalized: NormalizedURL, address: IPAddress) -> bool:
    return normalized.hostname in _METADATA_HOSTS or address in _METADATA_ADDRESSES


def _validate_addresses(
    consumer: str,
    normalized: NormalizedURL,
    addresses: tuple[IPAddress, ...],
    trust_class: URLTrustClass,
    exceptions: tuple[OwnerURLException, ...],
    callback_origin: str | None,
) -> None:
    for address in addresses:
        if _is_metadata(normalized, address):
            raise URLPolicyError("METADATA_ADDRESS", normalized.safe_url)

    if trust_class in {
        URLTrustClass.UNTRUSTED_PUBLIC,
        URLTrustClass.FIXED_PUBLIC_SERVICE,
    }:
        for address in addresses:
            if _is_non_global(address):
                raise URLPolicyError("NON_GLOBAL_ADDRESS", normalized.safe_url)
        return

    if trust_class == URLTrustClass.CONFIGURED_SERVICE:
        for address in addresses:
            if _is_non_global(address) and not any(
                _exception_allows(exception, consumer, normalized, address)
                for exception in exceptions
            ):
                raise URLPolicyError("NON_GLOBAL_ADDRESS", normalized.safe_url)
        return

    if trust_class == URLTrustClass.LOOPBACK_CALLBACK:
        if callback_origin is None:
            raise URLPolicyError("CALLBACK_ORIGIN_REQUIRED", normalized.safe_url)
        callback = normalize_url(callback_origin)
        try:
            expected = _canonical_ip(ipaddress.ip_address(callback.hostname))
        except ValueError as exc:
            raise URLPolicyError("CALLBACK_ORIGIN_INVALID", callback.safe_url) from exc
        if not expected.is_loopback or any(
            address != expected for address in addresses
        ):
            raise URLPolicyError("CALLBACK_ADDRESS_MISMATCH", normalized.safe_url)


def evaluate_url(
    consumer: str,
    method: str,
    url: str,
    *,
    trust_class: URLTrustClass = URLTrustClass.UNTRUSTED_PUBLIC,
    allowed_schemes: frozenset[str] = frozenset({"http", "https"}),
    allowed_methods: frozenset[str] = frozenset({"GET", "HEAD"}),
    allowed_ports: frozenset[int] | None = frozenset({80, 443}),
    fixed_origins: frozenset[str] = frozenset(),
    configured_origin: str | None = None,
    callback_origin: str | None = None,
    exceptions: tuple[OwnerURLException, ...] = (),
    resolver: Resolver = resolve_all,
) -> URLDecision:
    normalized = normalize_url(url)
    if normalized.scheme not in allowed_schemes:
        raise URLPolicyError("SCHEME_FORBIDDEN", normalized.safe_url)
    if exceptions and trust_class != URLTrustClass.CONFIGURED_SERVICE:
        raise URLPolicyError("EXCEPTIONS_FORBIDDEN", normalized.safe_url)
    normalized_method = method.upper()
    if normalized_method not in allowed_methods:
        raise URLPolicyError("METHOD_FORBIDDEN", normalized.safe_url)
    if allowed_ports is not None and normalized.port not in allowed_ports:
        raise URLPolicyError("PORT_FORBIDDEN", normalized.safe_url)

    if trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE:
        if not fixed_origins:
            raise URLPolicyError("FIXED_ORIGINS_REQUIRED", normalized.safe_url)
        if normalized.origin not in {
            normalize_origin(origin) for origin in fixed_origins
        }:
            raise URLPolicyError("FIXED_ORIGIN_MISMATCH", normalized.safe_url)
    elif trust_class == URLTrustClass.CONFIGURED_SERVICE:
        if configured_origin is None:
            raise URLPolicyError("CONFIGURED_ORIGIN_REQUIRED", normalized.safe_url)
        if normalized.origin != normalize_origin(configured_origin):
            raise URLPolicyError("CONFIGURED_ORIGIN_MISMATCH", normalized.safe_url)
    elif trust_class == URLTrustClass.LOOPBACK_CALLBACK:
        if callback_origin is None:
            raise URLPolicyError("CALLBACK_ORIGIN_REQUIRED", normalized.safe_url)
        if normalized.origin != normalize_origin(callback_origin):
            raise URLPolicyError("CALLBACK_ORIGIN_MISMATCH", normalized.safe_url)

    addresses = _resolve(normalized, resolver)
    _validate_addresses(
        consumer,
        normalized,
        addresses,
        trust_class,
        exceptions,
        callback_origin,
    )
    return URLDecision(
        consumer=consumer,
        method=normalized_method,
        normalized_url=normalized.normalized_url,
        origin=normalized.origin,
        hostname=normalized.hostname,
        port=normalized.port,
        resolved_ips=addresses,
        trust_class=trust_class,
    )


__all__ = [
    "NormalizedURL",
    "OwnerURLException",
    "Resolver",
    "URLDecision",
    "URLPolicyError",
    "URLTrustClass",
    "evaluate_url",
    "normalize_origin",
    "normalize_url",
    "resolve_all",
]

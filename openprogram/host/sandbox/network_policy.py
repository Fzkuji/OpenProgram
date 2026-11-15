"""Destination policy for managed sandbox HTTP proxies."""
from __future__ import annotations

import ipaddress
import re


def canonical_host(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('network host must be a nonempty hostname')
    value = value.removesuffix('.').lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    value = value.encode('idna').decode('ascii')
    labels = value.split('.')
    if len(value) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('invalid network hostname')
    return value


def parse_domains(value) -> tuple[tuple[str, str], ...] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('network_domains must be null or a hostname-to-allow/deny object')
    result = {}
    for raw, decision in value.items():
        if not isinstance(raw, str) or decision not in ('allow', 'deny'):
            raise ValueError('network domains require allow or deny decisions')
        wildcard = raw.startswith('*.')
        host = canonical_host(raw[2:] if wildcard else raw)
        if wildcard and (len(host.split('.')) < 2 or ':' in host or host.replace('.', '').isdigit()):
            raise ValueError('wildcards must name a scoped domain such as *.example.com')
        key = '*.' + host if wildcard else host
        if key in result and result[key] != decision:
            raise ValueError('conflicting canonical domain entries')
        result[key] = decision
    return tuple(sorted(result.items()))


def validation_error(value) -> str | None:
    try:
        parse_domains(value)
    except (ValueError, UnicodeError) as exc:
        return str(exc)
    return None


def permits(domains: tuple[tuple[str, str], ...], host: str) -> bool:
    host = canonical_host(host)
    decisions = [decision for pattern, decision in domains
                 if host == pattern or (pattern.startswith('*.') and host.endswith(pattern[1:]))]
    return 'allow' in decisions and 'deny' not in decisions


def public_addresses(records) -> list[tuple]:
    """Reject mixed public/private DNS answers, then connect to these exact IPs."""
    if not records or any(not ipaddress.ip_address(item[4][0]).is_global or ipaddress.ip_address(item[4][0]).is_multicast
                          for item in records):
        raise PermissionError('nonpublic network address')
    return list(records)

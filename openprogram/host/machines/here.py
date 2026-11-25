"""Whether an address reaches this computer.

This computer is built in as ``local``; a machine that is this computer
under another name would give it a second address. Connection methods ask
here when a machine is saved. Only the local
resolver is asked: nothing goes out on the network, and a name the resolver
cannot answer for is not taken to be this computer.
"""
from __future__ import annotations

import ipaddress
import socket


def _address(text: str):
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


def is_loopback(host: str | None) -> bool:
    """A loopback address or name, read without resolving anything."""
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    address = _address(name)
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def _resolve(host: str) -> set[str]:
    """The addresses the local resolver gives for ``host``; none when it cannot answer."""
    try:
        found = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return set()
    return {str(item[4][0]).split("%", 1)[0] for item in found}


def own_names() -> set[str]:
    """This computer's own host names, lower-cased: as set, without its domain, and on the local network."""
    names: set[str] = set()
    try:
        full = socket.gethostname().strip().rstrip(".").lower()
    except OSError:
        full = ""
    if full:
        short = full.split(".", 1)[0]
        names |= {full, short, f"{short}.local"}
    return names


def own_addresses() -> set[str]:
    """The addresses this computer's own host names resolve to."""
    found: set[str] = set()
    for name in own_names():
        found |= _resolve(name)
    return {address for address in found if not is_loopback(address)}


def is_this_computer(host: str | None) -> bool:
    """Whether ``host`` names or resolves to this computer: loopback, its own names, or its own addresses."""
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if is_loopback(name) or name in own_names():
        return True
    addresses = {name} if _address(name) is not None else _resolve(name)
    if not addresses:
        return False
    return any(is_loopback(address) for address in addresses) or bool(addresses & own_addresses())


__all__ = ["is_loopback", "is_this_computer", "own_addresses", "own_names"]

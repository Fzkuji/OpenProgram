"""Another OpenProgram: a machine reached through its own Worker.

The connection method ``openprogram`` holds where the other computer's
Worker answers and the device credential it gave this computer when the two
were paired. Its conversations, files and accounts stay there; this computer
lists them and forwards what is done to them
(``openprogram.interfaces.server.remote_machines``), and ``computer_use``
forwards its calls there whole, to run on that computer
(``computer_use/providers/paired.py``).

A pairing link is what the other computer's Settings → Devices → Add device shows:
``http://192.168.1.20:18100/#pair=<code>``, with ``&via=<origin>`` once for
each other address it answers at (its public ts.net address, say). The first
address that answers redeems the code, and every address is kept: each
connection tries them in order, so a laptop that leaves the home network
reaches the same computer at that address.

Design: docs/reference/design/integrations/remote-openprogram.html
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from openprogram.host.machines import here
from openprogram.host.machines.kinds import Kind, KindError, register

#: Seconds one address may take to answer before the next is tried.
TIMEOUT_SECONDS = 6.0
#: The connection method of a paired OpenProgram.
METHOD = "openprogram"


class PairingError(ValueError):
    """A link that is not a pairing link, or a code no address accepted."""


@dataclass(frozen=True)
class PairingLink:
    routes: tuple[str, ...]
    code: str


def _origin(text: str) -> str:
    parts = urlsplit(str(text or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def parse_link(link: str) -> PairingLink:
    """The addresses and the code in a pairing link."""
    text = str(link or "").strip()
    first = _origin(text)
    fragment = urlsplit(text).fragment
    params = parse_qs(fragment, keep_blank_values=False)
    code = (params.get("pair") or [""])[0].strip()
    if not first or not code:
        raise PairingError("Paste the whole link the other computer shows in Settings → Devices → Add device.")
    routes = [first]
    for extra in params.get("via") or []:
        origin = _origin(extra)
        if origin and origin not in routes:
            routes.append(origin)
    return PairingLink(tuple(routes), code)


def build_link(routes: list[str], code: str) -> str:
    """The link another device opens: the first address, the code, then each other address."""
    first, *rest = routes
    return f"{first}/#pair={code}" + "".join(f"&via={quote(route, safe='')}" for route in rest)


def device_label() -> str:
    try:
        name = socket.gethostname().split(".", 1)[0].strip()
    except OSError:
        name = ""
    return f"OpenProgram on {name}" if name else "OpenProgram"


def routes_of(settings: dict) -> list[str]:
    """Every address of a connection, the main one first."""
    found = [str(settings.get("url") or "")]
    found += [str(route) for route in settings.get("routes") or []]
    seen: list[str] = []
    for route in found:
        origin = _origin(route)
        if origin and origin not in seen:
            seen.append(origin)
    return seen


def _is_self(route: str, own_port: int | None) -> bool:
    """The address is this very OpenProgram: this computer, at the port this Worker listens on.

    Another OpenProgram on this computer (a second profile) answers at another port and may be paired.
    """
    parts = urlsplit(route)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return here.is_this_computer(parts.hostname) and (own_port is None or port == own_port)


def redeem(link: str, *, own_port: int | None = None) -> dict:
    """Redeem a pairing link: ``{"url", "routes", "token", "device_id"}`` from the first address that answers."""
    import httpx

    parsed = parse_link(link)
    for route in parsed.routes:
        if _is_self(route, own_port):
            raise PairingError("That link is this computer's own; open it on another computer.")
    last = ""
    for route in parsed.routes:
        try:
            response = httpx.post(f"{route}/api/auth/pair", json={"code": parsed.code, "label": device_label()},
                                  timeout=TIMEOUT_SECONDS, trust_env=False)
        except httpx.HTTPError as exc:
            last = f"{route} did not answer ({exc.__class__.__name__})"
            continue
        if response.status_code == 401:
            raise PairingError("The code was used or has expired. Make a new one on the other computer.")
        if response.status_code != 200:
            last = f"{route} answered {response.status_code}"
            continue
        body = response.json()
        rest = [item for item in parsed.routes if item != route]
        return {"url": route, "routes": rest, "token": str(body["token"]), "device_id": str(body["device_id"])}
    raise PairingError(f"No address of the other computer answered: {last}.")


def is_paired(machine) -> bool:
    """The machine is another computer's OpenProgram, reached by pairing."""
    return machine is not None and machine.method == METHOD


def credential(machine) -> tuple[list[str], str]:
    """Every address of a paired machine, the one that answered last first, and its device credential."""
    from openprogram.host.machines import secret

    if not is_paired(machine):
        return [], ""
    return routes_of(machine.connection.settings), secret(machine, "token")


def reachable(machine) -> tuple[str, str]:
    """``(route, "")`` for the first address that accepts this computer's credential, or ``("", why)``."""
    import httpx

    if not is_paired(machine):
        return "", "no OpenProgram connection"
    routes, token = credential(machine)
    if not token:
        return "", "not paired"
    reasons = []
    for route in routes:
        try:
            response = httpx.get(f"{route}/api/auth/session", headers={"Authorization": f"Bearer {token}"},
                                 timeout=TIMEOUT_SECONDS, trust_env=False)
        except httpx.HTTPError as exc:
            reasons.append(f"{route}: {exc.__class__.__name__}")
            continue
        if response.status_code == 204:
            return route, ""
        if response.status_code == 401:
            return "", "the other computer revoked this one; pair again"
        reasons.append(f"{route}: {response.status_code}")
    return "", "; ".join(reasons) or "no address"


def _check(machine) -> tuple[bool, str]:
    route, why = reachable(machine)
    return (True, route) if route else (False, why)


def _normalize(settings: dict) -> dict:
    routes = routes_of(settings)
    if not routes:
        raise KindError("openprogram: url must be http:// or https:// with a host")
    out = {**settings, "url": routes[0]}
    if routes[1:]:
        out["routes"] = routes[1:]
    else:
        out.pop("routes", None)
    return out


register(Kind(
    name=METHOD, label="OpenProgram", label_zh="OpenProgram", paired=True,
    settings={
        "type": "object",
        "description": "Another computer's OpenProgram, paired from its Settings → Devices → Add device.",
        "x-zh": {"description": "另一台电脑上的 OpenProgram，在它的 设置 → 远程访问 里配对。"},
        "properties": {
            "url": {"type": "string", "title": "Address", "x-zh": {"title": "地址"},
                    "examples": ["http://192.168.1.20:18100"]},
            "routes": {"type": "array", "items": {"type": "string"}, "title": "Other addresses",
                       "x-zh": {"title": "其他地址"}},
            "device_id": {"type": "string", "title": "Device ID", "x-zh": {"title": "设备 ID"}},
            "token": {"type": "string", "secret": True, "title": "Credential", "x-zh": {"title": "凭据"}},
        },
        "required": ["url", "token"],
    },
    check=_check, normalize=_normalize,
    where=lambda settings: (routes_of(settings) or [""])[0]))


__all__ = ["METHOD", "PairingError", "PairingLink", "TIMEOUT_SECONDS", "build_link", "credential", "device_label",
           "is_paired", "parse_link", "reachable", "redeem", "routes_of"]

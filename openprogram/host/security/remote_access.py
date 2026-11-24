"""Where other devices reach this computer's OpenProgram: the local network and Tailscale.

Two ways in, each switched on from Settings → Remote access:

* **This network.** The Worker listens on every interface (``web.host``
  ``0.0.0.0``) and answers at this computer's network address
  (``web.allowed_origins``). Plain HTTP, so only for a network one trusts;
  it applies when the Worker next starts.
* **Tailscale.** Tailscale Serve, run by the Tailscale on this computer,
  answers ``https://<name>.<tailnet>.ts.net`` and passes requests to the
  Worker on loopback. Encrypted end to end, reachable from every device
  on the same tailnet wherever it is, nothing opened to the internet, and
  the Worker keeps listening on loopback only, so it applies at once.

Either way another device still pairs once (``device_access``); being on
the network is never enough to sign in. This module only finds the
addresses and runs the ``tailscale`` command; it never edits the
configuration.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path

#: Where the Tailscale app keeps its command on macOS when it is not on PATH.
_MAC_APP_CLI = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
_TIMEOUT = 8


#: Home and office networks. A VPN or proxy tunnel (198.18.0.0/15) and Tailscale (100.64.0.0/10) are not
#: the local network.
_LAN_NETWORKS = tuple(ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _lan(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return address.version == 4 and any(address in net for net in _LAN_NETWORKS)


def lan_address() -> str:
    """This computer's address on its local network, or "" when it has none.

    The route a datagram would take picks the interface (nothing is sent);
    when a tunnel holds that route, the addresses this computer's own name
    resolves to locally (``<name>.local`` included) are asked instead.
    """
    candidates: list[str] = []
    for probe in ("192.168.0.1", "10.0.0.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))
                candidates.append(sock.getsockname()[0])
        except OSError:
            continue
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    for host in (name, f"{name.split('.', 1)[0]}.local") if name else ():
        try:
            candidates += [item[4][0] for item in socket.getaddrinfo(host, None, socket.AF_INET)]
        except (OSError, UnicodeError):
            continue
    return next((address for address in candidates if _lan(address)), "")


def lan_origin(port: int) -> str:
    address = lan_address()
    return f"http://{address}:{port}" if address else ""


def tailscale_cli() -> str:
    """The ``tailscale`` command, or "" when Tailscale is not installed."""
    found = shutil.which("tailscale")
    if found:
        return found
    return str(_MAC_APP_CLI) if _MAC_APP_CLI.exists() else ""


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([tailscale_cli(), *args], capture_output=True, text=True, timeout=_TIMEOUT,
                          check=False)


def tailscale_status() -> dict:
    """``{"installed", "running", "name", "origin"}``: the name this computer has on its tailnet."""
    if not tailscale_cli():
        return {"installed": False, "running": False, "name": "", "origin": ""}
    try:
        result = _run("status", "--json")
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"installed": True, "running": False, "name": "", "origin": ""}
    name = str(((data.get("Self") or {}).get("DNSName") or "")).rstrip(".")
    running = data.get("BackendState") == "Running" and bool(name)
    return {"installed": True, "running": running, "name": name if running else "",
            "origin": f"https://{name}" if running else ""}


def tailscale_serving(port: int) -> bool:
    """Whether Tailscale Serve passes this tailnet name's HTTPS to the Worker's port."""
    if not tailscale_cli():
        return False
    try:
        result = _run("serve", "status", "--json")
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    target = f"127.0.0.1:{port}"
    for web in (data.get("Web") or {}).values():
        for handler in ((web or {}).get("Handlers") or {}).values():
            if target in str((handler or {}).get("Proxy") or ""):
                return True
    return False


def set_tailscale_serve(port: int, on: bool) -> str:
    """Switch Tailscale Serve for the Worker on or off; "" when done, else what Tailscale said."""
    if not tailscale_cli():
        return "Tailscale is not installed."
    args = ("serve", "--bg", "--https=443", f"http://127.0.0.1:{port}") if on else ("serve", "--https=443", "off")
    try:
        result = _run(*args)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode == 0:
        return ""
    return (result.stderr or result.stdout or "Tailscale refused.").strip().splitlines()[-1]


__all__ = ["lan_address", "lan_origin", "set_tailscale_serve", "tailscale_cli", "tailscale_serving",
           "tailscale_status"]

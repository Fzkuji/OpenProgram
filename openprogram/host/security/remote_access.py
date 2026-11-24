"""Where other devices reach this computer's OpenProgram: the local network and anywhere.

Two ways in, each switched on from Settings → Devices:

* **Same Wi-Fi.** A second listener on this computer's local-network
  address (``interfaces/server/lan_listener.py``, ``web.lan``). Plain HTTP,
  so only for a network one trusts.
* **Anywhere.** ``openprogram-tailnet`` (``apps/tailnet``), shipped in the
  runtime, embeds Tailscale (tsnet) and publishes the Worker through
  Tailscale Funnel at ``https://<name>.<tailnet>.ts.net``. Nothing else is
  installed: the owner signs in to Tailscale once in a browser, and a phone
  opens the address in its own browser with no app. The Worker runs it as a
  child, reads its state lines, and adds the address to the Origins the
  running server answers (``OwnerAuthState.allow_origins``); the Worker
  keeps listening on loopback only, so it applies at once. ``web.anywhere``
  remembers the switch, and the Worker starts the sidecar with it.

Either way another device still pairs once (``device_access``); reaching
the address is never enough to sign in.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

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


# ---------------------------------------------------------------------------
# Anywhere: the embedded Tailscale sidecar
# ---------------------------------------------------------------------------

#: The states the sidecar reports, plus "off" when it is not wanted.
STATES = ("off", "starting", "needs_login", "needs_funnel", "running", "error")
BINARY = "openprogram-tailnet"
_STOP_SECONDS = 5.0


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def go_target() -> str:
    """This machine as Go names it: ``darwin-arm64``, ``windows-amd64``..."""
    system = {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def _runtime_roots() -> list[Path]:
    """Runtimes this Worker runs from: directories with a ``runtime-manifest.json``."""
    found: list[Path] = []
    for start in (Path(sys.executable), Path(sys.prefix), Path(__file__)):
        try:
            start = start.resolve()
        except OSError:
            continue
        for parent in (start.parent, *start.parents):
            if (parent / "runtime-manifest.json").is_file():
                if parent not in found:
                    found.append(parent)
                break
    return found


def binary_path() -> Path | None:
    """The sidecar: ``OPENPROGRAM_TAILNET_BIN``, the runtime's ``bin/``, or a source build."""
    override = os.environ.get("OPENPROGRAM_TAILNET_BIN", "").strip()
    candidates = [Path(override)] if override else []
    candidates += [root / "bin" / _exe(BINARY) for root in _runtime_roots()]
    # Running from a checkout: scripts/release/build-tailnet.py writes here.
    repo = Path(__file__).resolve().parents[3]
    candidates.append(repo / "apps" / "tailnet" / "dist" / go_target() / _exe(BINARY))
    return next((path for path in candidates if path.is_file()), None)


def state_dir() -> Path:
    """This profile's Tailscale identity, beside the rest of its state."""
    from openprogram.state.paths import get_state_dir

    return get_state_dir() / "tailnet"


def node_name() -> str:
    """The name this computer asks for on the tailnet: ``openprogram-<host>``.

    A named profile adds its name, so the development instance and the
    stable one never ask for the same name.
    """
    from openprogram.state.paths import get_active_profile

    try:
        host = socket.gethostname().split(".", 1)[0]
    except OSError:
        host = ""
    parts = ["openprogram", get_active_profile() or "", host]
    name = re.sub(r"[^a-z0-9-]+", "-", "-".join(part for part in parts if part).lower())
    return re.sub(r"-{2,}", "-", name).strip("-")[:63].strip("-") or "openprogram"


def anywhere_configured() -> bool:
    from openprogram.state.config import read_config

    web = read_config().get("web") or {}
    return isinstance(web, dict) and web.get("anywhere") is True


def set_anywhere_configured(on: bool) -> None:
    from openprogram.state.config import update_config

    def mutate(cfg: dict) -> None:
        web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
        web["anywhere"] = bool(on)
        cfg["web"] = web

    update_config(mutate)


def parse_line(line: str) -> dict[str, Any] | None:
    """One state line from the sidecar, or ``None`` for anything else."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("state") not in STATES or data.get("state") == "off":
        return None
    text = {key: (data[key] if isinstance(data.get(key), str) and data[key] else None)
            for key in ("url", "login_url", "enable_url", "message")}
    return {"state": data["state"], **text}


class Anywhere:
    """The sidecar as a child of this Worker, and the latest state it reported.

    ``allow`` changes the Origins the running server answers:
    ``allow(added=[...])`` when an address starts answering,
    ``allow(removed=[...])`` when it stops.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._allow: Callable[..., None] | None = None
        self._state: dict[str, Any] = {"state": "off"}
        self._address = ""

    def status(self) -> dict[str, Any]:
        """The ``anywhere`` object of ``GET /api/auth/remote-access``."""
        on = anywhere_configured()
        with self._lock:
            state = dict(self._state)
            alive = self._process is not None and self._process.poll() is None
        if not on and not alive:
            state = {"state": "off"}
        name = state.get("state", "off")
        return {
            "on": on,
            "state": name,
            "address": state.get("url") if name == "running" else None,
            "login_url": state.get("login_url") if name == "needs_login" else None,
            "enable_url": state.get("enable_url") if name == "needs_funnel" else None,
            "message": state.get("message"),
        }

    def address(self) -> str:
        """The public address while it answers, else ""."""
        with self._lock:
            return self._address

    def start(self, port: int, allow: Callable[..., None]) -> None:
        """Run the sidecar for the Worker on ``port``; a running one is kept."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            self._allow = allow
            binary = binary_path()
            if binary is None:
                self._state = {"state": "error", "message": f"{BINARY} is not part of this installation."}
                return
            folder = state_dir()
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            command = [str(binary), "--state-dir", str(folder), "--hostname", node_name(),
                       "--target", f"http://127.0.0.1:{int(port)}"]
            options: dict[str, Any] = {}
            if os.name == "nt":
                options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                           errors="replace", bufsize=1, **options)
            except OSError as exc:
                self._state = {"state": "error", "message": str(exc)}
                return
            self._process = process
            self._state = {"state": "starting"}
        threading.Thread(target=self._read, args=(process,), daemon=True, name=BINARY).start()

    def stop(self) -> None:
        """Stop the sidecar and stop answering at its address."""
        with self._lock:
            process, self._process = self._process, None
            self._state = {"state": "off"}
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=_STOP_SECONDS)
        self._set_address("")

    def _read(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is not None:
            for line in stream:
                self.apply(line, process)
        process.wait()
        with self._lock:
            current = self._process is process
            if current:
                self._process = None
                if self._state.get("state") != "error":
                    self._state = {"state": "error",
                                   "message": f"{BINARY} stopped (exit code {process.returncode})."}
        if current:
            self._set_address("")

    def apply(self, line: str, process: subprocess.Popen | None = None) -> None:
        """Take one state line from ``process``; a line from a replaced process is ignored."""
        state = parse_line(line)
        if state is None:
            return
        with self._lock:
            if process is not None and self._process is not process:
                return
            self._state = state
        self._set_address(state["url"] if state["state"] == "running" and state["url"] else "")

    def _set_address(self, address: str) -> None:
        with self._lock:
            previous, self._address = self._address, address
            allow = self._allow
        if previous == address or allow is None:
            return
        if previous:
            allow(removed=[previous])
        if address:
            allow(added=[address])


#: The one sidecar of this Worker.
anywhere = Anywhere()


def start_anywhere_if_configured(port: int, allow: Callable[..., None]) -> None:
    """Called as the Worker starts: bring back the sidecar the owner left on."""
    if anywhere_configured():
        anywhere.start(port, allow)


__all__ = ["Anywhere", "anywhere", "anywhere_configured", "binary_path", "go_target", "lan_address",
           "lan_origin", "node_name", "parse_line", "set_anywhere_configured", "start_anywhere_if_configured",
           "state_dir"]

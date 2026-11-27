"""An agent's config pointed at the gateway through a proxy in front of it.

After yetone/magpie ``internal/agent/reach.go`` (MIT License, Copyright (c)
yetone). An agent's address may reach the gateway another way than the one
OpenProgram wrote: a company's reverse proxy (``https://ai.corp.example/op``
forwarding to the gateway), a local proxy's route on loopback with a path of
its own, or billion-context's lane, ``http://127.0.0.1:8787/bili/<the
gateway's address>``. Such an address is kept when OpenProgram writes the
agent again, once the gateway has answered through it (its
``GET /gateway/openai/v1`` info, ``{"name": "openprogram"}``); a bili lane in
front of the gateway's own address on an older port goes on through the lane
to where the gateway is now.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from .kun import on_another_machine

#: How long a probe's answer is taken as it is.
AGE = 20.0
#: The gateway's own name in its info.
NAME = "openprogram"

_lock = threading.Lock()
_answers: dict[str, tuple[float, bool, bool]] = {}  # key -> (at, ok, busy)


def _probe(base: str, path: str, key: str) -> bool:
    """``base`` answers as the gateway: its info is 200 with the gateway's name.
    The address is the one the user's own agent config sends this key to: a
    proxy on this machine is asked as a local app's API is, one elsewhere
    under the outbound policy of a configured provider's address."""
    headers = {"Authorization": f"Bearer {key}"} if key else None
    if not on_another_machine(base):
        from . import local_api
        try:
            info = local_api.request("GET", base + path, headers=headers, timeout=3.0)
        except (local_api.NotRunning, local_api.ApiError):
            return False
        return isinstance(info, dict) and info.get("name") == NAME
    from openprogram.host.security.safe_http import safe_client
    from openprogram.host.security.url_policy import normalize_origin
    try:
        with safe_client("provider.configured_api", configured_origin=normalize_origin(base),
                         timeout=3.0, overall_timeout=5.0) as client:
            reply = client.get(base + path, headers=headers)
        info = reply.json() if reply.status_code == 200 else None
    except Exception:  # noqa: BLE001 - refused by policy, unreachable or not JSON: not the gateway
        return False
    return isinstance(info, dict) and info.get("name") == NAME


#: Asks ``base`` whether it is the gateway; tests stand in an answer.
probe: Callable[[str, str, str], bool] = _probe


def _parsed(v: str):
    try:
        u = urlparse(v)
        u.port  # noqa: B018 - a port out of range is no address
    except ValueError:
        return None
    return u


def loopback_with_path(base: str) -> bool:
    """``base`` is an http(s) address on this machine's loopback with a path: a
    local proxy's route, never the gateway's own address."""
    u = _parsed(base)
    if u is None or u.scheme not in ("http", "https") or not u.hostname or on_another_machine(base):
        return False
    return bool(u.path.strip("/"))


def proxy_base(v: str, want: str) -> tuple[str, bool]:
    """``v``, an address where OpenProgram wrote ``want``, with ``want``'s path
    cut off its end, when that can be another way to the gateway: an address
    on another machine or by name, or one on loopback with a path of its own
    before the gateway's. One on loopback with none is the gateway's own on
    an older port, never another way to it (magpie ``proxyBase``)."""
    w = _parsed(want)
    if w is None or w.scheme not in ("http", "https") or not w.netloc:
        return "", False
    v = (v or "").strip().rstrip("/")
    tail = w.path.rstrip("/")
    if not v.endswith(tail):
        return "", False
    base = v[: len(v) - len(tail)] if tail else v
    if not on_another_machine(base) and not loopback_with_path(base):
        return "", False
    u = _parsed(base)
    if u is None or u.username or u.password or u.query or u.fragment:
        return "", False
    return base, True


def chains_to_gateway(v: str, want: str) -> bool:
    """``v`` is a proxy on loopback naming ``want`` as its upstream at the end
    of its path (billion-context's ``http://127.0.0.1:18787/bili/<gateway>``):
    its requests reach the gateway whether or not the proxy answers as it."""
    v = (v or "").strip().rstrip("/")
    if not loopback_with_path(v):
        return False
    u = _parsed(v)
    if u is None or u.username or u.query or u.fragment:
        return False
    rest = u.path
    found = [i for i in (rest.find("/http://"), rest.find("/https://")) if i >= 0]
    if not found:
        return False
    upstream = rest[min(found) + 1:]
    up, w = _parsed(upstream), _parsed(want.rstrip("/"))
    if up is None or w is None or on_another_machine(want) or on_another_machine(upstream):
        return False
    return (up.scheme == w.scheme and up.port is not None and up.port == w.port
            and up.path.rstrip("/") == w.path.rstrip("/") and not up.username and not up.query)


_BILI = re.compile(r"^(https?://(?:127\.0\.0\.1|localhost|\[::1\]):\d{1,5}/bili/(?:[a-z][a-z0-9-]*/)?)(https?://\S+)$")


def bili_lane(v: str) -> tuple[str, str]:
    """``v`` behind billion-context's lane split into the lane and the address
    it forwards to; ``("", "")`` for any other ``v``."""
    m = _BILI.match((v or "").strip())
    return (m.group(1), m.group(2)) if m else ("", "")


def bili_to_gateway(v: str) -> str:
    """The lane in front of ``v`` when what it forwards to is on this machine
    (the gateway's own address before its port moved); ``""`` otherwise."""
    prefix, upstream = bili_lane(v)
    if not prefix or on_another_machine(upstream):
        return ""
    return prefix


def _path(want: str) -> str:
    u = _parsed(want)
    return u.path.rstrip("/") if u else ""


def gateway_at(base: str, want: str, key: str) -> tuple[bool, bool]:
    """Whether ``base`` answers as the gateway, and whether that is known yet:
    the last probe's answer, a new one started in the background when it is
    older than :data:`AGE`, never waited for."""
    name = base + _path(want)
    with _lock:
        at, ok, busy = _answers.get(name, (0.0, False, False))
        if not busy and time.monotonic() - at > AGE:
            _answers[name] = (at, ok, True)

            def run() -> None:
                answer = probe(base, _path(want), key)
                with _lock:
                    _answers[name] = (time.monotonic(), answer, False)
            threading.Thread(target=run, name="gateway-reach", daemon=True).start()
    return ok, at > 0


def gateway_asked(base: str, want: str, key: str) -> None:
    """:func:`gateway_at` waited for, for a write: ``base``, not asked yet, is
    asked now, so the write goes by the gateway's answer there."""
    name = base + _path(want)
    with _lock:
        at = _answers.get(name, (0.0, False, False))[0]
    if at > 0:
        return
    answer = probe(base, _path(want), key)
    with _lock:
        _answers[name] = (time.monotonic(), answer, False)


def surely_gateway(v: str, want: str, key: str) -> bool:
    """``v`` reaches the gateway another way, once the gateway has answered
    there: what a write that keeps the address goes by."""
    if chains_to_gateway(v, want):
        return True
    base, ok = proxy_base(v, want)
    if not ok:
        return False
    answered, known = gateway_at(base, want, key)
    return answered and known


def reach(v: str, want: str, key: str) -> str:
    """Where an agent's address ``v`` reaches the gateway, whose own is
    ``want`` (magpie ``zedReach``): ``v`` where it is the gateway's or a
    proxy the gateway has answered through; through billion-context's lane
    to the gateway where it is now, when the lane was in front of it before
    its port moved; else ``want``."""
    v = (v or "").strip()
    if v == want:
        return v
    base, ok = proxy_base(v, want)
    if ok:
        gateway_asked(base, want, key)
        if surely_gateway(v, want, key):
            return v
    elif chains_to_gateway(v, want):
        return v
    prefix = bili_to_gateway(v)
    if prefix:
        return prefix + want
    return want


def forget() -> None:
    """Drop every answer kept (tests)."""
    with _lock:
        _answers.clear()


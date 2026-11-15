"""What a Worker and a client tell each other about themselves.

A client built from another release than the Worker it reaches (a CLI
installed apart from the App, a remote server) must not guess what the other
side understands. The Worker says who it is when a socket connects (the
``worker_capabilities`` frame, under ``server``) and again in every
``chat_ack``; a ``chat`` command says who sent it (``client``).

``capabilities`` name what a client may rely on; a client keeps its
fallback for anything the Worker does not name:

* the control capabilities (``CONTROL_CAPABILITIES``): a stop never refused
  as stale, a stop that names a send;
* ``structured_output``: a structured reply is in the ``result`` frame's
  ``structured_output`` field, never only in its text;
* ``error_kind``: an ``error`` frame carries ``reason``, ``retryable`` and
  ``kind`` when the runtime classified the failure;
* ``turn_overrides``: a ``chat`` command's ``turn_model``, ``turn_effort``
  and ``turn_permission_mode`` apply to that turn only;
* ``wire_facts``: the ``result`` frame of a turn no one watches carries what
  its requests sent (``models/providers/recording/wire_facts.py``);
* ``no_web``: a ``chat`` command's ``turn_no_web`` runs that turn with no
  web tool and no provider-side search (``openprogram --print --no-web``).

``protocol`` is the highest history protocol the Worker serves (a client
asks for one with ``history_version`` on ``load_session``).
"""
from __future__ import annotations

from typing import Any

#: The name a Worker gives itself.
SERVER_NAME = "openprogram-worker"

#: The highest history protocol the Worker serves.
HISTORY_PROTOCOL = 3

#: What a ``chat`` turn's frames carry, beside the control capabilities.
CHAT_CAPABILITIES = ("structured_output", "error_kind", "turn_overrides", "wire_facts",
                     "no_web")

# The longest name or version a client may give; anything else is dropped.
_FIELD_LIMIT = 64


def package_version() -> str | None:
    """The installed ``openprogram`` version, ``None`` from a bare checkout."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openprogram")
    except PackageNotFoundError:
        return None


def server_identity() -> dict[str, Any]:
    """Who this Worker is: ``name``, ``version``, ``protocol`` and the
    ``capabilities`` a client may rely on, sorted."""
    from openprogram.runtime.execution.control.submission import CONTROL_CAPABILITIES

    return {
        "name": SERVER_NAME,
        "version": package_version(),
        "protocol": HISTORY_PROTOCOL,
        "capabilities": sorted({*CONTROL_CAPABILITIES, *CHAT_CAPABILITIES}),
    }


def client_identity(cmd: Any) -> dict[str, str] | None:
    """The ``client`` a command names (``name`` and ``version``, short
    strings), or ``None`` when it names none or a malformed one."""
    client = cmd.get("client") if isinstance(cmd, dict) else None
    if not isinstance(client, dict):
        return None
    identity = {key: client[key] for key in ("name", "version")
                if isinstance(client.get(key), str) and 0 < len(client[key]) <= _FIELD_LIMIT}
    return identity if "name" in identity else None


__all__ = ["CHAT_CAPABILITIES", "HISTORY_PROTOCOL", "SERVER_NAME", "client_identity",
           "package_version", "server_identity"]

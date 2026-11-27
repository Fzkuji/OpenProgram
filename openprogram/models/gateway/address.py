"""Where a harness reaches the gateway: this profile's Worker, on loopback."""
from __future__ import annotations

ANTHROPIC_PATH = "/gateway/anthropic"
OPENAI_PATH = "/gateway/openai/v1"
#: Each protocol's base under ``/gateway`` (and under ``/gateway/agent/<id>``).
PROTOCOL_PATHS = {
    "anthropic": "/anthropic",
    "openai-responses": "/openai/v1",
    "openai-chat": "/openai/v1",
    "gemini": "/gemini",
    "ollama": "/ollama",
}


def worker_base_url(*, stable: bool = False) -> str:
    """``http://127.0.0.1:<port>`` of this profile's Worker.

    The live Worker's port when one runs, else the port the profile settles
    on at start. ``stable`` asks for the latter only: a config written into a
    harness's own files outlives this Worker and must name the profile's
    port, not a port a single start moved to.
    """
    from openprogram.host.worker.process import read_worker_port, resolve_worker_port
    port = None if stable else read_worker_port()
    return f"http://127.0.0.1:{port or resolve_worker_port()}"


def anthropic_base_url(*, stable: bool = False) -> str:
    return worker_base_url(stable=stable) + ANTHROPIC_PATH


def openai_base_url(*, stable: bool = False) -> str:
    return worker_base_url(stable=stable) + OPENAI_PATH

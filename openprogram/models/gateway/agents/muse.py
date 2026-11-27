"""Muse Code, Meta's muse CLI (``${XDG_CONFIG_HOME:-~/.config}/muse/settings.json``).

After yetone/magpie ``internal/agent/muse.go`` and ``internal/gateway/muse.go``
(MIT License, Copyright (c) yetone). Muse refuses a settings file without
``"schema_version": 1``. Where its requests go is ``endpoint_transport``:
``base_url``, the ``/responses`` it asks (OpenAI's Responses API, streamed),
and ``auth``, ``"bearer"`` for the Meta sign-in's token (``auth.json``, or
``META_API_KEY``) or ``"none"`` for no Authorization at all and no sign-in
asked for. The model sessions start on is the top-level ``model``, one of
those Muse lists from ``<base_url's host>/muse-code/models``.

Connecting sets ``endpoint_transport`` to the gateway with ``auth: "none"`` —
so the Meta sign-in's token, which OpenProgram never reads, isn't sent to it
either — and ``model`` to the catalog id. ``endpoint_transport`` is OpenProgram's whole
while connected; the one the user had comes back on disconnect, and so
does their ``model`` while Muse is still on the gateway (Muse's own picker
rewrites it), and the file's other keys are left as they are. With no key
to send, Muse carries its own in its address (``path_keyed``:
``/gateway/agent/<key>/openai/v1``).
Its model list is asked at the host's root, where no address key reaches:
``/muse-code/models`` is served as Muse's own path and let in without a key
from this computer, from no web page, while Muse is connected
(``root_paths``; ``models_info.muse_models``).
"""
from __future__ import annotations

from pathlib import Path

from . import model_rows
from .base import Agent, Field, Pick, Wiring, Write

#: The host-root path Muse asks its model list at.
MODELS_PATH = "/muse-code/models"


def home() -> Path:
    return Path(model_rows.config_home()) / "muse"


def config_path() -> Path:
    return home() / "settings.json"


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    base = at.base("openai-responses")
    sets = {"endpoint_transport": {"base_url": base, "auth": "none"}}
    if not at.syncing:
        sets["model"] = at.model
    keep = {}
    doc = model_rows.read_doc("json", path)
    if "schema_version" not in doc:
        # Muse reads no file without its schema version: one OpenProgram makes
        # goes again with it once nothing else is left
        if path.exists():
            keep["schema_version"] = 1
        else:
            sets["schema_version"] = 1
    return [Write(path, "json", sets=sets, keep=keep, owns=("endpoint_transport",),
                  picks=(Pick(("model",), "endpoint_transport.base_url", base),) if "model" in sets else ())]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)muse( |$)"):
        return "Muse Code reads its settings at start-up — restart open muse sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="muse", name="Muse Code", icon="meta", aliases=("muse-code", "musecode"), kind="cli",
        protocol="openai-responses", bin="muse", dir=home, path=config_path,
        fields=[Field("model", "Model")],
        # muse-build/1.4.2 (non-interactive; macos-aarch64; …)
        ua=("muse-build", "muse-code"), path_keyed=True, root_paths=(MODELS_PATH,),
        wire=_wire, notice=_notice,
    )

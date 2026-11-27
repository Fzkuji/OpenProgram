"""PenguinHarness (``<PENGUIN_HOME>/<project>/.project_config.toml``).

After yetone/magpie ``internal/agent/penguin.go`` (MIT License, Copyright (c)
yetone). PenguinHarness (Prism-Shadow/penguin-harness, the ``penguin``
command; npm ``@prismshadow/penguin-cli``) keeps each Project's models in one
TOML file under its data root: ``$PENGUIN_HOME``, else ``~/.penguin/data`` on
every system, the CLI's and the desktop app's alike. The CLI works in the
Project ``$PENGUIN_PROJECT_ID``, else ``default_project``. Its shape (core
state/project-config.ts, penguin 0.2.17)::

    default_model = { provider = "openprogram", model_id = "<provider>/<model>" }
    [providers.openprogram]
    base_url = "http://127.0.0.1:<port>/gateway/openai/v1"
    client_type = "openai-responses"
    api_key = "<this agent's key>"
    [[models]]
    provider = "openprogram"
    model_id = "<provider>/<model>"
    display_name = "…"
    context_window = …
    vision = true
    max_tokens = …

A model is the pair (provider, model_id); one without a base_url, client
type or key of its own takes its group's, ``[providers.<provider>]``. Its
requests go through the client of that type: ``openai-responses`` posts
``base_url + /responses`` with its thinking level as ``reasoning.effort``,
which ``openai-chat`` leaves out. The server reads the file again whenever
its mtime moves, so a change needs no restart. OpenProgram's ``[[models]]``
entries sit after the user's (``Members``): disconnecting takes out those
naming ``openprogram`` and leaves the user's, edited since or not. A model's
list price goes in its ``[models.pricing]`` (``usd_per_mtok``: Penguin's
three buckets, ``cache_write`` being input not read from a cache), and a
``vision_model`` picked among OpenProgram's models in Penguin goes with them
on disconnect. A file OpenProgram starts is owner-only, as Penguin keeps its
own (it holds API keys): the editor makes every new file so.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, App, Field, Members, Pick, Wiring, Write, app_bundles
from .model_rows import PROVIDER_ID, images, max_tokens, name, price, read_doc, window, write_now

#: How Penguin speaks to the gateway: OpenAI Responses, the one of its
#: OpenAI clients that carries a thinking level.
CLIENT = "openai-responses"


def root() -> Path:
    env = os.environ.get("PENGUIN_HOME")
    return Path(env).expanduser() if env else Path.home() / ".penguin" / "data"


def project() -> str:
    """The Project the CLI works in: ``$PENGUIN_PROJECT_ID`` where it names a
    folder of the root, else ``default_project``."""
    value = (os.environ.get("PENGUIN_PROJECT_ID") or "").strip()
    if value and value not in (".", "..") and "/" not in value and "\\" not in value:
        return value
    return "default_project"


def config_path() -> Path:
    return root() / project() / ".project_config.toml"


def blocks(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The models as entries of Penguin's ``[[models]]``, each with what its
    own Models page records: the name, window, cap on a reply, image input."""
    out = []
    for m in models:
        entry: dict[str, Any] = {"provider": PROVIDER_ID, "model_id": m["id"]}
        if name(m) and name(m) != m["id"]:
            entry["display_name"] = name(m)
        if window(m):
            entry["context_window"] = window(m)
        # Penguin takes a model for one that reads images unless told not
        entry["vision"] = images(m)
        if max_tokens(m):
            entry["max_tokens"] = max_tokens(m)
        cost = price(m)
        if cost and (cost["input"] > 0 or cost["output"] > 0 or cost["cache_read"] > 0):
            # its three buckets: cache_write is input not read from a cache
            entry["pricing"] = {"unit": "usd_per_mtok", "cache_read": float(cost["cache_read"]),
                                "cache_write": float(cost["input"]), "output": float(cost["output"])}
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    table = f"providers.{PROVIDER_ID}"
    sets: dict[str, Any] = {
        f"{table}.base_url": at.base("openai-responses"),
        f"{table}.client_type": CLIENT,
        f"{table}.api_key": at.key,
    }
    # a default_model naming no entry is refused: the entries go in before it
    members = {"models": Members(match={"provider": PROVIDER_ID}, items=blocks(at.models))}
    writes = [Write(path, "toml", sets=sets, members=members, listing=True)]
    if not at.syncing:
        # spelled as Penguin's own writer spells it; its picker rewrites it,
        # and a sync of the model list never does
        writes.append(Write(path, "toml", sets={"default_model": {"provider": PROVIDER_ID, "model_id": at.model}},
                            picks=(Pick(("default_model",), "default_model.provider", PROVIDER_ID),)))
    return writes


def _unwire(record: dict[str, Any]) -> None:
    """A vision model picked among OpenProgram's in Penguin goes with them
    (magpie ``setRef("vision_model", "")``)."""
    path = config_path()
    vision = read_doc("toml", path).get("vision_model")
    if isinstance(vision, dict) and vision.get("provider") == PROVIDER_ID:
        write_now([Write(path, "toml", deletes=["vision_model"])])


#: The desktop app (magpie desktopapp.go): installed and not opened yet, it
#: has made no ~/.penguin. It reads the file again on its own, so no restart
#: is offered for it: it is a sign Penguin is here, nothing more.
APP = App("com.prismshadow.penguinharness", ("PenguinHarness.app",))


def _held() -> list[Path]:
    return app_bundles(APP)


def agent() -> Agent:
    return Agent(
        id="penguin", name="PenguinHarness", icon="penguin", aliases=("penguin-harness", "penguinharness"),
        kind="cli", protocol="openai-responses", bin="penguin", dir=lambda: root().parent, path=config_path,
        fields=[Field("model", "Model")], wire=_wire, unwire=_unwire, held_at=_held,
        # the desktop app makes no ~/.penguin until it is first opened
        sign=lambda: bool(app_bundles(APP)),
        # what disconnect leaves is put back byte for byte as Penguin wrote it
        before_copy=True,
    )

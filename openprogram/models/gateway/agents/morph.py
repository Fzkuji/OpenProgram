"""Mister Morph (``~/.morph/config.yaml``, or the file ``$MISTER_MORPH_CONFIG`` names).

After yetone/magpie ``internal/agent/morph.go`` (MIT License, Copyright (c)
yetone). Morph's main model is ``llm.model``, asked of the vendor
``llm.inference_provider`` names at ``llm.endpoint`` with ``llm.api_key``. It
has no list of providers to add one to, so connecting takes ``llm`` over:
``inference_provider: openai_response_compatible`` at the gateway's ``/v1``
(morph adds no second ``/v1`` to a base ending in one), spoken to on OpenAI's
Responses API, morph's own default; the gateway key, and the catalog id as the
model. ``llm.provider`` is left as it is: morph derives it from
``inference_provider``. Morph knows a model's window only from a list of its
own, by the id after its last slash, so the window the catalog knows goes in
``llm.context_window_tokens``. What the user had under those keys is put back
on disconnect. ``llm.profiles`` and ``llm.routes`` are the user's: a route
that sends the main loop to another profile is left so.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Field, Pick, Wiring, Write
from .model_rows import fixed, window, wrote_before

#: What morph takes as llm.reasoning_effort (magpie morph.go's effort field).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: The User-Agent morph is made to send, in ``llm.headers``: its requests
#: otherwise carry the OpenAI SDK's, which tells them from no one's.
MORPH_UA = "mistermorph"


def home() -> Path:
    return Path.home() / ".morph"


def config_path() -> Path:
    value = os.environ.get("MISTER_MORPH_CONFIG") or ""
    return Path(value).expanduser() if value else home() / "config.yaml"


def _wire(at: Wiring) -> list[Write]:
    sets: dict[str, Any] = {
        "llm.inference_provider": "openai_response_compatible",
        "llm.endpoint": at.base("openai-responses"),
        "llm.api_key": at.key,
        "llm.headers.User-Agent": MORPH_UA,
    }
    deletes: list[str] = []
    if at.syncing:
        # the model and effort are the user's to change in morph
        return [Write(config_path(), "yaml", sets=sets)]
    sets["llm.model"] = at.model
    known = next((window(model) for model in at.models if model.get("id") == at.model), 0)
    if known:
        sets["llm.context_window_tokens"] = known
    else:
        deletes.append("llm.context_window_tokens")
    # llm.reasoning_effort; unset, morph sends none
    effort = at.values.get("effort", "")
    if effort:
        sets["llm.reasoning_effort"] = effort
    elif "llm.reasoning_effort" in wrote_before("morph", config_path()):
        deletes.append("llm.reasoning_effort")
    pick = Pick(tuple(key for key in ("llm.model", "llm.reasoning_effort") if key in sets), "llm.endpoint",
                sets["llm.endpoint"])
    return [Write(config_path(), "yaml", sets=sets, deletes=deletes, picks=(pick,))]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)morph( |$)", r"(^|/)mistermorph( |$)"):
        return "Mister Morph's Console uses this for new tasks; restart open morph chats to use it there."
    return ""


def agent() -> Agent:
    # no bin: morph is other tools' name too; ~/.morph is made by its first
    # run, `morph install` or the desktop app's setup
    return Agent(
        id="morph", name="Mister Morph", icon="mistermorph", aliases=("mistermorph", "mister-morph"), kind="cli",
        protocol="openai-responses", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("effort", "Effort", quiet=True, options=fixed(*EFFORTS))],
        ua=(MORPH_UA,), wire=_wire, notice=_notice,
    )

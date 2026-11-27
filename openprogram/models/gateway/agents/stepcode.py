"""StepCode (StepFun's ``step`` command): its configuration in
``~/.stepcode/config.toml`` (or the parent of an absolute
``$STEP_CODING_AGENT_DIR``, or ``~/$STEPCODE_CONFIG_DIR``) and its models in
``models.json`` beside it, in Pi's provider format.

After yetone/magpie ``internal/agent/stepcode.go`` (MIT License, Copyright
(c) yetone). Connecting sets ``defaultProvider`` and ``defaultModel`` in
config.toml, adds the model to ``enabledModels`` when there is such a list
and nothing in it takes the model in (Pi's rule, ``pi_like.scope_with``),
and writes one ``providers.openprogram`` entry in models.json: Pi's entries,
each model on its native API, Gemini's left out (magpie ``piAsked``), and a
key that names StepCode. Disconnecting takes the provider out, puts the
pick back while it is still OpenProgram's, and takes OpenProgram's models
out of ``enabledModels``. After
a sync, ``enabledModels`` loses an entry on a model no longer given and
gets it back once it is (``pi_like.scope_synced``).
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Field, Pick, Wiring, Write, agent_dir
from .pi_like import PROVIDER_ID, provider_block, read_doc, scope_writes, theirs_kept, unwired


def folder() -> Path:
    value = os.environ.get("STEP_CODING_AGENT_DIR", "").strip()
    if value and os.path.isabs(value):
        return Path(os.path.normpath(value)).parent
    return Path.home() / (os.environ.get("STEPCODE_CONFIG_DIR", "").strip() or ".stepcode")


def config_path() -> Path:
    return folder() / "config.toml"


def _wire(at: Wiring) -> list[Write]:
    models = folder() / "models.json"
    block = provider_block(at, drop_gemini=True)
    del block["compat"]  # StepCode's block is Pi's without the session switch (magpie stepcodeProvider)
    block = theirs_kept(read_doc(models), f"providers.{PROVIDER_ID}", block)
    given = [m.get("id", "") for m in block.get("models") or [] if isinstance(m, dict)]
    synced, gone, picked = scope_writes(read_doc(config_path(), "toml"), given, f"{PROVIDER_ID}/{at.model}", at)
    picked.update({"defaultProvider": PROVIDER_ID, "defaultModel": at.model})
    key = f"providers.{PROVIDER_ID}"
    return [Write(models, "json", sets={key: block}, owns=(key,), listing=True),
            Write(config_path(), "toml", sets=synced, deletes=gone, listing=True),
            # StepCode's own picker sets the two as one
            Write(config_path(), "toml", sets=picked,
                  picks=(Pick(("defaultProvider", "defaultModel"), "defaultProvider", PROVIDER_ID),))]


def agent() -> Agent:
    return Agent(
        id="stepcode", name="StepCode", icon="stepfun", aliases=("step",), kind="cli",
        protocol="openai-chat", bin="step", dir=folder, path=config_path,
        fields=[Field("model", "Model")], ua=("stepcode",), named_key=True, wire=_wire,
        # enabledModels is the user's list as much as OpenProgram's
        loose=("enabledModels",),
        unwired=lambda record: unwired(folder() / "models.json", config_path()),
        # its folder alone: `step` is a common name
        detect=lambda: agent_dir(folder()), detect_only=True,
    )

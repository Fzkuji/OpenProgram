"""Launch overrides for a harness OpenProgram starts for a chat.

The harness's own config files are not touched: Codex gets ``-c key=value``
overrides on its command line, Claude Code gets environment variables. The
gateway key travels in the environment (Codex reads it through ``env_key``),
so it never shows in the process list. Model refs are
``openprogram:<provider>/<model>``.
"""
from __future__ import annotations

from .config_files import toml_value
from .harness_config import PROVIDER_ID, PROVIDER_TABLE, CLOUD_SWITCHES, claude_code_env, strip_ref

#: The variable a launched Codex reads the gateway key from.
KEY_ENV = "OPENPROGRAM_GATEWAY_KEY"


def _context_window(model_ref: str) -> int | None:
    from .conversion import GatewayError, resolve_model
    try:
        return resolve_model(model_ref).context_window or None
    except GatewayError:
        return None


def codex_launch(model_ref: str) -> tuple[list[str], dict[str, str]]:
    """Extra ``codex app-server`` arguments and environment for ``model_ref``."""
    from .address import openai_base_url
    from .keys import gateway_key
    model = strip_ref(model_ref)
    if "/" not in model:
        raise ValueError(f"not an OpenProgram model ref: {model_ref!r}")
    overrides: list[tuple[str, object]] = [
        ("model_provider", PROVIDER_ID),
        (f"{PROVIDER_TABLE}.name", "OpenProgram"),
        (f"{PROVIDER_TABLE}.base_url", openai_base_url()),
        (f"{PROVIDER_TABLE}.wire_api", "responses"),
        (f"{PROVIDER_TABLE}.env_key", KEY_ENV),
        ("model", model),
    ]
    context_window = _context_window(model)
    if context_window:
        # Codex has no metadata for a model it does not ship; without the
        # window it compacts at its fallback size.
        overrides.append(("model_context_window", int(context_window)))
    args: list[str] = []
    for key, value in overrides:
        args += ["-c", f"{key}={toml_value(value)}"]
    return args, {KEY_ENV: gateway_key()}


def claude_code_launch(model_ref: str, tiers: dict | None = None) -> dict[str, str]:
    """Environment for a Claude Code process whose model calls go to ``model_ref``.

    ``tiers`` maps ``opus`` / ``sonnet`` / ``haiku`` to model refs; a missing
    tier uses ``model_ref``. Cloud switches are set off for this process.
    """
    from .address import anthropic_base_url
    from .keys import gateway_key
    if "/" not in strip_ref(model_ref):
        raise ValueError(f"not an OpenProgram model ref: {model_ref!r}")
    env = claude_code_env(model_ref, tiers, base_url=anthropic_base_url(), token=gateway_key())
    env.update({name: "0" for name in CLOUD_SWITCHES})
    return env

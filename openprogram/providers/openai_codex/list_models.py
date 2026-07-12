"""OpenAI Codex model list — the account's own models endpoint.

Convention module: the model-listing dispatcher
(``webui/_model_listing/fetchers``) loads ``fetch(provider_id, timeout)`` from
each provider's ``list_models.py`` by directory name, the same way it loads
``probe_thinking.probe()``. A provider that ships this file describes its own
model source; one that doesn't falls back to the generic OpenAI-compatible
``/v1/models`` fetcher.

The Codex/ChatGPT-subscription backend has a private account-level list-models
endpoint — the same one the official ``codex`` CLI hits on startup:

    GET https://chatgpt.com/backend-api/codex/models?client_version=<ver>

authorized with the subscription OAuth bearer + ``chatgpt-account-id``. It
returns exactly the models this account may dispatch, each with its real
subscription-side ``context_window``, ``service_tiers`` (the fast/priority
knob), and ``supported_reasoning_levels`` (the thinking picker). We read all of
that here and hand it back in one normalised shape so enable-time storage and
the runtime never have to guess.

Why not models.dev: it tracks the *public API platform* catalogue, not the
subscription front-door — leaking un-runnable ids (``gpt-5.6-luna`` slipped
through the "no 'nano'" heuristic then 404'd), wrong context windows (1050k vs
the subscription's 372k), and a fast flag guessed from an id-prefix table that
misfired on tiers like ``gpt-5.4-mini``. The official endpoint is authoritative.

Contract: success → ``list[dict]`` (each row has at least id/name), failure →
``{"error": ...}``. Offline / no token → error (the dispatcher keeps the saved
list), never blank — you can't dispatch a Codex model without a token anyway.
"""
from __future__ import annotations

from typing import Any

# The framework's Codex thinking picker only knows these effort levels
# (openai_codex/provider.json). The live endpoint occasionally lists a higher
# ``ultra`` tier on the frontier models; the wire doesn't accept it here, so we
# drop anything outside this set instead of writing a level the UI can't render.
_CODEX_THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _codex_list_url(client_version: str) -> str:
    return (
        "https://chatgpt.com/backend-api/codex/models"
        f"?client_version={client_version}"
    )


def _normalise_codex_model(m: dict[str, Any]) -> dict[str, Any] | None:
    """One live endpoint row → our config-row shape. ``None`` for rows we skip
    (hidden helper models like ``codex-auto-review``)."""
    mid = m.get("slug")
    if not mid or m.get("visibility") == "hide":
        return None

    row: dict[str, Any] = {
        "id": mid,
        "name": m.get("display_name") or mid,
        "reasoning": bool(m.get("supported_reasoning_levels")),
    }
    ctx = m.get("context_window") or m.get("max_context_window")
    if ctx:
        row["context_window"] = int(ctx)
    if "image" in (m.get("input_modalities") or []):
        row["vision"] = True

    # Fast/priority tier: the endpoint spells it out per model, so no more
    # guessing from an id family table. ``service_tiers`` carries a
    # ``{"id": "priority", ...}`` entry exactly when this model has a fast tier.
    row["fast"] = any(
        t.get("id") == "priority" for t in (m.get("service_tiers") or [])
    )

    # Thinking picker: the effort ids the endpoint advertises, filtered to what
    # the framework's wire can send (drops ``ultra``).
    levels = [
        lv.get("effort")
        for lv in (m.get("supported_reasoning_levels") or [])
        if lv.get("effort") in _CODEX_THINKING_LEVELS
    ]
    if levels:
        row["thinking_levels"] = levels
        default = m.get("default_reasoning_level")
        row["default_thinking_level"] = default if default in levels else levels[0]
    return row


def fetch(provider_id: str, timeout: float) -> Any:
    """Live Codex fetch via the account's models endpoint.

    Returns ``{"error": ...}`` when unreachable / unauthorized so the
    dispatcher leaves the saved model list untouched."""
    import httpx

    from .oauth import _get_account_id_from_jwt
    from .openai_codex import _resolve_codex_bearer_token
    from .runtime import _CODEX_CLIENT_VERSION

    token = _resolve_codex_bearer_token(None)
    if not token:
        return {"error": (
            "not signed in to ChatGPT/Codex — run `codex login` or the "
            "OpenProgram OAuth wizard, then Fetch again."
        )}
    account_id = _get_account_id_from_jwt(token) or ""

    try:
        resp = httpx.get(
            _codex_list_url(_CODEX_CLIENT_VERSION),
            headers={
                "Authorization": f"Bearer {token}",
                "chatgpt-account-id": account_id,
                "originator": "codex_cli_rs",
                "version": _CODEX_CLIENT_VERSION,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return {"error": (
            f"could not reach the Codex models endpoint ({exc}) — your existing "
            "Codex model list was kept. Try Fetch again when online."
        )}

    out: list[dict[str, Any]] = []
    for m in payload.get("models") or []:
        if not isinstance(m, dict):
            continue
        row = _normalise_codex_model(m)
        if row:
            out.append(row)

    if not out:
        return {"error": "Codex models endpoint returned no usable models"}
    return out

"""The model the user ran last — what a new conversation starts on.

There is no default model. A new conversation starts on the model the
user last picked or sent a message with. On a fresh install nothing has
been used, so there is no model until the user picks one.

Stored in ``config.json`` as ``last_used_model: {"provider", "model"}``.
Background work that has no conversation of its own (titles, memory,
MCP sampling, the Auto permission review) runs on the same model.
"""
from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)

_KEY = "last_used_model"


def _bare(provider: str, model: str) -> str:
    return model[len(provider) + 1:] if model.startswith(f"{provider}:") else model


def last_used_in_config(cfg: dict) -> Optional[tuple[str, str]]:
    """The recorded pair in an already-read config, without adopting."""
    value = cfg.get(_KEY)
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or "").strip()
    model = str(value.get("model") or "").strip()
    return (provider, model) if provider and model else None


def read_last_used_model() -> Optional[tuple[str, str]]:
    """Return ``(provider, model)`` the user ran last, or ``None``."""
    from openprogram.state import config as user_config

    cfg = user_config.read_config()
    pair = last_used_in_config(cfg)
    if pair is not None:
        return pair
    if _KEY in cfg:
        return None
    return _adopt_existing_install()


def record_last_used_model(provider: Optional[str], model: Optional[str]) -> None:
    """Remember the model the user just picked or sent with."""
    provider = (provider or "").strip()
    model = _bare(provider, (model or "").strip()) if provider else ""
    if not provider or not model:
        return
    from openprogram.state import config as user_config
    from openprogram.models.providers.settings.storage import _cache_lock

    with _cache_lock:
        def update(cfg: dict) -> None:
            cfg[_KEY] = {"provider": provider, "model": model}
            cfg.pop("default_provider", None)
            cfg.pop("default_model", None)

        if last_used_in_config(user_config.read_config()) != (provider, model):
            user_config.update_config(update)


def forget_last_used_model() -> None:
    """Forget the record; new conversations start with no model again."""
    from openprogram.state import config as user_config
    from openprogram.models.providers.settings.storage import _cache_lock

    with _cache_lock:
        def update(cfg: dict) -> None:
            cfg[_KEY] = {}

        if last_used_in_config(user_config.read_config()) is not None:
            user_config.update_config(update)


def _adopt_existing_install() -> Optional[tuple[str, str]]:
    """Record, once, the model of the most recently active conversation.

    An install from before this record existed still knows what the user
    ran last: the newest conversation's pinned model. A fresh install has
    no conversations and records nothing, so it starts with no model.
    """
    try:
        pair = _newest_session_model()
    except Exception as exc:  # noqa: BLE001
        # Unknown is not "nothing used": try again on the next read.
        _log.warning("cannot read the newest conversation's model: %s", exc)
        return None
    from openprogram.state import config as user_config
    from openprogram.models.providers.settings.storage import _cache_lock

    adopted = False
    with _cache_lock:
        def update(cfg: dict) -> None:
            nonlocal adopted
            if _KEY in cfg:
                return
            adopted = True
            cfg[_KEY] = {"provider": pair[0], "model": pair[1]} if pair else {}
            cfg.pop("default_provider", None)
            cfg.pop("default_model", None)

        user_config.update_config(update)
    if adopted:
        _release_default_agent_model()
    return last_used_in_config(user_config.read_config())


def _release_default_agent_model() -> None:
    """The default Agent's model was the old default model; drop it.

    Agents without a model of their own run on the last used model, so
    the default Agent stops pinning every new conversation to one model.
    Other Agents keep a model set for them on purpose.
    """
    try:
        from openprogram.state.execution_facts import agent_registry

        manager = agent_registry()
        spec = manager.get_default()
        if spec is not None and (spec.model.provider or spec.model.id):
            manager.update(spec.id, {"model": {"provider": "", "id": ""}})
    except Exception as exc:  # noqa: BLE001
        _log.warning("cannot clear the default Agent's model: %s", exc)


def _newest_session_model() -> Optional[tuple[str, str]]:
    from openprogram.state.store.current import current_store
    from openprogram.state.execution_facts import session_chat_model

    # Listing rows are summaries without the model pin; read each pin.
    for row in current_store().list_sessions(limit=200, include_archived=True):
        provider, model = session_chat_model(row.get("id"))
        if provider and model:
            return provider, _bare(provider, model)
    return None

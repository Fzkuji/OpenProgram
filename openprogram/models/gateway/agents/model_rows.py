"""The enabled models as an agent's own model list spells them.

After yetone/magpie ``internal/agent/route.go`` (``magpieModels``,
``maxTokens``, ``unknownWindow``) and ``internal/provider/models.go``
(``OutputWithin``); MIT License, Copyright (c) yetone. Agents that keep a
list of their providers' models (Crush, Goose, Kimi Code, Hermes …) are
given the gateway's models with what the catalog knows of each: the window,
the output limit kept within it, image input and reasoning levels. A model
is listed as the catalog names it, ``provider/model``; inside an agent whose
models are named by provider it is ``openprogram/<provider>/<model>``.
"""
from __future__ import annotations

from typing import Any, Callable

#: The provider id OpenProgram's entry has in an agent's own config (magpie ``magpieID``).
PROVIDER_ID = "openprogram"
#: How the entry is named where an agent shows it.
PROVIDER_NAME = "OpenProgram"


def prefixed(ref: str) -> str:
    """``openprogram/<provider>/<model>``: a catalog ref as an agent that names
    models by provider spells it (magpie ``prefixed``)."""
    return f"{PROVIDER_ID}/{ref}" if ref and not ref.startswith(PROVIDER_ID + "/") else ref


def unprefixed(value: str) -> str:
    """The catalog ref of a value an agent holds, with or without ``openprogram/``."""
    return value[len(PROVIDER_ID) + 1:] if value.startswith(PROVIDER_ID + "/") else value


def window(model: dict[str, Any], default: int = 0) -> int:
    """The model's context window, ``default`` when the catalog doesn't know it."""
    value = model.get("context_window")
    return int(value) if isinstance(value, (int, float)) and value > 0 else default


def max_tokens(model: dict[str, Any], default: int = 0) -> int:
    """The output limit, kept within the window (magpie ``maxTokens``)."""
    value = model.get("max_tokens")
    output = int(value) if isinstance(value, (int, float)) and value > 0 else 0
    context = window(model)
    if context and output > context:
        output = context
    return output or default


def efforts(model: dict[str, Any]) -> list[str]:
    """The reasoning levels the model takes; none for one that doesn't reason."""
    levels = [str(level) for level in (model.get("thinking_levels") or []) if level and level != "off"]
    if not levels and model.get("reasoning"):
        levels = ["low", "medium", "high"]
    return levels


def images(model: dict[str, Any]) -> bool:
    return bool(model.get("vision"))


def name(model: dict[str, Any]) -> str:
    return str(model.get("name") or model.get("id") or "")


def dotted(*parts: str) -> str:
    """A dotted key path of ``parts``, a dot inside one escaped (``models.openprogram/a/gpt-5\\.1``)."""
    return ".".join(part.replace(".", "\\.") for part in parts)


def wrote_before(agent_id: str, path: Any) -> dict[str, Any]:
    """What OpenProgram last wrote in the agent's file at ``path``, by key
    (magpie keeps it as ``<agent>:<path>:wrote``), so a value the user changed
    since inside OpenProgram's own entries is told from OpenProgram's."""
    from .. import stash
    try:
        record = stash.entry(agent_id) or {}
    except stash.StashUnreadable:
        return {}
    import os
    wrote = ((record.get("files") or {}).get(os.path.realpath(path)) or {}).get("wrote") or {}
    return {key: value.get("set") for key, value in wrote.items() if isinstance(value, dict) and "set" in value}


def absent_before(agent_id: str, path: Any, keys: list[str], fmt: str = "toml") -> list[str]:
    """Of ``keys``, OpenProgram's own entries leaving the agent's config (a
    model gone from the catalog), those it can take out now: where the agent
    is connected, each is recorded as not there before OpenProgram, so a
    disconnect does not put it back; where it isn't, none (the stash would
    take the entry for the user's)."""
    import os
    from .. import stash
    try:
        if not keys or stash.entry(agent_id) is None:
            return []
        stash.remember(agent_id, os.path.realpath(path), {key: (False, None) for key in keys}, fmt=fmt)
    except stash.StashUnreadable:
        return []
    return list(keys)


def read_doc(fmt: str, path: Any) -> dict[str, Any]:
    """The agent's config at ``path`` as read; ``{}`` when it isn't there or
    doesn't parse (the engine then refuses the edit itself)."""
    from .. import edit
    try:
        text = edit.ConfigText.read(path)
        return edit.load(fmt, text.text, str(path)) if text.exists else {}
    except (OSError, edit.ConfigUnreadable):
        return {}


def native_api(model: dict[str, Any]) -> str:
    """The API the model's provider serves it on (``openai-responses``,
    ``anthropic-messages``, ``openai-completions`` …), ``""`` when not known,
    so an agent that asks each model on its own API (magpie ``m.APIs``) has
    the gateway relay it as it is."""
    if model.get("api"):
        return str(model["api"])
    try:
        from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
        found = ENABLED_MODELS.get(str(model.get("id") or ""))
    except Exception:  # noqa: BLE001 - a registry that cannot load says nothing
        return ""
    return str(getattr(found, "api", "") or "")


def env_dir(variable: str) -> str:
    """A folder an environment variable names, ``""`` unless it is absolute:
    an unexpanded ``~/.config`` must not send a write into ``./~/``
    (magpie ``appdir.Getenv``)."""
    import os
    value = (os.environ.get(variable) or "").strip()
    return value if value and os.path.isabs(value) else ""


def config_home() -> str:
    """``$XDG_CONFIG_HOME``, else ``~/.config``, on every OS (magpie ``homes``)."""
    import os
    return env_dir("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


def price(model: dict[str, Any]) -> dict[str, float] | None:
    """USD per million tokens (``input``, ``output``, ``cache_read``,
    ``cache_write``), ``None`` when not known (magpie ``m.Price``)."""
    value = model.get("price")
    return dict(value) if isinstance(value, dict) else None


def picked(values: dict[str, str], key: str = "model") -> dict[str, Any]:
    """The enabled model a field's value names, ``{}`` when none is."""
    from .. import conversion
    ref = unprefixed(values.get(key) or "")
    return next((row for row in conversion.enabled_model_refs() if row["id"] == ref), {})


def picked_efforts(values: dict[str, str], key: str = "model") -> list[str]:
    """An effort field's choices: the reasoning levels of the model picked in
    ``key`` (magpie's effort ``Options``)."""
    return efforts(picked(values, key))


def write_now(writes: list[Any]) -> None:
    """Write ``writes`` (``base.Write``) as they are, nothing stashed: what an
    agent's ``unwire`` takes out before the stashed values go back. A file
    that isn't there is left so; one that can't be read raises
    ``ConfigUnreadable``."""
    from .. import edit
    for write in writes:
        text = edit.ConfigText.read(write.path)
        if write.format == "file":
            if write.content is None:
                if text.exists:
                    write.path.unlink()
            elif write.content != text.text:
                text.write(write.content)
            continue
        if not text.exists:
            continue
        doc = edit.load(write.format, text.text, str(write.path))
        deletes = [key for key in write.deletes if edit.get_path(doc, key)[0]]
        sets = {**write.keep, **write.sets}
        if not sets and not deletes:
            continue
        new = edit.edit(write.format, text.text, sets=sets, deletes=deletes, where=str(write.path))
        if new != text.text:
            text.write(new)


def choice(agent_id: str, words: Callable[[dict[str, str]], list[str]]) -> Callable[[], list[tuple[str, str]]]:
    """A choice field's ``options`` (``Field.options``) from the words the
    agent takes for its picks now (a reasoning effort of the model picked),
    each shown as it is written."""
    def options() -> list[tuple[str, str]]:
        from .base import picked as connected
        return [(str(word), str(word)) for word in words(connected(agent_id))]
    return options


def fixed(*words: str) -> Callable[[], list[tuple[str, str]]]:
    """A choice field's ``options`` of the same words whatever is picked."""
    return lambda: [(word, word) for word in words]

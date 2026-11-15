"""Which native form a request uses to load a searched tool.

A provider declares, per wire API, the native tool-search form its
endpoint takes, under the ``tool_search`` key of its ``provider.json``::

    "tool_search": {"openai-codex": "responses"}

A model may opt out in its own table entry (``Model.tool_search = False``),
or the provider may list model-id fragments that never take its form under
the block's ``models_off`` key (Anthropic lists ``haiku``, as Claude Code
excludes Haiku from ``tool_reference``)::

    "tool_search": {"anthropic-messages": "anthropic-reference",
                    "models_off": ["haiku"]}

The form a request uses is the intersection: the provider declares one for
the request's API and the model does not opt out. Everything else gets
``None`` and keeps loading a searched tool into the tools array.

Forms:
  "responses"           — the Responses client-executed ``tool_search``:
                          the search is a ``tool_search_call`` item and its
                          result a ``tool_search_output`` carrying the
                          loaded definitions.
  "anthropic-reference" — the Messages API's ``tool_reference``: a loaded
                          tool is appended to the tools array with
                          ``defer_loading: true`` and the search's result is
                          only ``tool_reference`` blocks naming it.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from openprogram.models.providers.settings.thinking_spec import _load_folded

#: The forms an adapter knows how to render.
FORMS = frozenset({"responses", "anthropic-reference"})


@lru_cache(maxsize=None)
def _declared(provider_id: str) -> dict[str, str]:
    if not provider_id:
        return {}
    for dir_name in (provider_id, provider_id.replace("-", "_")):
        block = _load_folded(dir_name, "tool_search", "tool_search.json")
        if isinstance(block, dict):
            return {str(api): str(form) for api, form in block.items()
                    if isinstance(form, str) and form in FORMS}
    return {}


@lru_cache(maxsize=None)
def _models_off(provider_id: str) -> tuple[str, ...]:
    if not provider_id:
        return ()
    for dir_name in (provider_id, provider_id.replace("-", "_")):
        block = _load_folded(dir_name, "tool_search", "tool_search.json")
        if isinstance(block, dict):
            return tuple(str(part).lower() for part in block.get("models_off") or () if part)
    return ()


def tool_search_form(provider: Any, api: Any, model: Any = None) -> str | None:
    """The native tool-search form for ``(provider, api, model)``, or None.

    ``model`` is a ``Model`` (or anything with a ``tool_search`` attribute);
    ``tool_search = False`` there turns the form off for that model.
    """
    if not provider or not api:
        return None
    form = _declared(str(provider)).get(str(api))
    if form is None or getattr(model, "tool_search", None) is False:
        return None
    model_id = str(getattr(model, "id", "") or "").lower()
    if model_id and any(part in model_id for part in _models_off(str(provider))):
        return None
    return form


def model_tool_search_form(model: Any) -> str | None:
    """``tool_search_form`` for a ``Model`` (None for no model)."""
    if model is None:
        return None
    return tool_search_form(getattr(model, "provider", None), getattr(model, "api", None), model)

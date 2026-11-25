"""Each type's guide, each target's note and the code guide, delivered with the first call that operates such a target (``openprogram.programs._authoring.guides``)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openprogram.programs._authoring import guides

from .code_mode import calls as code_mode
from .routing import registry
from .actions import vocabulary
from .contract import Provider
from .context import NAME
from . import context


_GUIDES_DIR = Path(__file__).parent / "guides"


def _family_text(family: str) -> str:
    path = _GUIDES_DIR / f"{family}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _provider_of(call: Any) -> Provider | None:
    if not isinstance(call, dict):
        return None
    try:
        return registry.find(call, context._session_id())
    except Exception:
        return None


def _in_family(family: str):
    def applies(args: dict) -> bool:
        provider = _provider_of(args)
        return provider is not None and provider.family == family
    return applies


def _is_target(target: str):
    def applies(args: dict) -> bool:
        provider = _provider_of(args)
        return provider is not None and provider.target == target and bool(provider.note())
    return applies


def _note_text(target: str):
    def text() -> str:
        provider = registry.providers(context._session_id()).get(target)
        return provider.note() if provider is not None else ""
    return text


def _operates_with_code(args: dict) -> bool:
    return _provider_of(args) is not None and context._code_available()


_registered: tuple[str, ...] | None = None


def _register_guides(providers: list[Provider]) -> None:
    """Each family's guide arrives with the first call that operates a target of that family, each target's
    note with the first call that operates that target, and the code guide with the first call that operates
    any. A call that sends code before either has been seen runs and gets them with its result: no call is
    turned back to make the model read first."""
    global _registered
    names = tuple(sorted(provider.target for provider in providers))
    if names == _registered:
        return
    items = [guides.Guide(f"{NAME}.{family}", (lambda family=family: _family_text(family)), mode="included",
                          when=_in_family(family))
             for family in vocabulary.FAMILIES]
    items += [guides.Guide(f"{NAME}.note.{target}", _note_text(target), mode="included", when=_is_target(target))
              for target in names]
    items.append(guides.Guide(f"{NAME}.code", code_mode.guide, mode="included", when=_operates_with_code))
    guides.register(NAME, items)
    _registered = names


def _target_guides(provider: Provider) -> list[guides.Guide]:
    names = {f"{NAME}.{provider.family}"}
    if provider.note():
        names.add(f"{NAME}.note.{provider.target}")
    if context._code_available():
        names.add(f"{NAME}.code")
    return [item for item in guides.guides_for(NAME) if item.name in names]


def guide(call: dict) -> str:
    """The full guide of the target a call names (``{"type": "window"}``): its type's actions, its own note,
    then code."""
    provider = registry.resolve(call, context._session_id())
    _register_guides(list(registry.providers(context._session_id()).values()))
    return "\n\n".join(guides.render(item) for item in _target_guides(provider))


_register_guides([])

"""The registered models nearest to an id nobody knows.

An unknown model id ("gpt6 sol", "claude-sonet-5", a provider prefix
typed wrong) was refused with nothing but the id back. Now the refusal
names the closest registered models, nearest first.

Names are compared by their words: runs of letters and runs of digits,
lowercased ("gpt-6.1-sol" is ``gpt 6 1 sol``). A model scores for each
asked word found in its display name (4) or its id (3); a number found
there, or the provider's id, counts 1 and only beside a word of the name
or id, so a shared version alone is no match. A model whose name or id
holds all of the asked id's letters and digits in a row scores 4 more,
and one of the provider that was asked 2 more.

After magpie's closest-model suggestion (yetone/magpie, MIT).
"""
from __future__ import annotations

import re

from openprogram.models.providers.catalog.models import get_models

_WORD = re.compile(r"[a-z]+|[0-9]+")


def name_words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _squashed(text: str) -> str:
    return "".join(name_words(text))


def closest_models(asked: str, *, provider: str = "", limit: int = 3) -> list[str]:
    """Up to ``limit`` registered ``provider:id`` names nearest to ``asked``."""
    words = name_words(asked)
    if not words or limit <= 0:
        return []
    whole = _squashed(asked)
    scored: list[tuple[int, str]] = []
    for model in get_models():
        names = set(name_words(model.name))
        ids = set(name_words(model.id))
        providers = set(name_words(str(model.provider)))
        score = own = 0
        for word in words:
            if word.isdigit():
                score += word in names or word in ids  # a version alone is no match
            elif word in names:
                score, own = score + 4, own + 1
            elif word in ids:
                score, own = score + 3, own + 1
            elif word in providers:
                score += 1
        if not own:
            continue  # a provider's name alone is no match
        if whole and (whole in _squashed(model.name) or whole in _squashed(model.id)):
            score += 4
        if provider and str(model.provider) == provider:
            score += 2
        scored.append((score, f"{model.provider}:{model.id}"))
    scored.sort(key=lambda hit: -hit[0])  # stable: catalog order breaks ties
    out: list[str] = []
    for _, name in scored:
        if name not in out:
            out.append(name)
        if len(out) == limit:
            break
    return out


def unknown_model_message(provider: str, model_id: str) -> str:
    """``Unknown model 'p':'m'``, with the nearest registered models when there are any."""
    message = f"Unknown model {provider!r}:{model_id!r}"
    near = closest_models(model_id, provider=provider)
    if near:
        message += "; did you mean " + ", ".join(near) + "?"
    return message

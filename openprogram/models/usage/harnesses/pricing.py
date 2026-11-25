"""API list prices for third-party harnesses' calls, from the models.dev catalogue.

The cost is what the calls would cost at API prices, not a subscription
bill. A 1-hour cache write costs twice the input price on Anthropic models
only. A call whose prompt (input + cache read + cache write) exceeds a
model's context tier is priced at that tier; a fast call at the model's
fast price. A model the catalogue does not know is never priced from a
similar name: its calls are counted as unpriced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_BRACKETS = re.compile(r"\[[^\]]*\]$")


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True)
class Price:
    base: Rates
    tier_above: Optional[int] = None
    tier: Optional[Rates] = None
    fast: Optional[Rates] = None
    anthropic: bool = False

    def rates(self, speed: str, over_tier: bool) -> Rates:
        if speed == "fast" and self.fast is not None:
            return self.fast
        if over_tier and self.tier is not None:
            return self.tier
        return self.base

    def cost(self, speed: str, over_tier: bool, input: int, cache_read: int,
             cache_write: int, cache_write_1h: int, output: int) -> float:
        r = self.rates(speed, over_tier)
        one_hour = r.input * 2 if self.anthropic else r.cache_write
        return (input * r.input + output * r.output + cache_read * r.cache_read
                + (cache_write - cache_write_1h) * r.cache_write + cache_write_1h * one_hour) / 1_000_000


def _rates(row: dict, prefix: str = "") -> Optional[Rates]:
    def get(name: str) -> Optional[float]:
        value = row.get(f"{name}{prefix}")
        return float(value) if isinstance(value, (int, float)) else None

    input_rate, output_rate = get("input"), get("output")
    if input_rate is None or output_rate is None:
        return None
    cache_read = get("cache_read")
    cache_write = get("cache_write")
    return Rates(input_rate, output_rate,
                 input_rate if cache_read is None else cache_read,
                 input_rate if cache_write is None else cache_write)


def _candidates(model: str) -> list[str]:
    name = _BRACKETS.sub("", model.strip().lower())
    out = [name]
    bare = _DATE_SUFFIX.sub("", name)
    if bare != name:
        out.append(bare)
    return out


def price(provider: str, model: str) -> Optional[Price]:
    """The model's prices, or None when the catalogue does not know it."""
    from openprogram.models.providers.catalog.sources import models_dev

    row = None
    for name in _candidates(model):
        row = models_dev.lookup(provider, name)
        if row:
            break
    if not row:
        return None
    base = _rates(row, "_cost")
    if base is None:
        return None
    tier_above = tier = fast = None
    for entry in row.get("cost_tiers") or []:
        spec = entry.get("tier") if isinstance(entry, dict) else None
        if isinstance(spec, dict) and spec.get("type") == "context" and isinstance(spec.get("size"), int):
            rates = _rates(entry)
            if rates is not None and (tier_above is None or spec["size"] < tier_above):
                tier_above, tier = spec["size"], rates
    for mode in row.get("speed_modes") or []:
        if isinstance(mode, dict) and mode.get("id") == "fast" and isinstance(mode.get("cost"), dict):
            fast = _rates(mode["cost"])
    return Price(base=base, tier_above=tier_above, tier=tier, fast=fast, anthropic=provider == "anthropic")


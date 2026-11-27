"""Rules for settings that only a layer above state can check.

The settings registry (``state.settings``) owns every setting's key, path,
widget and default, but a few values can only be judged by the layer that
uses them: a bind host or an Origin is canonicalised by the host's backend
endpoint, the outbound URL policy is parsed by the host's security model,
a domain map by the sandbox's network policy, and a replay recording by
the provider recorder. Those layers sit above state and never get imported
by it, so each registers its rules here for the keys it owns when it loads
(``host.lower_layers`` and ``models.lower_layers``, installed by the root
package before any setting is read or written).

A rule is one of:

* ``validate(value) -> error | None`` — the value is refused with the
  error; ``setting_value`` falls back to the default for a stored value
  that fails it;
* ``normalize(value) -> value`` — the form that is stored;
* ``check(value, config) -> error | None`` — a check of the value against
  the whole configuration as it would be after the write (``config`` is a
  copy with the value applied), for settings that depend on each other;
* ``after_write(value) -> None`` — what changes once the value is stored.

A key with no registered rule passes unchecked and is stored as given,
which is what a process where the owning layer never loaded can judge.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

RULES = frozenset({"validate", "normalize", "check", "after_write"})

_rules: dict[str, dict[str, Callable[..., Any]]] = {}


def register(key: str, **rules: Callable[..., Any]) -> None:
    """Install the owning layer's rules for one setting key."""
    unknown = set(rules) - RULES
    if unknown:
        raise ValueError(f"unknown setting rules: {sorted(unknown)}")
    _rules.setdefault(key, {}).update(rules)


def _rule(key: str, name: str) -> Callable[..., Any] | None:
    return _rules.get(key, {}).get(name)


def has(key: str, name: str) -> bool:
    """Whether a rule named ``name`` is registered for ``key``."""
    return _rule(key, name) is not None


def validate(key: str, value: Any) -> str | None:
    """The registered validator's error for ``value``, or None."""
    rule = _rule(key, "validate")
    return rule(value) if rule else None


def validator(key: str) -> Callable[[Any], str | None]:
    """A ``SettingSpec.validate`` that asks the rule registered for ``key``
    at the moment it runs."""
    return lambda value: validate(key, value)


def normalize(key: str, value: Any) -> Any:
    """The form of ``value`` that is stored."""
    rule = _rule(key, "normalize")
    return rule(value) if rule else value


def check(key: str, value: Any, config: dict) -> str | None:
    """The registered check's error for ``value`` in ``config``, or None."""
    rule = _rule(key, "check")
    return rule(value, config) if rule else None


def after_write(key: str, value: Any) -> None:
    """Run what follows once ``value`` is stored."""
    rule = _rule(key, "after_write")
    if rule:
        rule(value)


__all__ = [
    "RULES",
    "after_write",
    "check",
    "has",
    "normalize",
    "register",
    "validate",
    "validator",
]

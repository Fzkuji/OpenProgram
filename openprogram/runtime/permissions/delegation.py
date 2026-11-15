"""What a sub-agent may do: what both its caller's turn and its own Agent allow.

A sub-agent (``agent``, foreground or background, fresh or forked, and a
message another agent delivers) starts from its caller's frozen permission
(``lifecycle.spawn_permission_snapshot``). Its own Agent may narrow that
with a ``permissions`` block on ``agent.json`` and never widen it, as a
Codex role narrows its parent's authority (``core/src/agent/role.rs``):

* **mode**: the stricter of the two, in ``MODE_ORDER``.
* **rules** ("allowed and denied tools", in the rule syntax of
  ``rule.py``): the caller's deny and ask rules plus the Agent's own; the
  caller's allow rules, cut to those the Agent also lists when it lists
  any. An Agent's allow rule alone never allows anything.
* **additional directories**: the caller's, cut to those the Agent lists
  when it lists any.
* **sandbox**: on when either turns it on.

The caller's settings are what the person chose for the conversation, so a
sub-agent whose Agent sets nothing works exactly as its caller would: the
intersection asks no question its caller would not.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html#permissions
"""
from __future__ import annotations

import copy
import dataclasses
import os
from typing import Any, Mapping, Optional

from openprogram.runtime.permissions.modes import normalize_permission

#: From the mode that lets the least run without the person to the one that
#: lets the most: Plan runs nothing that changes anything, Ask asks for each
#: change, Accept edits runs safe file edits, Auto lets a reviewer approve
#: most calls, Bypass runs everything.
MODE_ORDER = ("plan", "ask", "acceptEdits", "auto", "bypass")

#: The keys of an Agent's ``permissions`` block.
OWN_KEYS = ("mode", "allow", "deny", "ask", "additional_directories", "sandbox")


def stricter_mode(first: Any, second: Any) -> Optional[str]:
    """The stricter of two modes; an unknown or empty one does not count."""
    known = [mode for mode in (normalize_permission(first), normalize_permission(second)) if mode]
    return min(known, key=MODE_ORDER.index) if known else None


def _rules_dict(rules: Any) -> dict[str, list[str]]:
    if dataclasses.is_dataclass(rules) and not isinstance(rules, type):
        rules = dataclasses.asdict(rules)
    rules = rules if isinstance(rules, Mapping) else {}
    return {key: [str(item) for item in (rules.get(key) or []) if str(item)]
            for key in ("allow", "deny", "ask")}


def _union(first: list[str], second: list[str]) -> list[str]:
    return [*first, *(item for item in second if item not in first)]


def _same_dir(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))


def intersect(caller: Mapping[str, Any], own: Mapping[str, Any] | None) -> dict[str, Any]:
    """The settings a sub-agent runs with.

    ``caller`` holds the caller's ``permission_mode``, ``permission_rules``,
    ``additional_working_dirs`` and ``sandbox_enabled``; ``own`` is the
    Agent's validated ``permissions`` block (``OWN_KEYS``, every key
    optional). Returns the same four keys.
    """
    own = dict(own or {})
    mode = stricter_mode(caller.get("permission_mode"), own.get("mode")) or caller.get("permission_mode")
    rules = _rules_dict(caller.get("permission_rules"))
    if "allow" in own:
        wanted = set(own.get("allow") or ())
        rules["allow"] = [rule for rule in rules["allow"] if rule in wanted]
    rules["deny"] = _union(rules["deny"], list(own.get("deny") or ()))
    rules["ask"] = _union(rules["ask"], list(own.get("ask") or ()))
    dirs = [str(path) for path in (caller.get("additional_working_dirs") or ())]
    if "additional_directories" in own:
        wanted_dirs = {_same_dir(path) for path in own.get("additional_directories") or ()}
        dirs = [path for path in dirs if _same_dir(path) in wanted_dirs]
    sandbox = caller.get("sandbox_enabled")
    if own.get("sandbox") is True:
        sandbox = True
    return {"permission_mode": mode, "permission_rules": rules,
            "additional_working_dirs": dirs, "sandbox_enabled": sandbox}


def narrow_request(request: dict[str, Any], own: Mapping[str, Any] | None) -> dict[str, Any]:
    """``request`` (a stored turn request) narrowed by the Agent's ``own`` block, in place.

    The turn's ``turn_settings`` record names ``agent`` as the source of a
    mode or Sandbox the Agent made stricter.
    """
    if not own:
        return request
    before_mode, before_sandbox = request.get("permission_mode"), request.get("sandbox_enabled")
    narrowed = intersect(request, own)
    request["permission_mode"] = narrowed["permission_mode"]
    request["permission_rules"] = narrowed["permission_rules"]
    request["additional_working_dirs"] = narrowed["additional_working_dirs"]
    if narrowed["sandbox_enabled"] is not before_sandbox:
        request["sandbox_enabled"] = narrowed["sandbox_enabled"]
        request["sandbox_config_frozen"] = True
    record = request.get("turn_settings")
    if isinstance(record, dict) and isinstance(record.get("values"), dict):
        for key, before in (("permission_mode", before_mode), ("sandbox_enabled", before_sandbox)):
            if request.get(key) != before:
                record["values"][key] = copy.deepcopy(request.get(key))
                record.setdefault("sources", {})[key] = "agent"
    return request


__all__ = ["MODE_ORDER", "OWN_KEYS", "intersect", "narrow_request", "stricter_mode"]

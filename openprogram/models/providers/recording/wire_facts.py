"""What a model request carried on the wire, by name and digest only.

``wire_facts`` reads one captured request (``capture.CapturedRequest``) where
the adapter serialized its body: the model, the reasoning effort as the
provider reads it, OpenAI's ``include`` and ``store``, the tools by name with
a digest of their definitions, and a digest of the system text.

A ``--print`` run in this process observes its own requests
(``print_turn.recording_requests``). A run the Worker carries out for it is
seen in the Worker: the chat command watches its execution (:func:`watch`)
and the turn's ``result`` frame hands the facts back (:func:`take`), so the
run's header is the same whichever transport ran it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

#: The most executions watched at once; the oldest is dropped beyond it (a
#: turn that never sent its result frame).
WATCH_LIMIT = 64


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def wire_effort(wire: str, body: dict[str, Any]) -> Any:
    """The reasoning effort a request body asks for, as the provider reads
    it: the level (``medium``), a thinking budget (``budget:8000``), or
    ``None`` when the body sends none."""
    from openprogram.models.providers.recording import capture

    if wire == capture.RESPONSES:
        reasoning = body.get("reasoning")
        return reasoning.get("effort") if isinstance(reasoning, dict) else None
    if wire == capture.COMPLETIONS:
        return body.get("reasoning_effort")
    if wire == capture.ANTHROPIC:
        output = body.get("output_config")
        if isinstance(output, dict) and output.get("effort"):
            return output["effort"]
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") not in (None, "disabled"):
            budget = thinking.get("budget_tokens")
            return f"budget:{budget}" if budget else thinking.get("type")
        return None
    request = body.get("request") if isinstance(body.get("request"), dict) else body
    config = request.get("config") or request.get("generationConfig") or {}
    thinking = config.get("thinkingConfig") or config.get("thinking_config") if isinstance(
        config, dict) else None
    if isinstance(thinking, dict):
        level = thinking.get("thinkingLevel") or thinking.get("thinking_level")
        budget = thinking.get("thinkingBudget", thinking.get("thinking_budget"))
        return level or (f"budget:{budget}" if budget is not None else None)
    extra = body.get("additionalModelRequestFields")
    if isinstance(extra, dict) and extra:
        return json.dumps(extra, sort_keys=True, separators=(",", ":"))
    return None


def wire_facts(request: Any) -> dict[str, Any]:
    """What one captured request carried (module docstring). Only names and
    digests are kept."""
    units = request.units()
    tools = [unit for unit in units if unit.label.startswith("tools:")]
    system = [unit for unit in units if unit.label == "system" or unit.label.startswith("system:")]
    body = request.body
    return {
        "model": f"{request.provider}/{request.model_id}" if request.provider else request.model_id,
        "wire": request.wire,
        "effort": wire_effort(request.wire, body),
        "include": body.get("include"),
        "store": body.get("store"),
        "tools": [unit.label[len("tools:"):] for unit in tools],
        "tools_sha256": _sha256("\n".join(unit.text for unit in tools)),
        "system_sha256": _sha256("\n".join(unit.text for unit in system)) if system else None,
    }


# ---------------------------------------------------------------------------
# Watching an execution in a long-lived process (the Worker)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
# execution id -> (key the result frame names, facts noted so far)
_watched: "OrderedDict[str, tuple[str, list[dict[str, Any]]]]" = OrderedDict()
_by_key: dict[str, str] = {}
_observer_installed = False


def _note(request: Any) -> None:
    from openprogram.models.usage.context import call_context

    context = call_context()
    execution_id = getattr(context, "execution_id", None)
    with _lock:
        entry = _watched.get(execution_id) if execution_id else None
    if entry is None:
        return
    import time

    fact = {"time": time.time(), "call_kind": context.call_kind,
            "execution_id": execution_id, **wire_facts(request)}
    with _lock:
        entry[1].append(fact)


def _install() -> None:
    """Observe this process's requests from now on (once); only watched
    executions keep anything."""
    global _observer_installed
    if _observer_installed:
        return
    from openprogram.models.providers.recording import capture

    capture._observers.append(_note)
    _observer_installed = True


def watch(execution_id: str, key: str) -> None:
    """Keep the facts of ``execution_id``'s requests until :func:`take` is
    called with ``key`` (the message id its result frame names)."""
    if not execution_id or not key:
        return
    with _lock:
        _install()
        _watched[execution_id] = (key, [])
        _by_key[key] = execution_id
        while len(_watched) > WATCH_LIMIT:
            _old, (old_key, _facts) = _watched.popitem(last=False)
            _by_key.pop(old_key, None)


def take(key: str) -> list[dict[str, Any]] | None:
    """The facts noted for the execution watched under ``key``, ending the
    watch; ``None`` when none is watched under it."""
    with _lock:
        execution_id = _by_key.pop(key, None)
        if execution_id is None:
            return None
        _key, facts = _watched.pop(execution_id, (key, []))
    return list(facts)


__all__ = ["WATCH_LIMIT", "take", "watch", "wire_effort", "wire_facts"]

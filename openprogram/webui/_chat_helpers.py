"""Chat-input parsing and MessageStore → WebSocket bridge.

Extracted from server.py to keep the main module focused on app
construction and state. Both helpers are referenced via lazy
``from openprogram.webui import server as _s`` so they can reach the
broadcast / discovery / store helpers without import cycles.
"""
from __future__ import annotations

import json


def wire_message_store_broadcast() -> None:
    """Install a one-shot global listener on the process-wide store.

    Idempotent: the first call registers, subsequent ones are no-ops. The
    listener lives for the process lifetime; there's no matching unsubscribe
    because the store itself is the single source of truth.
    """
    from openprogram.webui import server as _s
    if getattr(wire_message_store_broadcast, "_installed", False):
        return
    store = _s._get_message_store()

    def _on_frame(session_id: str, frame: dict) -> None:
        envelope = {"type": "chat_response", "data": dict(frame)}
        envelope["data"]["session_id"] = session_id
        _s._broadcast(json.dumps(envelope, default=str))

    store.subscribe_all(_on_frame)
    wire_message_store_broadcast._installed = True  # type: ignore[attr-defined]


def parse_chat_input(text: str) -> dict:
    """Parse user input to determine intent.

    Returns dict with keys:
      - action: "run" or "query"
      - function: function name (if applicable)
      - kwargs: dict of arguments (if applicable)
      - raw: original text
    """
    from openprogram.webui import server as _s
    text = text.strip()
    lower = text.lower()

    # "create ..." -> meta create
    if lower.startswith("create "):
        rest = text[7:].strip()
        if lower.startswith("create app "):
            return {"action": "run", "function": "create_app",
                    "kwargs": {"description": text[11:].strip()}, "raw": text}
        if lower.startswith("create skill "):
            return {"action": "run", "function": "create_skill",
                    "kwargs": {"name": text[13:].strip()}, "raw": text}
        name = None
        desc = rest
        if "--name " in rest:
            idx = rest.index("--name ")
            name = rest[idx + 7:].strip().split()[0]
            desc = rest[:idx].strip().strip('"').strip("'")
        elif " as " in rest:
            parts = rest.rsplit(" as ", 1)
            desc = parts[0].strip().strip('"').strip("'")
            name = parts[1].strip()
        if not name:
            name = None  # let create() auto-generate from description
        kwargs = {"description": desc}
        if name is not None:
            kwargs["name"] = name
        return {"action": "run", "function": "create", "kwargs": kwargs, "raw": text}

    # "edit ..." -> meta edit
    if lower.startswith("edit "):
        rest = text[5:].strip()
        parts = rest.split(maxsplit=1)
        name = parts[0]
        instruction = parts[1] if len(parts) > 1 else None
        kwargs = {"name": name}
        if instruction:
            kwargs["instruction"] = instruction
        return {"action": "run", "function": "edit", "kwargs": kwargs, "raw": text}

    # "run func_name key=val ..." -> direct run
    if lower.startswith("run "):
        rest = text[4:].strip()
        try:
            import shlex
            parts = shlex.split(rest)
        except ValueError:
            parts = rest.split()
        func_name = parts[0] if parts else ""
        kwargs = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass
                kwargs[k] = v
        return {"action": "run", "function": func_name, "kwargs": kwargs, "raw": text}

    # Bare function name
    available = _s._discover_functions()
    for f in available:
        fname = f["name"]
        if lower.startswith(fname + " ") or lower == fname:
            rest = text[len(fname):].strip()
            kwargs = {}
            for p in rest.split():
                if "=" in p:
                    k, v = p.split("=", 1)
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    kwargs[k] = v
                elif f["params"]:
                    for param_name in f["params"]:
                        if param_name not in kwargs and param_name != "runtime":
                            kwargs[param_name] = rest
                            break
                    break
            return {"action": "run", "function": fname, "kwargs": kwargs, "raw": text}

    # Default: general LLM query
    return {"action": "query", "raw": text}

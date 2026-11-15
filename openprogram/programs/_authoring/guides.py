"""Tool guides: instructions a tool hands the model when a call needs them.

A tool registers guides instead of putting every rule in its schema. The agent
loop delivers them beside tool results (the attachment sent after a group of
results, ``shared.tool_attachments``), so a short schema still leads the model
to the full instructions at the moment it uses a feature:

``open``      the first result of the tool carries it.
``included``  the first result of a call whose arguments match ``when``.
              Both end with the index of the tool's lookup guides.
``lookup``    listed by name with ``summary``; ``help(name)`` returns it.
``on_error``  a failed result of a call matching ``when`` carries it.

``required_for(args)`` marks a guide the call cannot run without:
``prechecks.call_problem`` sends the call back and the result carries the guide,
so the retry is informed.

Whether the model knows a guide is read from the context it is looking at, not
from a ledger: a guide counts as known while its marked text is in the last
request the loop sent (or in a result delivered since). Compaction, cleared
tool results, a new agent and a restarted worker all drop the text, and the
next call that needs the guide delivers it again.

Design: docs/reference/design/function/tool-guides.html
"""
from __future__ import annotations

import json

import contextvars
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, Iterable, Literal, Optional

Mode = Literal["open", "included", "lookup", "on_error"]


@dataclass(frozen=True)
class Guide:
    name: str
    text: Callable[[], str] | str
    mode: Mode = "lookup"
    summary: str = ""
    when: Optional[Callable[[dict], bool]] = None
    required_for: Optional[Callable[[dict], bool]] = None
    # Deferred tools this guide teaches. Delivering it names them and how to
    # load them (``activation_note``); it loads nothing itself.
    activates: tuple[str, ...] = ()

    def body(self) -> str:
        text = self.text() if callable(self.text) else self.text
        return inspect.cleandoc(text or "")

    def applies(self, args: dict) -> bool:
        if self.when is None:
            return True
        try:
            return bool(self.when(args))
        except Exception:
            return False

    def required(self, args: dict) -> bool:
        if self.required_for is None:
            return False
        try:
            return bool(self.required_for(args))
        except Exception:
            return False


_registry: dict[str, list[Guide]] = {}


def register(tool: str, guides: Iterable[Guide]) -> None:
    """Declare ``tool``'s guides; a later call replaces them."""
    items = list(guides)
    names = [guide.name for guide in items]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate guide names for {tool}")
    _registry[tool] = items


def guides_for(tool: str) -> list[Guide]:
    return list(_registry.get(tool, ()))


def find(name: str) -> tuple[str, Guide] | None:
    for tool, items in _registry.items():
        for guide in items:
            if guide.name == name:
                return tool, guide
    return None


def names() -> list[str]:
    return sorted(guide.name for items in _registry.values() for guide in items)


def marker(name: str) -> str:
    return f'<guide name="{name}">'


def render(guide: Guide, args: dict | None = None) -> str:
    """The guide as the model reads it, wrapped in the marker that makes it known."""
    parts = [guide.body()]
    if guide.mode in ("open", "included"):
        index = lookup_index(_owner(guide), args or {})
        if index:
            parts.append(index)
    parts.append(activation_note(guide.activates))
    return f"{marker(guide.name)}\n" + "\n\n".join(p for p in parts if p) + "\n</guide>"


def activation_note(names: Iterable[str]) -> str:
    """The line that names the deferred tools a guide (or an opened
    application) teaches and how to load them, or "".

    Loading stays with the model's own ``tool_search`` call, as every peer
    does it (Codex leaves an app the user mentions deferred): the tools
    array changes only after a search, in one form per provider.
    """
    listed = [name for name in dict.fromkeys(names or ()) if isinstance(name, str) and name]
    if not listed:
        return ""
    tools = ", ".join(f"`{name}`" for name in listed)
    return f"Tools for this: {tools}. Load them with `tool_search select:{','.join(listed)}`."


def lookup_index(tool: str, args: dict) -> str:
    rows = [f'- help("{guide.name}"): {guide.summary}'
            for guide in guides_for(tool) if guide.mode == "lookup" and guide.applies(args)]
    return "More instructions, read when the situation comes up:\n" + "\n".join(rows) if rows else ""


def _owner(guide: Guide) -> str:
    found = find(guide.name)
    return found[0] if found else ""


# -- what the model is looking at ---------------------------------------------

@dataclass
class _View:
    text: str = ""
    delivered: set[str] = field(default_factory=set)


_view: contextvars.ContextVar[Optional[_View]] = contextvars.ContextVar("tool_guides_view", default=None)


def begin_loop() -> contextvars.Token:
    """Called by an agent loop: guides are tracked against this loop's requests."""
    return _view.set(_View())


def end_loop(token: contextvars.Token) -> None:
    _view.reset(token)


def observe_request(messages: Iterable[Any]) -> None:
    """Record the messages of the request the loop is about to send."""
    view = _view.get()
    if view is None:
        return
    view.text = "\n".join(_texts(messages))
    view.delivered.clear()


def _texts(messages: Iterable[Any]) -> Iterable[str]:
    from openprogram.models.providers.shared.tool_attachments import record

    for message in messages or ():
        # A guide delivered to a call is sent after its result, from the
        # result's attachment record.
        attached = record(message.get("details") if isinstance(message, dict)
                          else getattr(message, "details", None))
        if attached:
            yield attached["text"]
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            yield content
            continue
        for block in content or ():
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str):
                yield text


def tracking() -> bool:
    return _view.get() is not None


def known(name: str) -> bool:
    view = _view.get()
    if view is None:
        return True
    tag = marker(name)
    # A prompt from another speaker (an agent starting this one) reaches the
    # model inside a JSON envelope, where the marker's quotes are escaped.
    return name in view.delivered or tag in view.text or json.dumps(tag)[1:-1] in view.text


def _mark_delivered(guides: Iterable[Guide]) -> None:
    view = _view.get()
    if view is None:
        return
    for guide in guides:
        view.delivered.add(guide.name)


# -- decisions used by permission and the agent loop ---------------------------

def missing_required(tool: str, args: dict) -> list[Guide]:
    """Guides this call needs that the model does not have in view."""
    if not tracking():
        return []
    return [guide for guide in guides_for(tool) if guide.required(args) and not known(guide.name)]


def refusal(tool: str, guides: list[Guide]) -> str:
    listed = ", ".join(f'"{guide.name}"' for guide in guides)
    return (f"{tool} needs its instructions first ({listed}); they follow this message. "
            "Read them, then call again.")


def due(tool: str, args: dict, *, failed: bool) -> list[Guide]:
    """Guides the result of this call should carry."""
    if not tracking():
        return []
    picked: list[Guide] = []
    for guide in guides_for(tool):
        if known(guide.name):
            continue
        if guide.mode == "open":
            picked.append(guide)
        elif guide.mode == "included" and guide.applies(args):
            picked.append(guide)
        elif guide.mode == "on_error" and failed and guide.applies(args):
            picked.append(guide)
        elif guide.mode == "lookup" and failed and guide.required(args):
            picked.append(guide)
    return picked


def deliver(guides: list[Guide], args: dict | None = None) -> str:
    """Rendered text for ``guides``, now counted as known."""
    if not guides:
        return ""
    _mark_delivered(guides)
    return "\n\n".join(render(guide, args) for guide in guides)


def attach(tool: str, args: Any, result: Any) -> Any:
    """Record the guides this call is due as the attachment sent after its
    result (``shared.tool_attachments``), not inside the tool's own text.
    A guide that teaches deferred tools names them (``activation_note``);
    nothing is loaded until the model searches for them."""
    if not tracking():
        return result
    if tool in _registry:
        call_args = args if isinstance(args, dict) else {}
        due_now = due(tool, call_args, failed=bool(getattr(result, "is_error", False)))
        text = deliver(due_now, call_args)
        if text:
            from openprogram.models.providers.shared.tool_attachments import with_attachment
            result = result.model_copy(update={"details": with_attachment(
                getattr(result, "details", None), text,
                guides=[guide.name for guide in due_now], front=True)})
    return result


def help_text(name: str) -> str:
    """``help(name)``: a guide by name, or a tool's opening guide."""
    found = find(name)
    if found is None:
        opening = [guide for guide in guides_for(name) if guide.mode == "open"]
        if opening:
            found = (name, opening[0])
    if found is None:
        known_names = names()
        near = [item for item in known_names if item.startswith(name.split(".")[0])] or known_names
        raise LookupError(f"no guide named {name!r}; guides: {', '.join(near)}")
    _tool, guide = found
    return deliver([guide])


__all__ = [
    "Guide", "activation_note", "attach", "begin_loop", "deliver", "due", "end_loop", "find", "guides_for",
    "help_text", "known", "lookup_index", "marker", "missing_required", "names",
    "observe_request", "refusal", "register", "render", "tracking",
]

"""Another application's window in an App tab: list, attach, lay out, input, give back.

The desktop App's page drives these; the embedder for the worker's platform
does the work (``computer_use.desktop.embedding``). Every request
except ``embedded_window_input`` answers one ``embedded_window`` message
carrying the request's ``request_id``; input is posted without a reply, in
order, so pointer moves never wait on the socket.

Several *holders* may show one window at once, each named by an id the
page chooses: a window tab places the window (``place: true``), a preview
only shows it (``place: false``). The window is placed while any holder
places it, watched while only views hold it, and given back or forgotten
when the last holder lets go.

While any holder shows a window, the Worker follows it by the operating
system's events (``embedding.follow``) and signals ``embedded_windows``
keyed by the window id when it moves, is resized, minimized or restored,
renamed or closed; the page reads its state again then. A window that
comes with ``follows: true`` is followed this way; without it the page
looks again on its own timer while the view is open.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from openprogram.host._platform import changes

TOPIC = "embedded_windows"
changes.publish(TOPIC)

_MAX_EVENTS = 200
_MAX_EXCLUDED = 32
_MAX_HOLDER = 128
_DEFAULT_HOLDER = "tab"

# window id -> {holder: places it}; and the windows the embedder has placed.
_holders: dict[int, dict[str, bool]] = {}
_placed: set[int] = set()
_lock = threading.RLock()
# Window events, once the Worker starts following (``start_following``);
# in the CLI and in tests nothing is followed and no thread starts.
_follower = None


def _embedding():
    from openprogram.programs.tools.computer_use.computer_use.desktop import embedding
    return embedding


def _window_id(cmd: dict) -> int:
    value = cmd.get("window_id")
    if type(value) is not int or value <= 0:
        raise ValueError("window_id must be a positive integer")
    return value


def _holder(cmd: dict) -> str:
    value = cmd.get("holder")
    if value is None:
        return _DEFAULT_HOLDER
    if not isinstance(value, str) or not value or len(value) > _MAX_HOLDER:
        raise ValueError("holder must be a non-empty string")
    return value


def _for_holder(embedder, window: dict, places: bool | None) -> dict:
    """The window as one holder sees it: whether that holder's page forwards input."""
    if not isinstance(window, dict):
        return window
    if window.get("open") is False:
        return window
    if places is not None:
        window = {**window, "input": bool(embedder.place_input if places else embedder.view_input)}
    return window


def _closed(window_id: int, window: Any) -> None:
    if isinstance(window, dict) and window.get("open") is False:
        _holders.pop(window_id, None)
        _placed.discard(window_id)
        _unfollow(window_id)


def start_following(make_source=None) -> None:
    """Follow shown windows by their events from now on (the Worker, at start)."""
    global _follower
    from openprogram.programs.tools.computer_use.computer_use.desktop.embedding import follow
    with _lock:
        if _follower is not None:
            return
        _follower = follow.Follower(make_source or follow.event_source,
                                    lambda window_id: changes.signal(TOPIC, str(window_id)))


def stop_following() -> None:
    global _follower
    with _lock:
        follower, _follower = _follower, None
    if follower is not None:
        follower.stop()


def _follow(window_id: int, window: Any) -> Any:
    """Follow a held window; mark it ``follows`` when its events will come."""
    follower = _follower
    if follower is None or not isinstance(window, dict) or window.get("open") is False:
        return window
    pid = window.get("pid")
    if follower.follow(window_id, pid if type(pid) is int else 0):
        return {**window, "follows": True}
    return window


def _unfollow(window_id: int) -> None:
    if _follower is not None:
        _follower.unfollow(window_id)


def attach(embedder, window_id: int, holder: str, place: bool, host: Any, parse_host) -> dict:
    """Add a holder; place the window for a placing one, else watch it unless it is placed."""
    with _lock:
        held = _holders.setdefault(window_id, {})
        previous = held.get(holder)
        held[holder] = place
        try:
            if place:
                window = embedder.attach(window_id, parse_host(host))
                _placed.add(window_id)
            elif window_id in _placed:
                window = embedder.state(window_id)
            else:
                window = embedder.watch(window_id)
        except Exception:
            if previous is None:
                held.pop(holder, None)
            else:
                held[holder] = previous
            if not held:
                _holders.pop(window_id, None)
            raise
        _closed(window_id, window)
        return _follow(window_id, _for_holder(embedder, window, place))


def layout(embedder, window_id: int, holder: str, host: Any, parse_host) -> dict:
    """Lay the window out for a placing holder; for any other holder, only describe it."""
    with _lock:
        held = _holders.get(window_id)
        if not held:
            raise _embedding().EmbedError("That window is not embedded.")
        places = held.get(holder)
        if places:
            window = embedder.layout(window_id, parse_host(host))
        else:
            window = embedder.state(window_id)
        _closed(window_id, window)
        return _for_holder(embedder, window, places)


def state(embedder, window_id: int, holder: str | None) -> dict:
    with _lock:
        window = embedder.state(window_id)
        places = (_holders.get(window_id) or {}).get(holder) if holder is not None else None
        _closed(window_id, window)
        return _for_holder(embedder, window, places)


def detach(embedder, window_id: int, holder: str) -> None:
    """Remove a holder; give the window back when no holder places it, forget it when none holds it."""
    with _lock:
        held = _holders.get(window_id)
        if held is None:
            # Not held here (a restored tab, or a worker that restarted): give it back all the same.
            embedder.detach(window_id)
            _placed.discard(window_id)
            _unfollow(window_id)
            return
        held.pop(holder, None)
        if window_id in _placed and not any(held.values()):
            _placed.discard(window_id)
            embedder.detach(window_id)
            if held:
                # Views still show it: keep it for their input, where it now is.
                try:
                    embedder.watch(window_id)
                except Exception:
                    _holders.pop(window_id, None)
                    _unfollow(window_id)
                    raise
        elif not held and window_id not in _placed:
            embedder.detach(window_id)
        if not held:
            _holders.pop(window_id, None)
            _unfollow(window_id)


async def _reply(ws, cmd: dict, **data: Any) -> None:
    payload = {"request_id": cmd.get("request_id"), "action": cmd.get("action"), **data}
    await ws.send_text(json.dumps({"type": "embedded_window", "data": payload}))


async def _answer(ws, cmd: dict, work) -> None:
    embedding = _embedding()
    try:
        result = await asyncio.to_thread(work, embedding)
    except (embedding.EmbedError, ValueError) as exc:
        await _reply(ws, cmd, status="error", error=str(exc))
        return
    except Exception as exc:  # the platform API failed in a way nothing above names
        await _reply(ws, cmd, status="error", error=f"{type(exc).__name__}: {exc}")
        return
    await _reply(ws, cmd, status="ok", **(result or {}))


async def handle_list(ws, cmd: dict):
    raw = cmd.get("exclude_pids")
    excluded = {p for p in raw[:_MAX_EXCLUDED] if type(p) is int} if isinstance(raw, list) else set()
    await _answer(ws, cmd, lambda e: {"mode": e.embedder().mode, "windows": e.embedder().list_windows(excluded)})


async def handle_attach(ws, cmd: dict):
    def work(e):
        place = cmd.get("place")
        if place is not None and not isinstance(place, bool):
            raise ValueError("place must be true or false")
        return {"window": attach(e.embedder(), _window_id(cmd), _holder(cmd), place is not False,
                                 cmd.get("host"), e.parse_host)}
    await _answer(ws, cmd, work)


async def handle_layout(ws, cmd: dict):
    await _answer(ws, cmd, lambda e: {"window": layout(e.embedder(), _window_id(cmd), _holder(cmd),
                                                       cmd.get("host"), e.parse_host)})


async def handle_state(ws, cmd: dict):
    def work(e):
        holder = _holder(cmd) if cmd.get("holder") is not None else None
        return {"window": state(e.embedder(), _window_id(cmd), holder)}
    await _answer(ws, cmd, work)


async def handle_detach(ws, cmd: dict):
    def work(e):
        detach(e.embedder(), _window_id(cmd), _holder(cmd))
        return {}
    await _answer(ws, cmd, work)


async def handle_input(ws, cmd: dict):
    events = cmd.get("events")
    if not isinstance(events, list):
        return
    events = [event for event in events if isinstance(event, dict)][:_MAX_EVENTS]
    embedding = _embedding()
    try:
        # Queues the events; the embedder posts them on its own thread.
        embedding.embedder().input(_window_id(cmd), events)
    except (embedding.EmbedError, ValueError) as exc:
        await _reply(ws, cmd, status="error", error=str(exc))


ACTIONS = {
    "embedded_windows": handle_list,
    "embedded_window_attach": handle_attach,
    "embedded_window_layout": handle_layout,
    "embedded_window_state": handle_state,
    "embedded_window_detach": handle_detach,
    "embedded_window_input": handle_input,
}

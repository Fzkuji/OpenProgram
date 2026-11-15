from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future
from pathlib import Path

import pytest

from openprogram.models.providers.types import AssistantMessage, EventDone, EventStart, Usage
from openprogram.models.providers.utils.event_stream import AssistantMessageEventStream
from openprogram.models.providers.utils.failover import stream_with_failover
from openprogram.models.providers.utils.stream_retry import ProviderStreamError
from openprogram.models.usage.context import usage_scope
from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.state.store.session.session_store import SessionStore
from openprogram.interfaces.server.routes.settings import routing


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UsageLedger:
    lg = UsageLedger(tmp_path / "usage.db")
    monkeypatch.setattr("openprogram.models.usage.ledger.default_ledger", lg)
    return lg


class _Opener:
    """Stands in for the overview's background thread: opens run inline, or
    are held until ``run()`` when ``hold`` is set."""

    def __init__(self) -> None:
        self.hold = False
        self.queued: list[tuple[Future, object, tuple]] = []

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        self.queued.append((future, fn, args))
        if not self.hold:
            self.run()
        return future

    def run(self) -> None:
        while self.queued:
            future, fn, args = self.queued.pop(0)
            future.set_result(fn(*args))


@pytest.fixture(autouse=True)
def opener(monkeypatch: pytest.MonkeyPatch) -> _Opener:
    fake = _Opener()
    monkeypatch.setattr(routing, "_opener", fake)
    routing._opening.clear()
    return fake


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    st = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: st)
    routing._lane_cache.clear()
    return st


def _turn(store: SessionStore, sid: str, uid: str, aid: str, prev: str | None,
          provider: str, model: str) -> None:
    store.append_message(sid, {"id": uid, "role": "user", "content": uid, "predecessor": prev})
    store.append_message(sid, {"id": aid, "role": "assistant", "content": aid,
                               "predecessor": uid, "provider": provider, "model": model})


def test_a_failover_switch_is_kept_in_the_ledger(ledger: UsageLedger) -> None:
    class Model:
        def __init__(self, provider: str, mid: str) -> None:
            self.provider, self.id = provider, mid

    primary, fallback = Model("opencode-go", "kimi-k2"), Model("deepseek", "deepseek-chat")

    def base_stream(model, _context, _options):
        stream = AssistantMessageEventStream()
        message = AssistantMessage(content=[], api="openai-completions", provider=model.provider,
                                   model=model.id, usage=Usage(), timestamp=1)

        async def produce() -> None:
            stream.push(EventStart(partial=message))
            if model is primary:
                stream.fail(ProviderStreamError("HTTP 429: slow down", http_status=429, retryable=True))
                return
            stream.push(EventDone(reason="stop", message=message))

        asyncio.create_task(produce())
        return stream

    async def run() -> None:
        with usage_scope(session_id="s1"):
            stream = stream_with_failover(base_stream, primary, object(), None, [fallback])
            _ = [event async for event in stream]

    asyncio.run(run())
    [event] = ledger.route_events(since=0)
    assert event["session_id"] == "s1"
    assert (event["from_provider"], event["from_model"]) == ("opencode-go", "kimi-k2")
    assert (event["to_provider"], event["to_model"]) == ("deepseek", "deepseek-chat")
    assert event["reason"] == "rate_limit"


def test_overview_wires_branches_children_and_switches(store: SessionStore, ledger: UsageLedger) -> None:
    store.create_session("s1", "main", title="Paper notes")
    _turn(store, "s1", "u0", "a0", None, "deepseek", "deepseek-chat")
    _turn(store, "s1", "u1", "a1", "a0", "deepseek", "deepseek-chat")
    _turn(store, "s1", "u2", "a2", "a1", "deepseek", "deepseek-chat")
    _turn(store, "s1", "ub", "ab", "a0", "openai-codex", "gpt-6-astra")
    store.create_session("old", "main", title="Old")
    store.update_session("old", updated_at=time.time() - 30 * 86400)

    now = time.time()
    ledger.append(UsageEvent(ts=now - 10, session_id="s1", provider="deepseek", model_id="deepseek-chat"))
    for child in ("c1", "c2"):
        ledger.append(UsageEvent(ts=now - 5, session_id=child, parent_session_id="s1",
                                 provider="ark", model_id="doubao-seed"))
    ledger.append(UsageEvent(ts=now - 3, session_id="op-1234", provider="deepseek", model_id="deepseek-chat"))
    ledger.append_route_event(from_provider="opencode-go", from_model="kimi-k2",
                              to_provider="deepseek", to_model="deepseek-chat",
                              reason="rate_limit", session_id="s1", ts=now - 20)

    data = routing.build_overview(1800)

    session, background = data["sessions"]
    assert background["kind"] == "background"
    [bg_lane] = background["lanes"]
    assert (bg_lane["kind"], bg_lane["model"], bg_lane["events"]) == ("background", "deepseek-chat", 1)
    assert session["title"] == "Paper notes"
    branches = {lane["id"]: lane for lane in session["lanes"] if lane["kind"] == "branch"}
    assert branches["a2"]["trunk"] and branches["a2"]["model"] == "deepseek-chat"
    assert not branches["ab"]["trunk"] and branches["ab"]["provider"] == "openai-codex"
    [children] = [lane for lane in session["lanes"] if lane["kind"] == "children"]
    assert (children["count"], children["provider"], children["model"]) == (2, "ark", "doubao-seed")
    assert children["live"]
    assert data["idle_sessions"] == 1
    assert data["stats"] == {"requests": 4, "reroutes": 1}
    assert data["events"][0]["session_title"] == "Paper notes"
    assert set(data["accounts"]) == {"ark", "deepseek", "openai-codex", "opencode-go"}


def test_quiet_sessions_stay_out_of_the_map(store: SessionStore, ledger: UsageLedger) -> None:
    store.create_session("s1", "main", title="Yesterday")
    _turn(store, "s1", "u0", "a0", None, "deepseek", "deepseek-chat")
    store.update_session("s1", updated_at=time.time() - 86400)

    data = routing.build_overview(1800)
    assert data["sessions"] == []
    assert data["idle_sessions"] == 1


def test_an_unchanged_session_is_not_opened_again(store: SessionStore, ledger: UsageLedger,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    store.create_session("s1", "main", title="Paper notes")
    _turn(store, "s1", "u0", "a0", None, "deepseek", "deepseek-chat")
    routing.build_overview(1800)

    opened: list[str] = []
    get_session = store.get_session
    monkeypatch.setattr(store, "get_session", lambda sid: opened.append(sid) or get_session(sid))
    [session] = routing.build_overview(1800)["sessions"]
    assert opened == [] and session["lanes"][0]["model"] == "deepseek-chat"

    _turn(store, "s1", "u1", "a1", "a0", "openai-codex", "gpt-6-astra")
    [session] = routing.build_overview(1800)["sessions"]
    assert opened == ["s1"] and session["lanes"][0]["model"] == "gpt-6-astra"


def test_a_session_still_opening_shows_from_the_ledger_until_ready(
        store: SessionStore, ledger: UsageLedger, opener: _Opener,
        monkeypatch: pytest.MonkeyPatch) -> None:
    store.create_session("s1", "main", title="Paper notes")
    _turn(store, "s1", "u0", "a0", None, "deepseek", "deepseek-chat")
    ledger.append(UsageEvent(ts=time.time() - 5, session_id="s1", provider="deepseek", model_id="deepseek-chat"))
    opener.hold = True
    monkeypatch.setattr(routing, "_OPEN_BUDGET", 0)

    first = routing.build_overview(1800)
    [session] = first["sessions"]
    assert first["partial"] and session["title"] == "Paper notes"
    assert [lane["kind"] for lane in session["lanes"]] == ["calls"]

    opener.run()
    assert routing._opening == {}
    second = routing.build_overview(1800)
    [session] = second["sessions"]
    assert not second["partial"] and session["lanes"][0]["kind"] == "branch"

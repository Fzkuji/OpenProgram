"""Which credential a request uses, and what each kind of failure does to it
(openprogram/models/auth/selection.py)."""
from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.models.auth import selection
from openprogram.models.auth.credential_provider import (
    CredentialProvider, set_credential_provider_for_testing,
)
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import AuthConfigError, Credential, CredentialData
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, SimpleStreamOptions,
)
from openprogram.models.providers.utils.failures.errors import LLMError

P = "testprov"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    value = AuthStore(root=tmp_path / "state")
    set_store_for_testing(value)
    set_credential_provider_for_testing(CredentialProvider(store=value))
    sessions = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.shared._default_store",
                        sessions, raising=False)
    monkeypatch.setattr(selection.asyncio, "sleep", _no_sleep)
    selection.forget_bindings()
    value.sessions = sessions
    yield value
    selection.forget_bindings()
    set_store_for_testing(None)
    set_credential_provider_for_testing(None)


async def _no_sleep(_s):
    return None


def _key(store: AuthStore, label: str, *, value: str = "", provider: str = P) -> Credential:
    cred = Credential(provider_id=provider, kind="api_key",
                      payload=CredentialData(kind="api_key", auth_value=value or f"sk-{label}-0123456789"),
                      label=label)
    return store.add_credential(cred)


def _session(store: AuthStore, sid: str = "s1") -> str:
    store.sessions.create_session(sid, "main")
    return sid


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------

def test_in_order_takes_the_first_usable_one(store):
    a, b, c = _key(store, "a"), _key(store, "b"), _key(store, "c")
    assert selection.choose(P, record=False).id == a.id
    store.update(P, a.id, lambda r: setattr(r, "enabled", False))
    assert selection.choose(P, record=False).id == b.id
    selection.mark(P, b.id, "cooling", until_ms=int(time.time() * 1000) + 60_000)
    assert selection.choose(P, record=False).id == c.id


@pytest.mark.parametrize("status", ["quota", "invalid_key", "needs_login"])
def test_a_held_or_refused_credential_is_skipped(store, status):
    a, b = _key(store, "a"), _key(store, "b")
    selection.mark(P, a.id, status, until_ms=int(time.time() * 1000) + 60_000)
    assert selection.choose(P, record=False).id == b.id


def test_a_blocked_model_skips_the_credential_for_that_model_only(store):
    a, b = _key(store, "a"), _key(store, "b")
    selection.block_model(P, a.id, "big-model")
    assert selection.choose(P, "big-model", record=False).id == b.id
    assert selection.choose(P, "small-model", record=False).id == a.id


def test_a_cooldown_that_passed_is_usable_again(store):
    a, _ = _key(store, "a"), _key(store, "b")
    selection.mark(P, a.id, "cooling", until_ms=int(time.time() * 1000) - 1)
    assert selection.choose(P, record=False).id == a.id


def test_evenly_takes_each_in_turn(store):
    ids = [_key(store, n).id for n in "abc"]
    store.mutate(P, lambda pool: setattr(pool, "strategy", "evenly"))
    picked = [selection.choose(P).id for _ in range(4)]
    assert picked == [ids[0], ids[1], ids[2], ids[0]]


def test_least_used_takes_the_one_used_least_today(store):
    a, b = _key(store, "a"), _key(store, "b")
    store.mutate(P, lambda pool: setattr(pool, "strategy", "least_used"))
    now = int(time.time() * 1000)
    store.record_use(P, a.id, now)
    assert selection.choose(P, record=False).id == b.id
    # A key used a lot on earlier days is not held back by it: today counts.
    usage = store.usage_file(P)
    import json
    doc = json.loads(usage.read_text())
    doc["uses"][a.id].update(use_count=500, today=40, day="2000-01-01")
    doc["uses"][b.id] = {"use_count": 1, "today": 1, "day": time.strftime("%Y-%m-%d"),
                         "last_used_at_ms": now}
    usage.write_text(json.dumps(doc))
    assert selection.choose(P, record=False).id == a.id
    assert store.find(P, a.id).use_count == 500


def test_no_credential_is_a_configuration_error(store):
    with pytest.raises(AuthConfigError):
        selection.choose(P)


def test_nothing_usable_lists_every_credential_state(store):
    a, b = _key(store, "a"), _key(store, "b")
    selection.mark(P, a.id, "invalid_key")
    selection.mark(P, b.id, "cooling", until_ms=int(time.time() * 1000) + 30_000)
    with pytest.raises(selection.NoUsableCredential) as err:
        selection.choose(P)
    states = {r["id"]: r["state"] for r in err.value.rows}
    assert states == {a.id: "invalid_key", b.id: "cooling"}
    assert 0 < err.value.wait_ms <= 30_000 and err.value.wait_label == "b"


# ---------------------------------------------------------------------------
# Binding and pin
# ---------------------------------------------------------------------------

def test_a_conversation_keeps_the_credential_it_started_on(store):
    sid = _session(store)
    ids = [_key(store, n).id for n in "ab"]
    store.mutate(P, lambda pool: setattr(pool, "strategy", "evenly"))
    first = selection.choose(P, session_id=sid).id
    assert [selection.choose(P, session_id=sid).id for _ in range(3)] == [first] * 3
    selection.forget_bindings()  # read back from the session's metadata
    assert selection.choose(P, session_id=sid).id == first
    assert selection.session_credentials(sid) == {"binding": {P: first}, "pin": {}}
    assert first in ids


def test_a_binding_moves_when_its_credential_cannot_be_used(store):
    sid = _session(store)
    a, b = _key(store, "a"), _key(store, "b")
    assert selection.choose(P, session_id=sid).id == a.id
    selection.mark(P, a.id, "cooling", until_ms=int(time.time() * 1000) + 60_000)
    assert selection.choose(P, session_id=sid).id == b.id
    selection.clear(P, a.id)
    assert selection.choose(P, session_id=sid).id == b.id


def test_a_pin_wins_and_does_not_fall_through(store):
    sid = _session(store)
    a, b = _key(store, "a"), _key(store, "b")
    assert selection.set_pin(sid, P, b.id) is True
    assert selection.choose(P, session_id=sid).id == b.id
    assert selection.session_credentials(sid) == {"binding": {P: b.id}, "pin": {P: b.id}}
    selection.mark(P, b.id, "invalid_key")
    with pytest.raises(selection.NoUsableCredential) as err:
        selection.choose(P, session_id=sid)
    assert err.value.pinned_id == b.id and err.value.wait_ms is None
    assert selection.set_pin(sid, P, None) is True
    assert selection.session_credentials(sid)["pin"] == {}
    assert selection.choose(P, session_id=sid).id == a.id


def test_a_pin_names_a_stored_credential_of_a_real_conversation(store):
    _key(store, "a")
    with pytest.raises(AuthConfigError):
        selection.set_pin(_session(store), P, "cred_missing")
    assert selection.set_pin("no-such-session", P, None) is False


def test_sub_agents_and_goal_turns_use_the_parent_conversation(store):
    from openprogram.models.usage.context import bind_goal, usage_scope
    with usage_scope(session_id="child", parent_session_id="parent"):
        assert selection.conversation_of("child") == "parent"
    with bind_goal({"goal_session_id": "goal-conv"}, session_id="turn"):
        assert selection.conversation_of(None) == "goal-conv"
    assert selection.conversation_of("plain") == "plain"


# ---------------------------------------------------------------------------
# Failures through with_credential
# ---------------------------------------------------------------------------

class _Http(Exception):
    def __init__(self, status: int, text: str = "", retry_after: float | None = None):
        super().__init__(text or f"HTTP {status}")
        self.status_code = status
        if retry_after is not None:
            self.retry_after_s = retry_after


def _model() -> Model:
    return Model(id="m1", name="m1", api="openai-completions", provider=P, base_url="http://127.0.0.1:9")


def _attempt(failures: dict[str, list[BaseException]], seen: list[str]):
    """An adapter whose request with key ``k`` raises the next queued failure
    for ``k``, else answers."""
    def attempt(opts):
        async def run():
            seen.append(opts.credential.label)
            queue = failures.get(opts.credential.label) or []
            if queue:
                raise queue.pop(0)
            yield EventStart(partial=AssistantMessage.empty(_model()))
            message = AssistantMessage.empty(_model())
            message.stop_reason = "stop"
            yield EventDone(reason="stop", message=message)
        return run()
    return attempt


def _run(attempt, **opts):
    async def go():
        events = []
        async for event in selection.with_credential(_model(), SimpleStreamOptions(**opts), attempt):
            events.append(event)
        return events
    return asyncio.run(go())


def _row(store, cred):
    return store.find(P, cred.id)


def test_a_rate_limit_cools_the_key_and_the_next_one_answers(store):
    a, b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    events = _run(_attempt({"a": [_Http(429, "rate limited", retry_after=120)]}, seen))
    assert seen == ["a", "b"]
    assert _row(store, a).status == "cooling"
    assert _row(store, a).cooldown_until_ms > time.time() * 1000 + 100_000
    retries = [e for e in events if getattr(e, "type", None) == "retry"]
    assert len(retries) == 1 and retries[0].reason == "credential" and "b" in retries[0].error
    assert events[-1].message.credential_id == b.id


def test_a_short_retry_after_is_waited_out_on_the_same_key(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(429, retry_after=2)]}, seen))
    assert seen == ["a", "a"]
    assert _row(store, a).status == "ready"


def test_a_rate_limit_without_retry_after_backs_off_longer_each_strike(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(429)]}, seen))
    first = _row(store, a).cooldown_until_ms - time.time() * 1000
    selection.clear(P, a.id)
    store.update(P, a.id, lambda r: setattr(r, "strikes", 2))
    _run(_attempt({"a": [_Http(429)]}, seen))
    second = _row(store, a).cooldown_until_ms - time.time() * 1000
    assert 50_000 < first <= 60_000 and 230_000 < second <= 240_000


def test_spent_quota_holds_the_key_until_its_reset(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(402, "insufficient balance")]}, seen))
    assert seen == ["a", "b"]
    row = _row(store, a)
    assert row.status == "quota" and row.cooldown_until_ms > time.time() * 1000 + 3600_000


def test_a_refused_key_is_marked_invalid(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(401, "invalid api key")]}, seen))
    assert seen == ["a", "b"] and _row(store, a).status == "invalid_key"


def test_a_refused_sign_in_is_refreshed_and_sent_again(store, monkeypatch):
    signed = store.add_credential(Credential(
        provider_id=P, kind="oauth", label="a",
        payload=CredentialData(kind="oauth", auth_value="old-token",
                               data={"refresh_token": "r", "expires_at_ms": int(time.time() * 1000) + 3600_000})))
    _key(store, "b")

    def refresh(self, cred):
        def _swap(row):
            row.payload.auth_value = "new-token"
        store.update(P, cred.id, _swap)
        return store.find(P, cred.id)

    monkeypatch.setattr(CredentialProvider, "force_refresh_sync", refresh)
    seen: list[str] = []
    _run(_attempt({"a": [_Http(401, "token expired")]}, seen))
    assert seen == ["a", "a"]
    assert _row(store, signed).status == "ready"


def test_a_model_the_key_cannot_use_is_blocked_for_that_model(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(404, "The model `m1` does not exist or you do not have access to it.")]}, seen))
    assert seen == ["a", "b"]
    row = _row(store, a)
    assert "m1" in row.blocked_models and row.status == "ready"


def test_a_server_error_is_retried_on_the_same_key(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []
    _run(_attempt({"a": [_Http(503, "overloaded"), _Http(502)]}, seen))
    assert seen == ["a", "a", "a"] and _row(store, a).status == "ready"


def test_a_server_error_after_two_retries_is_the_callers(store):
    _key(store, "a")
    seen: list[str] = []
    with pytest.raises(_Http):
        _run(_attempt({"a": [_Http(500), _Http(500), _Http(500)]}, seen))
    assert seen == ["a", "a", "a"]


def test_a_failure_after_output_is_not_retried_elsewhere(store):
    a, _b = _key(store, "a"), _key(store, "b")
    seen: list[str] = []

    def attempt(opts):
        async def run():
            seen.append(opts.credential.label)
            yield EventStart(partial=AssistantMessage.empty(_model()))
            yield {"type": "text_delta", "delta": "hi"}
            raise _Http(429, retry_after=120)
        return run()

    with pytest.raises(_Http):
        _run(attempt)
    assert seen == ["a"] and _row(store, a).status == "cooling"


def test_nothing_usable_ends_with_an_error_listing_each_key(store):
    a, b = _key(store, "a"), _key(store, "b")
    selection.mark(P, a.id, "invalid_key")
    selection.mark(P, b.id, "quota", until_ms=int(time.time() * 1000) + 3600_000)
    with pytest.raises(LLMError) as err:
        _run(_attempt({}, []))
    failure = err.value.failure
    assert failure["cause"] == "credentials_unavailable"
    assert {r["id"]: r["state"] for r in failure["credentials"]} == {a.id: "invalid_key", b.id: "quota"}
    assert err.value.retryable is False


def test_a_key_back_within_a_minute_is_waited_for(store, monkeypatch):
    a = _key(store, "a")
    selection.mark(P, a.id, "cooling", until_ms=int(time.time() * 1000) + 20_000)
    waited: list[float] = []

    async def sleep(seconds):
        waited.append(seconds)
        selection.clear(P, a.id)  # the cooldown runs out while it waits

    monkeypatch.setattr(selection.asyncio, "sleep", sleep)
    seen: list[str] = []
    events = _run(_attempt({}, seen))
    assert seen == ["a"] and 15 < waited[0] <= 20.1
    waits = [e for e in events if getattr(e, "type", None) == "retry"]
    assert waits and waits[0].reason == "credential_wait"


def test_a_callers_own_key_is_sent_as_given(store):
    _key(store, "a")
    seen: list[str] = []

    def attempt(opts):
        async def run():
            seen.append(opts.api_key)
            message = AssistantMessage.empty(_model())
            yield EventDone(reason="stop", message=message)
        return run()

    from openprogram.models.providers.streaming.stream import _credentialed
    wrapped = _credentialed(lambda model, context, opts: attempt(opts))

    async def go():
        async for _ in wrapped(_model(), None, SimpleStreamOptions(api_key="own-key")):
            pass
    asyncio.run(go())
    assert seen == ["own-key"]


@pytest.mark.parametrize("exc,kind", [
    (_Http(429), "rate_limit"),
    (_Http(402), "quota"),
    (_Http(429, "You exceeded your current quota, please check your plan and billing details"), "quota"),
    (_Http(401), "auth"),
    (_Http(403, "permission denied"), "auth"),
    (_Http(403, "Incorrect API key provided"), "auth"),
    (_Http(403, "Your organization must be verified to stream this model."), "model"),
    (_Http(403, '{"error": {"code": "unsupported_country_region_territory"}}'), "other"),
    (_Http(403, "Forbidden"), "other"),
    (_Http(404, "model_not_found: no access to model m1"), "model"),
    (_Http(500), "server"),
    (_Http(400, "bad request"), "other"),
])
def test_each_failure_is_classified(exc, kind):
    assert selection.classify(exc).kind == kind


# ---------------------------------------------------------------------------
# One key, pins, errors that stay
# ---------------------------------------------------------------------------

def test_one_key_that_hits_a_rate_limit_waits_and_is_sent_again(store, monkeypatch):
    a = _key(store, "a")
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)
        selection.clear(P, a.id)  # the cooldown runs out while it waits

    monkeypatch.setattr(selection.asyncio, "sleep", sleep)
    seen: list[str] = []
    events = _run(_attempt({"a": [_Http(429, "Too Many Requests")]}, seen))
    assert seen == ["a", "a"]
    notices = [e for e in events if getattr(e, "type", None) == "retry"]
    assert [n.reason for n in notices] == ["credential_wait"]
    assert 50 < slept[0] <= 61
    assert getattr(events[-1], "type", None) == "done"


def test_a_policy_403_does_not_lock_the_only_key(store):
    a = _key(store, "a")
    seen: list[str] = []
    with pytest.raises(_Http):
        _run(_attempt({"a": [_Http(403, "unsupported_country_region_territory")]}, seen))
    assert _row(store, a).status == "ready"
    assert _run(_attempt({}, seen)) and seen == ["a", "a"]


def test_a_429_retry_does_not_use_up_the_refresh_a_401_gets(store, monkeypatch):
    signed = store.add_credential(Credential(
        provider_id=P, kind="oauth", label="a",
        payload=CredentialData(kind="oauth", auth_value="old-token",
                               data={"refresh_token": "r", "expires_at_ms": int(time.time() * 1000) + 3600_000})))
    refreshed: list[str] = []

    def refresh(self, cred):
        refreshed.append(cred.id)
        store.update(P, cred.id, lambda row: setattr(row.payload, "auth_value", "new-token"))
        return store.find(P, cred.id)

    monkeypatch.setattr(CredentialProvider, "force_refresh_sync", refresh)
    seen: list[str] = []
    _run(_attempt({"a": [_Http(429, retry_after=1), _Http(401, "token expired")]}, seen))
    assert seen == ["a", "a", "a"] and refreshed == [signed.id]
    assert _row(store, signed).status == "ready"


def test_a_pinned_key_that_is_only_cooling_is_a_rate_limit(store):
    from openprogram.models.providers.utils.failures.errors import ErrorReason
    sid = _session(store)
    _a, b = _key(store, "a"), _key(store, "b")
    selection.set_pin(sid, P, b.id)
    selection.mark(P, b.id, "cooling", until_ms=int(time.time() * 1000) + 600_000)
    with pytest.raises(LLMError) as err:
        _run(_attempt({}, []), session_id=sid)
    assert err.value.reason == ErrorReason.RATE_LIMIT and err.value.failure["pinned"] == b.id
    selection.mark(P, b.id, "invalid_key")
    with pytest.raises(LLMError) as err:
        _run(_attempt({}, []), session_id=sid)
    assert err.value.reason == ErrorReason.AUTHENTICATION


def test_a_stored_error_never_holds_the_key(store, monkeypatch):
    from openprogram.models.auth.manage import credential_row, provider_credentials
    secret = "sk-proj-SECRETSECRETSECRET0123456789"
    a = _key(store, "a", value=secret)
    monkeypatch.setenv("TESTPROV_ECHO", "x")
    selection.mark(P, a.id, "invalid_key", message=(
        f"401: Incorrect API key provided: {secret}. Authorization: Bearer {secret} "
        "url https://x.example/v1/models?key=AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK&alt=sse"))
    selection.block_model(P, a.id, "m1", message=f"no access with {secret}")
    row = credential_row(store.find(P, a.id))
    listing = repr(provider_credentials(P))
    on_disk = store.pool_file(P).read_text()
    text = row["last_error"]["message"]
    assert secret not in text and secret not in listing
    assert "AIzaFAKE" not in text and "····6789" in text
    assert on_disk.count(secret) == 1  # only the payload itself


def test_programs_and_sub_agents_spend_the_conversations_pinned_key(store):
    from openprogram.models.usage.context import usage_scope
    sid = _session(store, "conv")
    _a, b = _key(store, "a"), _key(store, "b")
    selection.set_pin(sid, P, b.id)
    # A Runtime call sends a provider cache key, not the conversation id.
    with usage_scope(session_id=sid):
        conv = selection.conversation_of("op-0123456789abcdef")
    assert conv == sid and selection.choose(P, session_id=conv).id == b.id
    # A sub-agent's child session names its parent in its metadata.
    store.sessions.create_session("child", "agent", parent_session_id=sid)
    assert selection.conversation_of("child") == sid
    assert selection.choose(P, session_id=selection.conversation_of("child")).id == b.id


def test_a_card_lists_a_key_that_is_not_in_use(store):
    from openprogram.models.usage.provider_status import in_use, keys_of
    a, b = _key(store, "a"), _key(store, "b")
    store.update(P, a.id, lambda r: setattr(r, "enabled", False))
    rows = keys_of(P)
    assert [(r["credential_id"], r["enabled"], r["active"]) for r in rows] == [
        (a.id, False, False), (b.id, True, True)]
    assert [r["credential_id"] for r in in_use(rows)] == [b.id]


def test_a_sign_in_is_named_by_its_email_alone():
    cred = Credential(provider_id=P, kind="oauth", payload=CredentialData(kind="oauth", auth_value="t"),
                      identity={"email": "me@example.com", "plan": "Plus"})
    assert cred.display_label() == "me@example.com"


def test_a_runtime_is_made_while_the_only_key_cools_down(store, monkeypatch):
    import openprogram.runtime.agent.runtime as runtime_package
    from openprogram.runtime.agent.runtime.factory import create_runtime
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runtime_package, "Runtime", lambda **kwargs: kwargs)
    a = _key(store, "a", provider="openai")
    selection.mark("openai", a.id, "cooling", until_ms=int(time.time() * 1000) + 30_000)
    with pytest.raises(selection.NoUsableCredential):
        selection.choose("openai", record=False)
    # The key is the provider's still; the request waits for it or says why.
    assert create_runtime(provider="openai") is not None
    store.remove_credential("openai", a.id)
    with pytest.raises(ValueError):
        create_runtime(provider="openai")


def test_a_sign_in_in_a_quota_hold_still_makes_its_runtime(store):
    from openprogram.models.providers.adapters.openai_codex import credentials as codex
    login = store.add_credential(Credential(
        provider_id="openai-codex", kind="oauth",
        payload=CredentialData(kind="oauth", auth_value="t", data={"refresh_token": "r"})))
    selection.mark("openai-codex", login.id, "quota", until_ms=int(time.time() * 1000) + 3600_000)
    codex.require_sign_in()
    store.remove_credential("openai-codex", login.id)
    store.add_credential(Credential(provider_id="openai-codex", kind="api_key",
                                    payload=CredentialData(kind="api_key", auth_value="sk-plain-0001")))
    with pytest.raises(AuthConfigError):
        codex.require_sign_in()


def test_a_pin_to_a_key_that_is_gone_says_how_to_get_back_to_auto(store):
    sid = _session(store)
    _key(store, "a")
    with pytest.raises(AuthConfigError, match=r"has no key nope; set this conversation back to Auto \(credential_id: null\)"):
        selection.set_pin(sid, P, "nope")


def test_a_pinned_key_that_is_turned_off_is_a_setting_not_a_failed_sign_in(store):
    from openprogram.models.providers.utils.failures.errors import ErrorReason
    sid = _session(store)
    _a, b = _key(store, "a"), _key(store, "b")
    selection.set_pin(sid, P, b.id)
    store.update(P, b.id, lambda r: setattr(r, "enabled", False))
    with pytest.raises(LLMError) as err:
        _run(_attempt({}, []), session_id=sid)
    assert err.value.reason == ErrorReason.INVALID_REQUEST


def test_a_key_that_is_an_environment_variables_value_is_stored_and_listed_once(store, monkeypatch):
    from openprogram.models.auth import manage
    monkeypatch.setattr("openprogram.models.auth.login.login_method_registry.env_vars",
                        lambda provider: ["TESTPROV_API_KEY"])
    monkeypatch.setenv("TESTPROV_API_KEY", "sk-from-the-environment-01")
    out = manage.add_api_key(P, "sk-from-the-environment-01", validate=False)
    assert [c.id for c in store.get(P).credentials] == [out["credential"]["id"]]
    monkeypatch.delenv("TESTPROV_API_KEY")
    assert [c.id for c in store.get(P).credentials] == [out["credential"]["id"]], "it outlives the variable"


def test_a_report_with_no_credential_named_is_the_only_or_active_keys(store, monkeypatch):
    from types import SimpleNamespace

    from openprogram.models.auth import manage
    from openprogram.models.usage import quota

    report = lambda account: SimpleNamespace(account=account, wire=lambda now: {"limits": [{"label": account or "unnamed"}]})
    monkeypatch.setattr(quota.default_store, "reports", lambda p: [report(""), report("b")])
    assert manage._limits_by_credential(P, ["a"], None) == {"a": [{"label": "unnamed"}], "b": [{"label": "b"}]}
    assert manage._limits_by_credential(P, ["a", "c"], "c")["c"] == [{"label": "unnamed"}]
    assert manage._limits_by_credential(P, ["a", "b"], "b")["b"] == [{"label": "b"}], "a named report wins"

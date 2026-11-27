"""Codex plan limits: read from its app-server, merged from its updates, stored for the usage page."""
from __future__ import annotations

import time

import pytest

from openprogram.models.usage import harnesses as oa
from openprogram.models.usage.harnesses.store import Store
from openprogram.runtime.harnesses.codex import rate_limits as rl


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "_store", Store(tmp_path / "state" / "harnesses.db"))
    rl._last.clear()
    yield
    rl._last.clear()


def _quota():
    return oa.report(since=0, until=time.time() + 10**10)["quota"]


class _Server:
    def __init__(self, answer=None, error=None, home="/h/one"):
        self.answer, self.error, self.calls = answer, error, []
        self.env = {"CODEX_HOME": home}

    def request(self, method, params, timeout=None):
        self.calls.append((method, params, timeout))
        if self.error:
            raise self.error
        return self.answer


_SNAPSHOT = {
    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 4070908800},
    "secondary": {"usedPercent": 40.5, "windowDurationMins": 10080, "resetsAt": 4071513600},
    "credits": {"balance": "41.5", "unlimited": False, "hasCredits": True},
    "planType": "pro",
}


def test_a_protocol_snapshot_is_kept_in_the_stored_shape():
    assert rl.stored_form(_SNAPSHOT) == {
        "primary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": 4070908800},
        "secondary": {"used_percent": 40.5, "window_minutes": 10080, "resets_at": 4071513600},
        "credits": {"balance": "41.5", "unlimited": False},
        "plan_type": "pro",
    }
    assert rl.stored_form({"primary": {"windowDurationMins": 300}, "secondary": None}) == {}


def test_a_read_asks_the_app_server_and_stores_the_answer():
    server = _Server({"rateLimits": _SNAPSHOT})
    rl.read(server)
    assert server.calls == [("account/rateLimits/read", {}, rl.READ_TIMEOUT_SECONDS)]
    (q,) = _quota()
    assert q["agent"] == "codex" and q["plan"] == "pro" and q["credits"] == 41.5
    assert [w["used_percent"] for w in q["windows"]] == [12.0, 40.5]


def _update(**snapshot):
    return {"method": "account/rateLimits/updated", "params": {"rateLimits": snapshot}}


def test_a_sparse_update_keeps_the_windows_it_does_not_name():
    rl.read(_Server({"rateLimits": _SNAPSHOT}))
    taken = rl.listener("/h/one")(_update(
        primary={"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 4070908800},
        secondary=None,
    ))
    assert taken is False
    (q,) = _quota()
    assert [w["used_percent"] for w in q["windows"]] == [30.0, 40.5]
    assert q["plan"] == "pro"


def test_an_update_merges_only_into_its_own_codex_home():
    rl.read(_Server({"rateLimits": _SNAPSHOT}, home="/h/one"))
    rl.listener("/h/two")(_update(primary={"usedPercent": 70, "windowDurationMins": 300}))
    (q,) = _quota()
    # The other home's sign-in has no weekly window of its own to borrow.
    assert [w["used_percent"] for w in q["windows"]] == [70.0]


def test_another_limit_family_is_left_out():
    rl.read(_Server({"rateLimits": {**_SNAPSHOT, "limitId": "codex_bengalfox"}}))
    rl.listener("/h/one")(_update(limitId="codex_other", primary={"usedPercent": 99, "windowDurationMins": 300}))
    assert _quota() == []
    rl.read(_Server({"rateLimits": {**_SNAPSHOT, "limitId": "codex"}}))
    assert [w["used_percent"] for w in _quota()[0]["windows"]] == [12.0, 40.5]


def test_other_messages_pass_through_untouched():
    assert rl.listener("/h/one")({"method": "turn/completed", "params": {}}) is False
    assert _quota() == []


def test_the_newer_of_a_rollout_file_and_the_app_server_stays():
    oa.store().record_quota("codex", 100.0, {"primary": {"used_percent": 5.0, "window_minutes": 300}})
    oa.store().record_quota("codex", 50.0, {"primary": {"used_percent": 90.0, "window_minutes": 300}})
    assert [w["used_percent"] for w in _quota()[0]["windows"]] == [5.0]


def test_a_login_without_plan_limits_stores_nothing():
    rl.read(_Server(error=RuntimeError("not signed in with ChatGPT")))
    rl.read(_Server({"rateLimits": {"primary": None, "secondary": None}}))
    rl.read(_Server(None))
    assert _quota() == []


def test_a_rollout_reading_counts_only_the_plan_family():
    from openprogram.models.usage.harnesses.codex import _plan_family

    assert _plan_family(None) and _plan_family("codex") and _plan_family("Codex")
    assert not _plan_family("codex_bengalfox")

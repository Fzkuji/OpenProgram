"""Requests sent a provider on OpenProgram's own behalf are written down,
apart from usage (models/listing/probe_log.py)."""
from __future__ import annotations

import httpcore
import pytest

import openprogram.models.usage.ledger as ledger_module
from openprogram.host.security import safe_http
from openprogram.host.security.safe_http import ObservedRequest
from openprogram.models.listing import probe_log
from openprogram.models.usage.ledger import PROBE_EVENTS_KEPT_S, UsageLedger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    fresh = UsageLedger(tmp_path / "usage.db")
    monkeypatch.setattr(ledger_module, "default_ledger", fresh)
    yield fresh
    fresh.close()


def _seen(status=None, error=None):
    return ObservedRequest(
        consumer="webui.model_listing.configured",
        method="GET",
        url="https://api.example.test/v1/models",
        elapsed_ms=12,
        status=status,
        error=error,
    )


def _send(seen):
    observer = safe_http._request_observer.get()
    assert observer is not None
    observer(seen)


def test_a_probe_request_is_a_row_and_never_usage(ledger):
    with probe_log.probing("deepseek", probe_log.KIND_CONNECTIVITY):
        _send(_seen(status=401))

    rows = ledger.probe_events(since=0)
    assert rows == [{
        "ts": rows[0]["ts"], "provider": "deepseek", "kind": "connectivity",
        "method": "GET", "url": "https://api.example.test/v1/models",
        "status": 401, "elapsed_ms": 12, "error": None,
    }]
    assert [row.events for row in ledger.query(since=0)] == [0]


def test_an_answered_model_list_is_left_out_and_a_failed_one_kept(ledger):
    with probe_log.probing("deepseek", probe_log.KIND_MODEL_LIST):
        _send(_seen(status=200))
        _send(_seen(error="ConnectTimeout"))

    assert [(r["status"], r["error"]) for r in ledger.probe_events(since=0)] == [
        (None, "ConnectTimeout")
    ]


def test_the_outer_probe_names_a_nested_one(ledger):
    @probe_log.probe(probe_log.KIND_CREDENTIAL)
    def validate(provider_id):
        _send(_seen(status=200))

    with probe_log.probing("openai", probe_log.KIND_CONNECTIVITY):
        validate("openai")

    assert [r["kind"] for r in ledger.probe_events(since=0)] == ["connectivity"]


def test_nothing_is_written_outside_a_probe(ledger):
    assert safe_http._request_observer.get() is None
    assert ledger.probe_events(since=0) == []


def test_old_rows_go_as_new_ones_come(ledger):
    common = dict(provider="p", kind="credential", method="GET", url="u",
                  status=200, elapsed_ms=1, error=None)
    ledger.append_probe_event(**common, ts=1000.0)
    ledger.append_probe_event(**common, ts=1000.0 + PROBE_EVENTS_KEPT_S + 1)
    assert [r["ts"] for r in ledger.probe_events(since=0)] == [1000.0 + PROBE_EVENTS_KEPT_S + 1]


def test_the_transport_reports_origin_and_path_without_the_query(monkeypatch):
    from tests.component.security.test_safe_http_limits import _small_client

    client = _small_client(monkeypatch, httpcore.Response(
        200, headers=[(b"content-type", b"text/plain")], content=[b"ok"]))
    seen: list[ObservedRequest] = []
    with client, safe_http.observing_requests(seen.append):
        client.get("https://public.test/v1/models?key=secret")

    assert [(s.method, s.url, s.status, s.error) for s in seen] == [
        ("GET", "https://public.test/v1/models", 200, None)
    ]

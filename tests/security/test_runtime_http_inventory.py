from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.security import runtime_http_audit


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_http_inventory_has_no_unclassified_calls():
    result = runtime_http_audit.scan_runtime_http(ROOT / "openprogram")

    assert result.unregistered == ()
    assert result.active_unmanaged_transports == ()
    assert result.registry_without_consumer == ()
    assert result.stale_exclusions == ()


def test_scanner_fails_closed_for_representative_raw_network_calls(tmp_path):
    package = tmp_path / "runtime"
    package.mkdir()
    (package / "raw.py").write_text(
        """
import socket
import urllib.request as request
import requests
import httpx

request.urlopen("https://example.com")
requests.get("https://example.com")
httpx.Client()
s = socket.socket()
s.connect(("127.0.0.1", 80))
""",
        encoding="utf-8",
    )

    result = runtime_http_audit.scan_runtime_http(
        package,
        exclusions=(),
        registry={},
    )

    assert {issue.kind for issue in result.unregistered} == {
        "urllib.request.urlopen",
        "requests.get",
        "httpx.Client",
        "socket.connect",
    }


def test_scanner_reports_stale_boundary_exclusions(tmp_path):
    package = tmp_path / "runtime"
    package.mkdir()
    exclusion = runtime_http_audit.BoundaryExclusion(
        path="missing.py",
        boundary_owner="browser-control",
        reason="browser navigation is outside Runtime fetch policy",
    )

    result = runtime_http_audit.scan_runtime_http(
        package,
        exclusions=(exclusion,),
        registry={},
    )

    assert result.stale_exclusions == ("missing.py",)


def test_scanner_reports_exclusion_whose_declared_call_no_longer_exists(tmp_path):
    package = tmp_path / "runtime"
    package.mkdir()
    (package / "old.py").write_text("VALUE = 1\n", encoding="utf-8")
    exclusion = runtime_http_audit.BoundaryExclusion(
        path="old.py",
        boundary_owner="browser-control",
        reason="historical browser call",
        kinds=frozenset({"urllib.request.urlopen"}),
    )

    result = runtime_http_audit.scan_runtime_http(
        package,
        exclusions=(exclusion,),
        registry={},
    )

    assert result.stale_exclusions == ("old.py",)


def test_scanner_detects_supported_raw_libraries_and_known_sdk(tmp_path):
    package = tmp_path / "runtime"
    package.mkdir()
    (package / "raw.py").write_text(
        """
from urllib.request import urlopen as open_url
from requests import Session
from httpx import AsyncClient, AsyncHTTPTransport
import httpcore
import aiohttp
import urllib3
import socket
import openai

open_url("https://example.com")
Session()
AsyncClient()
AsyncHTTPTransport()
httpcore.AsyncConnectionPool()
httpcore.AsyncHTTPProxy("http://proxy.example")
aiohttp.ClientSession()
urllib3.PoolManager()
urllib3.ProxyManager("http://proxy.example")
socket.socket().connect(("127.0.0.1", 80))
openai.AsyncOpenAI()
""",
        encoding="utf-8",
    )

    result = runtime_http_audit.scan_runtime_http(
        package,
        exclusions=(),
        registry={},
    )

    assert {issue.kind for issue in result.unregistered} == {
        "urllib.request.urlopen",
        "requests.Session",
        "httpx.AsyncClient",
        "httpx.AsyncHTTPTransport",
        "httpcore.AsyncConnectionPool",
        "httpcore.AsyncHTTPProxy",
        "aiohttp.ClientSession",
        "urllib3.PoolManager",
        "urllib3.ProxyManager",
        "socket.connect",
        "sdk.openai.AsyncOpenAI",
    }
    assert result.active_unmanaged_transports == ("sdk.openai.AsyncOpenAI",)


def test_boundary_manifest_is_explicit_and_auditable():
    assert runtime_http_audit.BOUNDARY_MANIFEST
    for exclusion in runtime_http_audit.BOUNDARY_MANIFEST:
        assert exclusion.path
        assert exclusion.boundary_owner
        assert exclusion.reason


def test_registry_consumer_cannot_be_satisfied_by_docstring_only(tmp_path):
    package = tmp_path / "runtime"
    package.mkdir()
    (package / "claims.py").write_text(
        '"""tool.web_fetch"""\n',
        encoding="utf-8",
    )

    result = runtime_http_audit.scan_runtime_http(
        package,
        exclusions=(),
        registry={"tool.web_fetch": object()},
    )

    assert result.registry_without_consumer == ("tool.web_fetch",)


def test_shared_denial_ring_is_bounded_and_origin_only():
    runtime_http_audit.clear_runtime_http_audit()
    for index in range(runtime_http_audit.RUNTIME_HTTP_AUDIT_CAPACITY + 3):
        runtime_http_audit.record_runtime_http_denial(
            consumer="tool.web_fetch",
            reason=f"PRIVATE_ADDRESS_{index}",
            url="https://user:BEARER-TOKEN@example.com/private?token=QUERY-SECRET",
            delegated_to_policy_proxy=False,
        )

    events = runtime_http_audit.recent_runtime_http_denials()
    assert len(events) == runtime_http_audit.RUNTIME_HTTP_AUDIT_CAPACITY
    assert events[0].reason == "PRIVATE_ADDRESS_3"
    assert events[-1].safe_origin == "https://example.com"
    assert set(events[-1].__dict__) == {
        "consumer",
        "reason",
        "safe_origin",
        "delegated_to_policy_proxy",
        "timestamp",
    }
    assert "BEARER-TOKEN" not in repr(events)
    assert "QUERY-SECRET" not in repr(events)


def test_safe_http_policy_denial_is_forwarded_to_shared_ring():
    from openprogram.security.safe_http import OutboundSecurityConfig, safe_client
    from openprogram.security.url_policy import URLPolicyError

    runtime_http_audit.clear_runtime_http_audit()
    with safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(resolver=lambda _host, _port: ("127.0.0.1",)),
    ) as client:
        with pytest.raises(URLPolicyError, match="NON_GLOBAL_ADDRESS"):
            client.get("https://example.com/path?token=QUERY-SECRET")

    events = runtime_http_audit.recent_runtime_http_denials()
    assert len(events) == 1
    assert events[0].consumer == "tool.web_fetch"
    assert events[0].reason == "NON_GLOBAL_ADDRESS"
    assert events[0].safe_origin == "https://example.com"
    assert "QUERY-SECRET" not in repr(events)


def test_shared_audit_rejects_unbounded_or_peer_controlled_fields():
    runtime_http_audit.clear_runtime_http_audit()
    runtime_http_audit.record_runtime_http_denial(
        consumer="unknown/QUERY-SECRET",
        reason="PEER-BODY QUERY-SECRET",
        url="https://example.com/path?token=QUERY-SECRET",
        delegated_to_policy_proxy=False,
    )

    assert runtime_http_audit.recent_runtime_http_denials() == (
        runtime_http_audit.RuntimeHTTPAuditEvent(
            consumer="<unknown-consumer>",
            reason="INVALID_REASON",
            safe_origin="https://example.com",
            delegated_to_policy_proxy=False,
            timestamp=runtime_http_audit.recent_runtime_http_denials()[0].timestamp,
        ),
    )
    assert "QUERY-SECRET" not in repr(runtime_http_audit.recent_runtime_http_denials())

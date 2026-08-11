from __future__ import annotations

import asyncio
import gzip
import os
from collections import deque
from dataclasses import replace
from types import MappingProxyType

import httpcore
import httpx
import pytest

import openprogram.security.safe_http as safe_http
from openprogram.security.safe_http import (
    OutboundSecurityConfig,
    safe_async_client,
    safe_client,
)
from openprogram.security.url_policy import URLPolicyError


class _ScriptedPool:
    def __init__(self, response):
        self.response = response

    def handle_request(self, _request):
        if isinstance(self.response, Exception):
            raise self.response
        self.response.stream = _ClosableStream(self.response.stream)
        return self.response

    def close(self):
        pass


class _ClosableStream:
    def __init__(self, stream):
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self):
        pass


class _AsyncClosableStream:
    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    async def aclose(self):
        pass


class _AsyncScriptedPool:
    def __init__(self, response, chunks, error=None):
        self.response = response
        self.chunks = chunks
        self.error = error

    async def handle_async_request(self, _request):
        self.response.stream = _AsyncClosableStream(self.chunks, self.error)
        return self.response

    async def aclose(self):
        pass


def _small_client(monkeypatch, response, *, cap=8):
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["tool.web_fetch"] = replace(
        registry["tool.web_fetch"], max_decoded_body_bytes=cap
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    monkeypatch.setattr(
        client._transport, "_pool", lambda _decision: _ScriptedPool(response)
    )
    return client


def _small_async_client(monkeypatch, response, chunks, *, cap=8, error=None):
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["tool.web_fetch"] = replace(
        registry["tool.web_fetch"], max_decoded_body_bytes=cap
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    client = safe_async_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _AsyncScriptedPool(response, chunks, error),
    )
    return client


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ([(f"x-{index}".encode(), b"v") for index in range(101)], "TOO_MANY_HEADERS"),
        ([(b"x-large", b"a" * (65_537 - 11))], "HEADERS_TOO_LARGE"),
    ],
)
def test_response_header_limits_are_checked_before_body(monkeypatch, headers, reason):
    consumed = []

    def body():
        consumed.append(True)
        yield b"body"

    response = httpcore.Response(200, headers=headers, content=body())
    client = _small_client(monkeypatch, response)

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == reason
    assert not consumed


@pytest.mark.parametrize("content_length", [None, b"1"])
def test_absent_or_false_content_length_cannot_bypass_decoded_cap(
    monkeypatch, content_length
):
    headers = [(b"content-type", b"text/plain")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    response = httpcore.Response(200, headers=headers, content=[b"1234", b"56789"])
    client = _small_client(monkeypatch, response)

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "BODY_TOO_LARGE"


@pytest.mark.parametrize(
    ("encoding", "encoded"),
    [
        (b"gzip", gzip.compress(b"decoded-too-large")),
        (b"deflate", __import__("zlib").compress(b"decoded-too-large")),
    ],
)
def test_compressed_body_cap_counts_decoded_bytes(monkeypatch, encoding, encoded):
    response = httpcore.Response(
        200,
        headers=[
            (b"content-type", b"text/plain"),
            (b"content-encoding", encoding),
        ],
        content=encoded,
    )
    client = _small_client(monkeypatch, response)

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "BODY_TOO_LARGE"


def test_async_compressed_body_cap_counts_decoded_bytes(monkeypatch):
    encoded = gzip.compress(b"decoded-too-large")
    response = httpcore.Response(
        200,
        headers=[
            (b"content-type", b"text/plain"),
            (b"content-encoding", b"gzip"),
        ],
        content=_empty_async(),
    )
    client = _small_async_client(monkeypatch, response, [encoded])

    async def exercise():
        async with client:
            with pytest.raises(URLPolicyError) as exc:
                await client.get("https://public.test/resource")
        return exc.value

    assert asyncio.run(exercise()).reason == "BODY_TOO_LARGE"


def test_unknown_content_encoding_is_rejected_before_body(monkeypatch):
    response = httpcore.Response(
        200,
        headers=[
            (b"content-type", b"text/plain"),
            (b"content-encoding", b"br"),
        ],
        content=b"body",
    )
    client = _small_client(monkeypatch, response)

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "CONTENT_ENCODING_FORBIDDEN"


def test_request_forces_supported_accept_encoding(monkeypatch):
    captured = []

    class _CapturePool(_ScriptedPool):
        def handle_request(self, request):
            captured.extend(request.headers)
            return super().handle_request(request)

    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=b"ok",
    )
    client = _small_client(monkeypatch, response)
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _CapturePool(response),
    )

    with client:
        client.get("https://public.test/resource", headers={"Accept-Encoding": "br"})

    headers = {name.lower(): value for name, value in captured}
    assert headers[b"accept-encoding"] == b"gzip, deflate"


def test_mime_prefix_is_checked_without_parameters(monkeypatch):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"image/png; charset=binary")],
        content=b"png",
    )
    client = _small_client(monkeypatch, response)
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["tool.web_fetch"] = replace(
        registry["tool.web_fetch"], accepted_mime_prefixes=("text/",)
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "MIME_TYPE_FORBIDDEN"


def test_chunked_streaming_is_bounded(monkeypatch):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=[b"1234", b"56789"],
    )
    client = _small_client(monkeypatch, response)

    with client:
        with pytest.raises(URLPolicyError) as exc:
            with client.stream("GET", "https://public.test/resource") as result:
                list(result.iter_bytes())

    assert exc.value.reason == "BODY_TOO_LARGE"


def test_sync_raw_streaming_is_bounded(monkeypatch):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=[b"12345", b"67890"],
    )
    client = _small_client(monkeypatch, response, cap=4)

    with client:
        with client.stream("GET", "https://public.test/resource") as result:
            with pytest.raises(URLPolicyError) as exc:
                b"".join(result.iter_raw())
            assert result.is_closed

    assert exc.value.reason == "BODY_TOO_LARGE"


def test_async_raw_streaming_is_bounded(monkeypatch):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=_empty_async(),
    )
    client = _small_async_client(monkeypatch, response, [b"12345", b"67890"], cap=4)

    async def exercise():
        async with client:
            async with client.stream("GET", "https://public.test/resource") as result:
                with pytest.raises(URLPolicyError) as exc:
                    b"".join([chunk async for chunk in result.aiter_raw()])
                assert result.is_closed
        return exc.value

    assert asyncio.run(exercise()).reason == "BODY_TOO_LARGE"


def test_sync_raw_streaming_enforces_overall_deadline(monkeypatch):
    ticks = iter((0.0, 0.0, 121.0))
    monkeypatch.setattr(safe_http, "monotonic", lambda: next(ticks, 121.0))
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=b"ok",
    )
    client = _small_client(monkeypatch, response, cap=4)

    with client:
        with client.stream("GET", "https://public.test/resource") as result:
            with pytest.raises(URLPolicyError) as exc:
                b"".join(result.iter_raw())
            assert result.is_closed

    assert exc.value.reason == "OVERALL_TIMEOUT"


def test_async_raw_streaming_enforces_overall_deadline(monkeypatch):
    ticks = iter((0.0, 0.0, 121.0))
    monkeypatch.setattr(safe_http, "monotonic", lambda: next(ticks, 121.0))
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=_empty_async(),
    )
    client = _small_async_client(monkeypatch, response, [b"ok"], cap=4)

    async def exercise():
        async with client:
            async with client.stream("GET", "https://public.test/resource") as result:
                with pytest.raises(URLPolicyError) as exc:
                    b"".join([chunk async for chunk in result.aiter_raw()])
                assert result.is_closed
        return exc.value

    assert asyncio.run(exercise()).reason == "OVERALL_TIMEOUT"


@pytest.mark.parametrize(
    ("core_error", "expected"),
    [
        (httpcore.ConnectTimeout("connect"), httpx.ConnectTimeout),
        (httpcore.ReadTimeout("read"), httpx.ReadTimeout),
        (httpcore.WriteTimeout("write"), httpx.WriteTimeout),
        (httpcore.PoolTimeout("pool"), httpx.PoolTimeout),
    ],
)
def test_timeout_failures_keep_httpx_mapping(monkeypatch, core_error, expected):
    client = _small_client(monkeypatch, core_error)

    with client, pytest.raises(expected):
        client.get("https://public.test/resource")


def test_overall_timeout_is_enforced_during_body_read(monkeypatch):
    ticks = iter((0.0, 0.0, 121.0, 121.0))
    monkeypatch.setattr(safe_http, "monotonic", lambda: next(ticks, 121.0))
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=[b"ok"],
    )
    client = _small_client(monkeypatch, response)

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "OVERALL_TIMEOUT"


def test_failed_download_removes_temp_and_preserves_destination(monkeypatch, tmp_path):
    class _Interrupted:
        def __iter__(self):
            yield b"new"
            raise httpcore.ReadError("interrupted")

        def close(self):
            pass

    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=_Interrupted(),
    )
    client = _small_client(monkeypatch, response, cap=32)
    destination = tmp_path / "result.txt"
    destination.write_bytes(b"old")

    with client, pytest.raises(httpx.ReadError):
        client.download("https://public.test/resource", destination)

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [destination]


def test_successful_download_fsyncs_then_atomically_replaces(monkeypatch, tmp_path):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=b"new",
    )
    client = _small_client(monkeypatch, response, cap=32)
    destination = tmp_path / "result.txt"
    destination.write_bytes(b"old")
    replaced = []
    original_replace = os.replace

    def record_replace(source, target):
        replaced.append((source, target))
        original_replace(source, target)

    monkeypatch.setattr(safe_http.os, "replace", record_replace)

    with client:
        result = client.download("https://public.test/resource", destination)

    assert result == destination
    assert destination.read_bytes() == b"new"
    assert len(replaced) == 1
    assert list(tmp_path.iterdir()) == [destination]


def test_async_failed_download_removes_temp_and_preserves_destination(
    monkeypatch, tmp_path
):
    response = httpcore.Response(
        200,
        headers=[(b"content-type", b"text/plain")],
        content=_empty_async(),
    )
    client = _small_async_client(
        monkeypatch,
        response,
        [b"new"],
        cap=32,
        error=httpcore.ReadError("interrupted"),
    )
    destination = tmp_path / "result.txt"
    destination.write_bytes(b"old")

    async def exercise():
        async with client:
            with pytest.raises(httpx.ReadError):
                await client.download("https://public.test/resource", destination)

    asyncio.run(exercise())
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [destination]


async def _empty_async():
    if False:
        yield b""

"""_transport outbound pipeline: rate-limit backoff retry (Retry-After
first), the rendered hard-cap split, and file-send capability table.
"""
from __future__ import annotations

import pytest

from openprogram.channels import _transport
from openprogram.channels._transport import SendResult


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch):
    naps: list[float] = []
    monkeypatch.setattr(_transport.time, "sleep", lambda s: naps.append(s))
    return naps


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: tmp_path / "state")


def test_rate_limit_retries_until_success(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[str] = []

    def flaky(account_id, target, text):
        calls.append(text)
        if len(calls) < 3:
            return SendResult.fail("rate_limit", "429", retryable=True)
        return SendResult.success("m1")

    monkeypatch.setitem(_transport._POSTERS, "telegram", flaky)
    result = _transport.post_message("telegram", "a", "42", "hi")
    assert result.ok
    assert len(calls) == 3
    assert _no_sleep == [1.0, 3.0]          # fallback delays, no Retry-After


def test_retry_after_from_platform_wins(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[int] = []

    def flaky(account_id, target, text):
        calls.append(1)
        if len(calls) == 1:
            return SendResult.fail("rate_limit", "429", retryable=True,
                                   retry_after=7.5)
        return SendResult.success("m1")

    monkeypatch.setitem(_transport._POSTERS, "discord", flaky)
    assert _transport.post_message("discord", "a", "c1_u1", "hi").ok
    assert _no_sleep == [7.5]


def test_retry_sleep_capped(monkeypatch: pytest.MonkeyPatch, _no_sleep) -> None:
    def always_limited(account_id, target, text):
        return SendResult.fail("rate_limit", "429", retryable=True,
                               retry_after=999.0)

    monkeypatch.setitem(_transport._POSTERS, "slack", always_limited)
    result = _transport.post_message("slack", "a", "c_u", "hi")
    assert not result.ok
    assert result.error_kind == "rate_limit"
    assert all(s <= _transport._RETRY_SLEEP_CAP for s in _no_sleep)
    assert len(_no_sleep) == _transport._RETRY_ATTEMPTS - 1


def test_non_rate_limit_errors_do_not_retry(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[int] = []

    def auth_fail(account_id, target, text):
        calls.append(1)
        return SendResult.fail("auth", "token revoked")

    monkeypatch.setitem(_transport._POSTERS, "telegram", auth_fail)
    result = _transport.post_message("telegram", "a", "42", "hi")
    assert result.error_kind == "auth"
    assert len(calls) == 1
    assert _no_sleep == []


def test_extract_retry_after_sources() -> None:
    f = _transport._extract_retry_after
    assert f("", "12") == 12.0                                   # header
    assert f('{"retry_after": 3.2}') == 3.2                      # discord body
    assert f('{"parameters": {"retry_after": 44}}') == 44.0      # telegram body
    assert f("not json", "") == 0.0
    assert f('{"retry_after": "x"}', "bad") == 0.0


# ---------------------------------------------------------------------------
# render pipeline inside post_message
# ---------------------------------------------------------------------------

def test_post_message_renders_per_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setitem(
        _transport._POSTERS, "telegram",
        lambda a, t, text: sent.append(text) or SendResult.success("m"))
    _transport.post_message("telegram", "a", "42", "**bold** & more")
    assert sent == ["<b>bold</b> &amp; more"]


def test_rendered_chunk_over_hard_cap_is_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4000 raw '&' chars render to 20000 on telegram (&amp;) — the
    pipeline must split until every wire piece fits the 4096 hard cap."""
    sent: list[str] = []
    monkeypatch.setitem(
        _transport._POSTERS, "telegram",
        lambda a, t, text: sent.append(text) or SendResult.success("m"))
    _transport.post_message("telegram", "a", "42", "&" * 4000)
    assert len(sent) > 1
    assert all(len(p) <= 4096 for p in sent)
    assert "".join(sent) == "&amp;" * 4000


def test_patch_message_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[str] = []
    monkeypatch.setitem(
        _transport._PATCHERS, "slack",
        lambda a, t, mid, text: got.append(text) or SendResult.success(mid))
    result = _transport.patch_message("slack", "a", "C_U", "123", "**hi**")
    assert result.ok
    assert got == ["*hi*"]


# ---------------------------------------------------------------------------
# post_file capability table
# ---------------------------------------------------------------------------

def test_post_file_wechat_not_supported(tmp_path) -> None:
    f = tmp_path / "report.txt"
    f.write_text("x")
    result = _transport.post_file("wechat", "a", "peer", str(f))
    assert not result.ok
    assert result.error_kind == "not_supported"


def test_post_file_missing_file_is_bad_target() -> None:
    result = _transport.post_file("telegram", "a", "42", "/no/such/file.bin")
    assert result.error_kind == "bad_target"


def test_post_file_dispatches_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path, _no_sleep,
) -> None:
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG")
    calls: list = []

    def flaky(account_id, target, path, caption):
        calls.append((str(path), caption))
        if len(calls) == 1:
            return SendResult.fail("rate_limit", "429", retryable=True)
        return SendResult.success("m9")

    monkeypatch.setitem(_transport._FILE_POSTERS, "discord", flaky)
    result = _transport.post_file("discord", "a", "c_u", str(f), "here")
    assert result.ok and result.message_id == "m9"
    assert len(calls) == 2
    assert calls[0] == (str(f), "here")

"""_transport outbound pipeline: rate-limit backoff retry (Retry-After
first), the rendered hard-cap split, and file-send capability table.
"""
from __future__ import annotations

import httpx
import pytest

from openprogram.interfaces.channels import _transport
from openprogram.interfaces.channels._transport import SendResult


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch):
    naps: list[float] = []
    monkeypatch.setattr(_transport.time, "sleep", lambda s: naps.append(s))
    return naps


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir",
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


def test_connect_failure_is_resent_until_it_connects(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[str] = []

    def flaky(account_id, target, text):
        calls.append(text)
        if len(calls) < 4:
            return _transport._classify_network_error(httpx.ConnectError("eof"))
        return SendResult.success("")

    monkeypatch.setitem(_transport._POSTERS, "wechat", flaky)
    assert _transport.post_message("wechat", "a", "u", "hi").ok
    assert len(calls) == 4
    assert _no_sleep == [1.0, 2.0, 4.0]


def test_connect_failure_gives_up_as_network(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[int] = []

    def down(account_id, target, text):
        calls.append(1)
        return _transport._classify_network_error(httpx.ConnectTimeout("slow"))

    monkeypatch.setitem(_transport._POSTERS, "wechat", down)
    result = _transport.post_message("wechat", "a", "u", "hi")
    assert (result.ok, result.error_kind) == (False, "network")
    assert len(calls) == _transport._CONNECT_RETRY_ATTEMPTS


def test_failure_after_the_request_went_out_is_not_resent(
    monkeypatch: pytest.MonkeyPatch, _no_sleep,
) -> None:
    calls: list[int] = []

    def timed_out(account_id, target, text):
        calls.append(1)
        return _transport._classify_network_error(httpx.ReadTimeout("no answer"))

    monkeypatch.setitem(_transport._POSTERS, "wechat", timed_out)
    result = _transport.post_message("wechat", "a", "u", "hi")
    assert result.error_kind == "network"
    assert len(calls) == 1
    assert _no_sleep == []


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

def test_post_file_unknown_platform_not_supported(tmp_path) -> None:
    f = tmp_path / "report.txt"
    f.write_text("x")
    result = _transport.post_file("matrix", "a", "peer", str(f))
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


# ---------------------------------------------------------------------------
# code blocks across message boundaries
# ---------------------------------------------------------------------------

def _balanced(chunks: list[str]) -> bool:
    return all(c.count("```") % 2 == 0 for c in chunks)


def test_over_limit_code_block_reopens_its_fence() -> None:
    body = "\n".join(f"line {i}" for i in range(1000))
    chunks = _transport._chunk(f"intro\n```python\n{body}\n```\nafter", 1800)
    assert len(chunks) > 1
    assert all(len(c) <= 1800 for c in chunks)
    assert _balanced(chunks)
    assert all(c.startswith("```python\n") for c in chunks[1:-1])
    lines = [line for c in chunks for line in c.split("\n")]
    assert [line for line in lines if line.startswith("line ")] == body.split("\n")


def test_code_block_with_one_huge_line_still_finishes() -> None:
    text = "```py\n" + "x" * 9000 + "\n```"
    chunks = _transport._chunk(text, 1800)
    assert all(len(c) <= 1800 for c in chunks)
    assert _balanced(chunks)
    assert sum(c.count("x") for c in chunks) == 9000


def test_opening_fence_moves_with_its_body() -> None:
    chunks = _transport._chunk("intro\n```py\n" + "x" * 3000 + "\n```", 1800)
    assert chunks[0] == "intro"
    assert _balanced(chunks)


def test_endless_fence_line_terminates() -> None:
    chunks = _transport._chunk("```" + "a" * 9000, 1800)
    assert all(len(c) <= 1800 for c in chunks)
    assert sum(c.count("a") for c in chunks) == 9000


def test_blank_stretch_sends_no_empty_message() -> None:
    chunks = _transport._chunk("\n" * 5000 + "end", 1800)
    assert chunks == ["end"]


@pytest.mark.parametrize("length", range(1770, 1800))
def test_cut_before_the_closing_fence_leaves_no_empty_block(length: int) -> None:
    chunks = _transport._chunk(
        "```py\n" + "a" * length + "\n```\n" + "b" * 2000, 1800)
    assert not any(c.startswith("```py\n```") for c in chunks)
    assert _balanced(chunks)
    assert "".join(chunks).count("a") == length


def test_blank_stretch_inside_code_sends_no_empty_block() -> None:
    chunks = _transport._chunk("```\n" + "\n" * 5000 + "x\n```", 1800)
    assert chunks == ["```\nx\n```"]


def test_opening_fence_on_an_overlong_line_stays_whole() -> None:
    chunks = _transport._chunk("x" * 1785 + "```python\nbody\n```", 1800)
    assert chunks == ["x" * 1785, "```python\nbody\n```"]


def _long_visual_reply() -> str:
    drawing = "\n".join(f"<rect x='{i}'/>" for i in range(800))
    return f"Here is the chart.\n```visual\n{drawing}\n```\nDone."


def test_long_visual_sends_nothing_of_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setitem(
        _transport._POSTERS, "telegram",
        lambda a, t, text: sent.append(text) or SendResult.success("m"))
    _transport.post_message("telegram", "a", "42", _long_visual_reply())
    assert sent == ["Here is the chart.\nDone."]


def test_long_visual_alone_gets_one_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setitem(
        _transport._POSTERS, "telegram",
        lambda a, t, text: sent.append(text) or SendResult.success("m"))
    drawing = "\n".join(f"<rect x='{i}'/>" for i in range(800))
    _transport.post_message("telegram", "a", "42", f"```visual\n{drawing}\n```")
    assert sent == [_transport._format.VISUAL_ONLY_NOTE]


def test_long_visual_fits_one_placeholder_edit() -> None:
    assert _transport.split_for_edit("telegram", _long_visual_reply(), " more") == (
        "Here is the chart.\nDone.", "",
    )


def test_render_split_keeps_code_block_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setitem(
        _transport._POSTERS, "telegram",
        lambda a, t, text: sent.append(text) or SendResult.success("m"))
    body = "\n".join("&&&&&&&&" for _ in range(400))
    _transport.post_message("telegram", "a", "42", f"```\n{body}\n```")
    assert len(sent) > 1
    assert all(len(p) <= 4096 for p in sent)
    assert all(p.startswith("<pre>") and p.endswith("</pre>") for p in sent)


# ---------------------------------------------------------------------------
# wire payloads
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._payload = payload
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def _send(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    post = _send
    patch = _send


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    def install(replies: list) -> _FakeClient:
        client = _FakeClient(replies)
        monkeypatch.setattr(_transport, "safe_client", lambda *a, **k: client)
        monkeypatch.setattr(_transport._accounts, "load_credentials",
                            lambda *a, **k: {"bot_token": "t"})
        return client
    return install


def test_discord_payloads_allow_no_mentions(fake_http, tmp_path) -> None:
    client = fake_http([_FakeResponse(payload={"id": "1"}) for _ in range(3)])
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert _transport._post_discord("a", "c_u", "@everyone hi").ok
    assert _transport._patch_discord("a", "c_u", "1", "@everyone hi").ok
    assert _transport._post_file_discord("a", "c_u", f, "@everyone").ok
    assert client.calls[0][1]["json"]["allowed_mentions"] == {"parse": []}
    assert client.calls[1][1]["json"]["allowed_mentions"] == {"parse": []}
    payload = _transport.json.loads(client.calls[2][1]["data"]["payload_json"])
    assert payload["allowed_mentions"] == {"parse": []}


def _picture(tmp_path):
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG")
    return f


def test_refused_telegram_image_goes_as_document(fake_http, tmp_path) -> None:
    client = fake_http([
        _FakeResponse(400, {"ok": False,
                            "description": "Bad Request: PHOTO_INVALID_DIMENSIONS"}),
        _FakeResponse(payload={"ok": True, "result": {"message_id": 5}}),
    ])
    result = _transport._post_file_telegram("a", "42", _picture(tmp_path), "c")
    assert result.ok and result.message_id == "5"
    assert [c[0].rsplit("/", 1)[1] for c in client.calls] == [
        "sendPhoto", "sendDocument",
    ]
    assert "document" in client.calls[1][1]["files"]


def test_telegram_image_timeout_sends_no_duplicate(fake_http, tmp_path) -> None:
    client = fake_http([TimeoutError("slow")])
    result = _transport._post_file_telegram("a", "42", _picture(tmp_path), "c")
    assert result.error_kind == "network"
    assert len(client.calls) == 1


def test_telegram_unreadable_success_sends_no_duplicate(fake_http, tmp_path) -> None:
    client = fake_http([_FakeResponse(200, None, "<html>")])
    result = _transport._post_file_telegram("a", "42", _picture(tmp_path), "c")
    assert result.error_kind == "network"
    assert len(client.calls) == 1


@pytest.mark.parametrize("status, description, kind", [
    (401, "Unauthorized", "auth"),
    (429, "Too Many Requests: retry after 1", "rate_limit"),
    (400, "Bad Request: chat not found", "bad_target"),
])
def test_telegram_image_failure_for_other_reasons_is_not_resent_as_document(
    fake_http, tmp_path, status: int, description: str, kind: str,
) -> None:
    client = fake_http([_FakeResponse(status, {"ok": False,
                                               "description": description})])
    result = _transport._post_file_telegram("a", "42", _picture(tmp_path), "c")
    assert result.error_kind == kind
    assert len(client.calls) == 1


def test_telegram_error_keeps_the_reason(fake_http) -> None:
    fake_http([_FakeResponse(400, {"ok": False,
                                   "description": "Bad Request:\nmessage thread not found"})])
    result = _transport._post_telegram("a", "42", "hi")
    assert result.error_kind == "unknown"
    assert result.error_detail == "Bad Request: message thread not found"

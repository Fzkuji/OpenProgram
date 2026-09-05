"""Inbound attachment pipeline (_attachments.py) and the base wiring:
download to the account state dir, small images become TurnRequest
image blocks, every file becomes an [attachment: ...] note.
"""
from __future__ import annotations

import base64
import contextlib
import threading
from pathlib import Path

import pytest

from openprogram.attachments import format_marker
from openprogram.channels import _attachments
from openprogram.channels._message import Attachment, ChannelMessage
from openprogram.channels._transport import SendResult
from openprogram.channels.base import Channel
from openprogram.security.url_policy import URLPolicyError


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)


class _FakeResponse:
    def __init__(self, content: bytes, *, ok: bool = True,
                 content_type: str = "image/png") -> None:
        self._content = content
        self.ok = ok
        self.is_success = ok
        self.status_code = 200 if ok else 500
        self.headers = {"Content-Type": content_type}
        self.content = content

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def iter_bytes(self):
        yield self._content


class _FakeSafeClient:
    def __init__(self, response, seen: dict | None = None) -> None:
        self.response = response
        self.seen = seen if seen is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, headers=None, timeout=60, **kwargs):
        self.seen.update(url=url, headers=headers, timeout=timeout, **kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @contextlib.contextmanager
    def stream(self, _method, url, headers=None, timeout=60):
        self.seen.update(url=url, headers=headers, timeout=timeout)
        if isinstance(self.response, Exception):
            raise self.response
        yield self.response


def test_download_inbound_saves_to_account_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    consumers: list[str] = []
    monkeypatch.setattr(
        _attachments,
        "safe_client",
        lambda consumer: consumers.append(consumer)
        or _FakeSafeClient(_FakeResponse(b"PNGDATA"), seen),
    )
    saved = _attachments.download_inbound(
        "discord", "a1",
        [Attachment(name="pic.png", mime="image/png",
                    url="https://cdn/pic.png",
                    headers=(("Authorization", "Bearer t"),))],
    )
    assert len(saved) == 1
    row = saved[0]
    p = Path(row["path"])
    assert p.is_file() and p.read_bytes() == b"PNGDATA"
    assert p.parts[-6:-1] == (
        "channels", "discord", "accounts", "a1", "attachments",
    )
    assert row["mime"] == "image/png"
    assert row["size"] == 7
    assert seen["headers"] == {"Authorization": "Bearer t"}
    assert consumers == ["channel.attachment.download"]


def test_download_skips_oversize_declared_and_streamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_attachments, "MAX_DOWNLOAD_BYTES", 10)
    calls = 0

    def fake_client(_consumer):
        nonlocal calls
        calls += 1
        return _FakeSafeClient(
            URLPolicyError("BODY_TOO_LARGE", "https://cdn")
        )

    monkeypatch.setattr(_attachments, "safe_client", fake_client)
    # declared size over cap → no request at all
    saved = _attachments.download_inbound(
        "discord", "a1",
        [Attachment(name="big.bin", url="https://cdn/big", size=999)])
    assert saved == []
    assert calls == 0
    # undeclared size, stream exceeds cap → aborted + partial removed
    saved = _attachments.download_inbound(
        "discord", "a1",
        [Attachment(name="sneaky.bin", url="https://cdn/sneaky")])
    assert saved == []
    assert calls == 1
    att_dir = _attachments.attachments_dir("discord", "a1")
    assert list(att_dir.iterdir()) == []


def test_telegram_file_id_resolved_via_getfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.channels import accounts as _accounts
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})

    class _GetFileResp:
        is_success = True

        def json(self):
            return {"ok": True, "result": {"file_path": "photos/f_1.jpg"}}

    consumers: list[str] = []

    def managed_client(consumer):
        consumers.append(consumer)
        if consumer == "channel.telegram.api":
            return _FakeSafeClient(_GetFileResp())
        return _FakeSafeClient(_FakeResponse(b"JPG", content_type="image/jpeg"))

    monkeypatch.setattr(
        _attachments,
        "safe_client",
        managed_client,
    )
    saved = _attachments.download_inbound(
        "telegram", "a1",
        [Attachment(name="photo.jpg", mime="image/jpeg", file_id="F123")])
    assert len(saved) == 1
    assert consumers == ["channel.telegram.api", "channel.telegram.attachment"]


def test_to_turn_attachments_only_small_images(tmp_path) -> None:
    img = tmp_path / "a.png"
    img.write_bytes(b"IMGDATA")
    doc = tmp_path / "b.pdf"
    doc.write_bytes(b"%PDF")
    saved = [
        {"path": str(img), "name": "a.png", "mime": "image/png", "size": 7},
        {"path": str(doc), "name": "b.pdf", "mime": "application/pdf", "size": 4},
    ]
    turn = _attachments.to_turn_attachments(saved)
    assert len(turn) == 1
    assert turn[0]["type"] == "image"
    assert turn[0]["media_type"] == "image/png"
    assert base64.b64decode(turn[0]["data"]) == b"IMGDATA"

    notes = _attachments.attachment_notes(saved)
    assert len(notes) == 2
    assert notes[0] == format_marker("a.png", img, 7, mime="image/png")
    assert notes[1] == format_marker("b.pdf", doc, 4, mime="application/pdf")


def test_oversize_image_stays_file_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_attachments, "IMAGE_INLINE_BYTES", 4)
    img = tmp_path / "big.png"
    img.write_bytes(b"12345678")
    saved = [{"path": str(img), "name": "big.png",
              "mime": "image/png", "size": 8}]
    assert _attachments.to_turn_attachments(saved) == []
    assert len(_attachments.attachment_notes(saved)) == 1


# ---------------------------------------------------------------------------
# base wiring: attachments flow into dispatch (notes + image blocks)
# ---------------------------------------------------------------------------

class _AttChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target: str, text: str) -> SendResult:
        return SendResult.success("m")


def test_base_passes_attachments_into_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    from openprogram.channels import _access
    _access.approve_user("faketg", "acct1", "7")

    img = tmp_path / "x.png"
    img.write_bytes(b"IMG")
    monkeypatch.setattr(
        "openprogram.channels._attachments.download_inbound",
        lambda ch, acct, atts: [{"path": str(img), "name": "x.png",
                                 "mime": "image/png", "size": 3}])
    seen: dict = {}
    monkeypatch.setattr(
        "openprogram.channels._conversation.dispatch_inbound",
        lambda **kw: seen.update(kw) or None)

    ch = _AttChannel()
    ch._dispatch_and_reply(ChannelMessage(
        text="look",
        chat_id="42", user_id="7", user_display="Bob",
        attachments=(Attachment(name="x.png", url="https://u"),),
    ))
    assert seen["attachments"] and seen["attachments"][0]["type"] == "image"
    assert format_marker("x.png", img, 3, mime="image/png") in seen["user_text"]
    assert seen["user_text"].startswith("[Bob (7)] look\n\n")

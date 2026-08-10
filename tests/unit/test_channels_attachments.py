"""Inbound attachment pipeline (_attachments.py) and the base wiring:
download to the account state dir, small images become TurnRequest
image blocks, every file becomes an [attachment: ...] note.
"""
from __future__ import annotations

import base64
import threading
from pathlib import Path

import pytest

from openprogram.channels import _attachments
from openprogram.channels._message import Attachment, ChannelMessage
from openprogram.channels._transport import SendResult
from openprogram.channels.base import Channel


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: tmp_path / "state")


class _FakeResponse:
    def __init__(self, content: bytes, *, ok: bool = True,
                 content_type: str = "image/png") -> None:
        self._content = content
        self.ok = ok
        self.status_code = 200 if ok else 500
        self.headers = {"Content-Type": content_type}

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


def test_download_inbound_saves_to_account_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def fake_get(url, headers=None, stream=True, timeout=60):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(b"PNGDATA")

    monkeypatch.setattr(_attachments.requests, "get", fake_get)
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
    assert "channels/discord/accounts/a1/attachments" in str(p)
    assert row["mime"] == "image/png"
    assert row["size"] == 7
    assert seen["headers"] == {"Authorization": "Bearer t"}


def test_download_skips_oversize_declared_and_streamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_attachments, "MAX_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(
        _attachments.requests, "get",
        lambda *a, **k: _FakeResponse(b"x" * 50))
    # declared size over cap → no request at all
    saved = _attachments.download_inbound(
        "discord", "a1",
        [Attachment(name="big.bin", url="https://cdn/big", size=999)])
    assert saved == []
    # undeclared size, stream exceeds cap → aborted + partial removed
    saved = _attachments.download_inbound(
        "discord", "a1",
        [Attachment(name="sneaky.bin", url="https://cdn/sneaky")])
    assert saved == []
    att_dir = _attachments.attachments_dir("discord", "a1")
    assert list(att_dir.iterdir()) == []


def test_telegram_file_id_resolved_via_getfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.channels import accounts as _accounts
    _accounts.save_credentials("telegram", "a1", {"bot_token": "TOK"})

    class _GetFileResp:
        ok = True
        def json(self):
            return {"ok": True, "result": {"file_path": "photos/f_1.jpg"}}

    calls: list[str] = []

    def fake_get(url, params=None, headers=None, stream=False, timeout=0):
        calls.append(url)
        if "getFile" in url:
            return _GetFileResp()
        return _FakeResponse(b"JPG", content_type="image/jpeg")

    monkeypatch.setattr(_attachments.requests, "get", fake_get)
    saved = _attachments.download_inbound(
        "telegram", "a1",
        [Attachment(name="photo.jpg", mime="image/jpeg", file_id="F123")])
    assert len(saved) == 1
    assert "api.telegram.org/botTOK/getFile" in calls[0]
    assert "api.telegram.org/file/botTOK/photos/f_1.jpg" in calls[1]


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
    assert notes[0] == f"[attachment: a.png (png, 1 KB) @ {img}]"
    assert notes[1] == f"[attachment: b.pdf (pdf, 1 KB) @ {doc}]"


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
    assert f"[attachment: x.png (png, 1 KB) @ {img}]" in seen["user_text"]
    assert seen["user_text"].startswith("[Bob (7)] look\n\n")

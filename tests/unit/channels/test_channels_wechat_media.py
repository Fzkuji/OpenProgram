"""WeChat (iLink) media: item parsing, CDN decryption on download, and
the encrypted upload behind ``post_file``.
"""
from __future__ import annotations

import base64
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _attachments, _transport
from openprogram.interfaces.channels import _wechat_media as media
from openprogram.interfaces.channels._message import Attachment
from openprogram.interfaces.channels._transport import SendResult

KEY = bytes(range(16))
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


def _hex_b64(key: bytes) -> str:
    return base64.b64encode(key.hex().encode()).decode()


def _decrypt(data: bytes, key: bytes) -> bytes:
    d = media.Decryptor(key)
    return d.update(data) + d.finish()


# ---------------------------------------------------------------------------
# Keys and items
# ---------------------------------------------------------------------------

def test_both_key_encodings_decode_to_the_raw_key() -> None:
    assert media.parse_aes_key(base64.b64encode(KEY).decode()) == KEY
    assert media.parse_aes_key(_hex_b64(KEY)) == KEY
    assert media.parse_aes_key(base64.b64encode(b"short").decode()) is None


def test_encrypt_round_trips_with_pkcs7_padding() -> None:
    for size in (0, 15, 16, 17, 1000):
        data = bytes(size)
        cipher = media.encrypt(data, KEY)
        assert len(cipher) == media.padded_size(size)
        assert _decrypt(cipher, KEY) == data


def test_items_become_text_and_attachments() -> None:
    text, atts = media.parse_items([
        {"type": 1, "text_item": {"text": " look "}},
        {"type": 2, "image_item": {
            "aeskey": KEY.hex(), "mid_size": 64,
            "media": {"encrypt_query_param": "p/1+="}}},
        {"type": 4, "file_item": {
            "file_name": "report.pdf", "len": "2048",
            "media": {"encrypt_query_param": "p2", "aes_key": _hex_b64(KEY)}}},
        {"type": 5, "video_item": {
            "video_size": 4096,
            "media": {"full_url": "https://novac2c.cdn.weixin.qq.com/c2c/x",
                      "aes_key": _hex_b64(KEY)}}},
    ])
    assert text == "look"
    image, doc, video = atts
    assert image.url == f"{media.CDN_BASE}/download?encrypted_query_param=p%2F1%2B%3D"
    assert image.aes_key == KEY.hex() and image.mime == "" and image.size == 64
    assert (doc.name, doc.mime, doc.size) == ("report.pdf", "application/pdf", 2048)
    assert doc.aes_key == KEY.hex()
    assert video.url.endswith("/c2c/x") and video.mime == "video/mp4"


def test_voice_uses_wechat_transcript_and_keeps_untranscribed_audio() -> None:
    voice = {"media": {"encrypt_query_param": "v", "aes_key": _hex_b64(KEY)}}
    text, atts = media.parse_items([
        {"type": 3, "voice_item": {**voice, "text": "明天开会"}},
    ])
    assert (text, atts) == ("明天开会", ())

    text, atts = media.parse_items([{"type": 3, "voice_item": voice}])
    assert text == ""
    assert [(a.name, a.mime) for a in atts] == [("voice.silk", "audio/silk")]


def test_media_without_a_usable_key_is_dropped() -> None:
    _text, atts = media.parse_items([
        {"type": 4, "file_item": {"media": {"encrypt_query_param": "p"}}},
        {"type": 4, "file_item": {"media": {"encrypt_query_param": "p",
                                            "aes_key": "bm9wZQ=="}}},
        {"type": 2, "image_item": {"media": {}}},
    ])
    assert atts == ()
    # An image with no key is served in the clear.
    _text, atts = media.parse_items([
        {"type": 2, "image_item": {"media": {"encrypt_query_param": "p"}}},
    ])
    assert atts[0].aes_key == ""


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, body: bytes) -> None:
        self.is_success = True
        self.status_code = 200
        self.headers = {"Content-Type": "application/octet-stream"}
        self._body = body

    def iter_bytes(self):
        # Odd chunk sizes: decryption must not depend on block alignment.
        for i in range(0, len(self._body), 7):
            yield self._body[i:i + 7]


class _Client:
    def __init__(self, response, log: list) -> None:
        self.response = response
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @contextlib.contextmanager
    def stream(self, method, url, headers=None, timeout=60):
        self.log.append((method, url))
        yield self.response

    def post(self, url, content=b"", headers=None, timeout=60):
        self.log.append(("POST", url, content))
        return self.response


def test_cdn_media_is_decrypted_and_typed_on_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumers: list[str] = []
    log: list = []
    monkeypatch.setattr(
        _attachments, "safe_client",
        lambda consumer: consumers.append(consumer)
        or _Client(_Response(media.encrypt(PNG, KEY)), log),
    )
    saved = _attachments.download_inbound("wechat", "a1", [
        Attachment(url=f"{media.CDN_BASE}/download?encrypted_query_param=p",
                   aes_key=KEY.hex()),
    ])
    assert consumers == ["channel.wechat.cdn"]
    (row,) = saved
    assert Path(row["path"]).read_bytes() == PNG
    assert row["mime"] == "image/png" and row["path"].endswith(".png")
    assert row["size"] == len(PNG)
    assert _attachments.to_turn_attachments(saved)[0]["media_type"] == "image/png"


def test_a_wrong_key_saves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _attachments, "safe_client",
        lambda _c: _Client(_Response(media.encrypt(PNG, KEY)), []),
    )
    saved = _attachments.download_inbound("wechat", "a1", [
        Attachment(name="x.png", url=f"{media.CDN_BASE}/download",
                   aes_key=bytes(16).hex()),
    ])
    assert saved == []
    assert list(_attachments.attachments_dir("wechat", "a1").iterdir()) == []


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class _UploadResponse:
    status_code = 200
    headers = {"x-encrypted-param": "down-param"}


@pytest.fixture
def upload(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {"api": [], "cdn": [], "items": [], "texts": []}

    def fake_api(account_id, endpoint, body):
        calls["api"].append((endpoint, body))
        return {"ret": 0, "upload_param": "up/param"}

    monkeypatch.setattr(_transport, "_wechat_post", fake_api)
    monkeypatch.setattr(
        _transport, "safe_client",
        lambda consumer: calls.setdefault("consumer", consumer)
        and _Client(_UploadResponse(), calls["cdn"]),
    )
    monkeypatch.setattr(
        _transport, "_send_wechat_item",
        lambda a, u, item: calls["items"].append(item) or SendResult.success(""),
    )
    monkeypatch.setattr(
        _transport, "_post_wechat",
        lambda a, u, text: calls["texts"].append(text) or SendResult.success(""),
    )
    return calls


def test_an_image_is_encrypted_uploaded_and_sent(upload, tmp_path) -> None:
    f = tmp_path / "chart.png"
    f.write_bytes(PNG)
    result = _transport.post_file("wechat", "a1", "u@im.wechat", str(f), "图表")
    assert result.ok

    ((endpoint, body),) = upload["api"]
    assert endpoint == "getuploadurl"
    assert body["media_type"] == media.UPLOAD_IMAGE
    assert body["to_user_id"] == "u@im.wechat"
    assert body["rawsize"] == len(PNG)
    key = bytes.fromhex(body["aeskey"])

    assert upload["consumer"] == "channel.wechat.cdn"
    ((_method, url, content),) = upload["cdn"]
    assert url == (f"{media.CDN_BASE}/upload?encrypted_query_param=up%2Fparam"
                   f"&filekey={body['filekey']}")
    assert len(content) == body["filesize"]
    assert _decrypt(content, key) == PNG

    (item,) = upload["items"]
    assert item["type"] == media.ITEM_IMAGE
    sent = item["image_item"]["media"]
    assert sent["encrypt_query_param"] == "down-param"
    assert media.parse_aes_key(sent["aes_key"]) == key
    assert upload["texts"] == ["图表"]


def test_other_files_go_out_as_file_items(upload, tmp_path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert _transport.post_file("wechat", "a1", "u", str(f)).ok
    assert upload["api"][0][1]["media_type"] == media.UPLOAD_FILE
    (item,) = upload["items"]
    assert item["file_item"]["file_name"] == "notes.txt"
    assert item["file_item"]["len"] == "5"
    assert upload["texts"] == []


def test_upload_without_download_param_fails(
    upload, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_UploadResponse, "headers", {})
    f = tmp_path / "a.png"
    f.write_bytes(PNG)
    result = _transport.post_file("wechat", "a1", "u", str(f), "cap")
    assert not result.ok
    assert upload["items"] == [] and upload["texts"] == []


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

def test_adapter_passes_media_only_messages_on() -> None:
    from openprogram.interfaces.channels.implementations.wechat import WechatChannel

    got: list = []
    fake = SimpleNamespace(account_id="a1", handle_inbound=got.append)
    WechatChannel._handle_message(fake, {
        "message_type": 1, "from_user_id": "u@im.wechat", "message_id": 7,
        "item_list": [{"type": 2, "image_item": {
            "aeskey": KEY.hex(), "media": {"encrypt_query_param": "p"}}}],
    })
    (msg,) = got
    assert msg.text == "" and len(msg.attachments) == 1

    WechatChannel._handle_message(fake, {
        "message_type": 1, "from_user_id": "u@im.wechat",
        "item_list": [{"type": 4, "file_item": {"media": {}}}],
    })
    assert len(got) == 1

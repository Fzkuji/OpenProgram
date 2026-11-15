"""WeChat (iLink) 媒体 — 图片、语音、文件、视频的条目解析和 CDN 加解密.

iLink 的媒体不在消息里, 在腾讯的 C2C CDN 上, 用 AES-128-ECB (PKCS7)
加密. 一个媒体条目带两样东西:

* 下载地址: ``media.encrypt_query_param`` 拼到
  ``{CDN_BASE}/download?encrypted_query_param=…``, 或服务端直接给的
  ``media.full_url``.
* 密钥: 图片优先用 ``image_item.aeskey`` (16 字节的 hex), 否则是
  ``media.aes_key`` — base64 编码, 解出来要么是 16 字节原始密钥 (图片),
  要么是 32 个 hex 字符 (语音、文件、视频).

语音条目常带 ``voice_item.text``, 是微信自己的转写, 直接当消息文本.
没有转写的语音是 SILK 编码, 原样落盘.

出站反过来: ``getuploadurl`` 拿上传参数 → 随机密钥加密 → POST 到 CDN,
响应头 ``x-encrypted-param`` 就是对方下载用的参数 → ``sendmessage`` 发
一个对应类型的条目. 上传在 :mod:`._transport`, 这里只放协议常量和
纯函数.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
import re
from urllib.parse import quote

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from openprogram.interfaces.channels._message import Attachment


CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"

# 消息条目类型 (item_list[].type)
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

# getuploadurl 的 media_type
UPLOAD_IMAGE = 1
UPLOAD_VIDEO = 2
UPLOAD_FILE = 3

_HEX_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


# ---------------------------------------------------------------------------
# AES-128-ECB
# ---------------------------------------------------------------------------

def parse_aes_key(value: str) -> bytes | None:
    """``media.aes_key`` → 16 字节密钥; 两种编码都认, 认不出返回 None."""
    try:
        raw = base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(raw) == 16:
        return raw
    text = raw.decode("ascii", "replace")
    if len(raw) == 32 and _HEX_KEY.match(text):
        return bytes.fromhex(text)
    return None


def encrypt(data: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padder.update(data) + padder.finalize()) + encryptor.finalize()


def padded_size(size: int) -> int:
    """PKCS7 补齐后的密文长度 (正好整块时也多一整块)."""
    return (size // 16 + 1) * 16


class Decryptor:
    """边下载边解密: ``update`` 喂密文块, ``finish`` 去掉 PKCS7 填充."""

    def __init__(self, key: bytes) -> None:
        self._cipher = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        self._unpadder = padding.PKCS7(128).unpadder()

    def update(self, chunk: bytes) -> bytes:
        return self._unpadder.update(self._cipher.update(chunk))

    def finish(self) -> bytes:
        return self._unpadder.update(self._cipher.finalize()) + self._unpadder.finalize()


def media_field(key: bytes) -> str:
    """出站条目里的 ``aes_key``: base64(hex 字符串), 跟入站文件类一致."""
    return base64.b64encode(key.hex().encode("ascii")).decode("ascii")


def download_url(param: str) -> str:
    return f"{CDN_BASE}/download?encrypted_query_param={quote(param, safe='')}"


def upload_url(param: str, filekey: str) -> str:
    return (f"{CDN_BASE}/upload?encrypted_query_param={quote(param, safe='')}"
            f"&filekey={quote(filekey, safe='')}")


# ---------------------------------------------------------------------------
# 入站条目 → 文本 + 附件
# ---------------------------------------------------------------------------

def parse_items(items: list[dict]) -> tuple[str, tuple[Attachment, ...]]:
    """一条 iLink 消息的条目 → (文本, 附件).

    文本取第一个文本条目; 没有的话取语音的微信转写. 带转写的语音不再
    下载 (SILK 没法直接给模型), 没转写的语音落盘为 ``voice.silk``.
    """
    text = ""
    voice_text = ""
    attachments: list[Attachment] = []
    for item in items or ():
        kind = item.get("type")
        if kind == ITEM_TEXT:
            text = text or str((item.get("text_item") or {}).get("text") or "")
            continue
        if kind == ITEM_VOICE:
            voice = item.get("voice_item") or {}
            transcript = str(voice.get("text") or "").strip()
            if transcript:
                voice_text = voice_text or transcript
                continue
        att = _attachment(kind, item)
        if att is not None:
            attachments.append(att)
    return (text.strip() or voice_text), tuple(attachments)


def _attachment(kind: object, item: dict) -> Attachment | None:
    field = {
        ITEM_IMAGE: "image_item", ITEM_VOICE: "voice_item",
        ITEM_FILE: "file_item", ITEM_VIDEO: "video_item",
    }.get(kind)  # type: ignore[arg-type]
    if field is None:
        return None
    body = item.get(field) or {}
    media = body.get("media") or {}
    param = str(media.get("encrypt_query_param") or "")
    url = download_url(param) if param else str(media.get("full_url") or "")
    if not url:
        return None

    key: bytes | None = None
    if kind == ITEM_IMAGE and _HEX_KEY.match(str(body.get("aeskey") or "")):
        key = bytes.fromhex(body["aeskey"])
    elif media.get("aes_key"):
        key = parse_aes_key(str(media["aes_key"]))
        if key is None:
            return None
    elif kind != ITEM_IMAGE:
        # 只有图片见过明文; 其他类型没密钥就解不出来.
        return None

    if kind == ITEM_IMAGE:
        name, mime, size = "", "", _int(body.get("mid_size"))
    elif kind == ITEM_VOICE:
        name, mime, size = "voice.silk", "audio/silk", 0
    elif kind == ITEM_VIDEO:
        name, mime, size = "video.mp4", "video/mp4", _int(body.get("video_size"))
    else:
        name = str(body.get("file_name") or "")
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        size = _int(body.get("len"))
    return Attachment(
        name=name, mime=mime, url=url, size=size,
        aes_key=key.hex() if key else "",
    )


def _int(value: object) -> int:
    try:
        return max(0, int(str(value or 0)))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# 出站: 文件 → 上传类型和条目
# ---------------------------------------------------------------------------

def upload_kind(mime: str) -> tuple[int, int]:
    """MIME → (getuploadurl media_type, 消息条目 type)."""
    if mime.startswith("image/"):
        return UPLOAD_IMAGE, ITEM_IMAGE
    if mime.startswith("video/"):
        return UPLOAD_VIDEO, ITEM_VIDEO
    return UPLOAD_FILE, ITEM_FILE


def media_item(item_type: int, *, download_param: str, key: bytes,
               name: str, raw_size: int, cipher_size: int) -> dict:
    """上传完成后 ``sendmessage`` 用的条目."""
    media = {
        "encrypt_query_param": download_param,
        "aes_key": media_field(key),
        "encrypt_type": 1,
    }
    if item_type == ITEM_IMAGE:
        return {"type": ITEM_IMAGE,
                "image_item": {"media": media, "mid_size": cipher_size}}
    if item_type == ITEM_VIDEO:
        return {"type": ITEM_VIDEO,
                "video_item": {"media": media, "video_size": cipher_size}}
    return {"type": ITEM_FILE,
            "file_item": {"media": media, "file_name": name,
                          "len": str(raw_size)}}


# ---------------------------------------------------------------------------
# 解密后的内容 → 图片 MIME (CDN 只回 octet-stream)
# ---------------------------------------------------------------------------

def sniff_image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""

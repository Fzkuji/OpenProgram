"""Pure helpers, tested on any host: PNG encoding, key chords, CF_HTML and the Start menu's app list."""
from __future__ import annotations

import json
import struct
import zlib

from ..common import keys as keymap
from ..common.base import BadArgument
from .win32 import VK_MODIFIERS, VK_NAMED


def png_encode(width: int, height: int, bgra: bytes) -> bytes:
    """A PNG of top-down 32-bit BGRA pixels, as GDI returns them."""
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        line = bgra[row * stride:(row + 1) * stride]
        raw.append(0)  # filter: none
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        raw += rgb

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


def key_chord(key: str) -> tuple[list[int], int, str]:
    """``(modifier vks, key vk, text)`` for an xdotool chord; ``text`` is a single printable character."""
    try:
        keymap.parse_chord(key)
    except keymap.KeyNameError as exc:
        if "modifier" in str(exc) or "malformed" in str(exc) or "non-empty" in str(exc):
            raise BadArgument(str(exc)) from exc
    raw = key.strip()
    parts = raw.split("+")
    if raw == "+" or raw.endswith("++"):
        parts = raw[:-1].split("+")[:-1] + ["+"]
    mods = []
    for name in parts[:-1]:
        vk = VK_MODIFIERS.get(name.strip().lower())
        if vk is None:
            raise BadArgument(f"unknown modifier {name!r} in {key!r}")
        mods.append(vk)
    last = parts[-1].strip() if parts[-1].strip() else parts[-1]
    lower = last.lower()
    if lower in VK_MODIFIERS and len(parts) == 1:
        return [], VK_MODIFIERS[lower], ""
    if lower in VK_NAMED:
        return mods, VK_NAMED[lower], ""
    if len(last) == 1:
        if last.isalpha():
            return mods, ord(last.upper()), last
        if last.isdigit():
            return mods, ord(last), last
        return mods, 0, last  # the layout decides the key (VkKeyScanW)
    shifted = {"plus": "+", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
               "backslash": "\\", "semicolon": ";", "apostrophe": "'", "grave": "`", "bracketleft": "[",
               "bracketright": "]", "exclam": "!", "at": "@", "numbersign": "#", "dollar": "$",
               "percent": "%", "asciicircum": "^", "ampersand": "&", "asterisk": "*", "parenleft": "(",
               "parenright": ")", "underscore": "_", "colon": ":", "quotedbl": '"', "less": "<",
               "greater": ">", "question": "?", "asciitilde": "~", "bar": "|", "braceleft": "{", "braceright": "}"}
    if lower in shifted:
        return mods, 0, shifted[lower]
    raise BadArgument(f"unknown key {last!r}")


def cf_html(fragment: str) -> bytes:
    """The clipboard's "HTML Format" payload for an HTML fragment."""
    head = ("Version:0.9\r\nStartHTML:{0:010d}\r\nEndHTML:{1:010d}\r\n"
            "StartFragment:{2:010d}\r\nEndFragment:{3:010d}\r\n")
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    blank = head.format(0, 0, 0, 0).encode()
    start_html = len(blank)
    start_fragment = start_html + len(prefix.encode())
    end_fragment = start_fragment + len(fragment.encode())
    end_html = end_fragment + len(suffix.encode())
    return (head.format(start_html, end_html, start_fragment, end_fragment) + prefix + fragment + suffix).encode()


def parse_start_apps(text: str) -> list[dict[str, str]]:
    """``Get-StartApps | ConvertTo-Json`` rows as ``{name, app_id}``."""
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else [data]
    out = []
    for row in rows:
        if isinstance(row, dict) and row.get("Name") and row.get("AppID"):
            out.append({"name": str(row["Name"]), "app_id": str(row["AppID"])})
    return sorted(out, key=lambda r: r["name"].lower())

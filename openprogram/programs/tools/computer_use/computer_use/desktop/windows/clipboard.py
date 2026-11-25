"""Paste and copy through the clipboard, which is restored afterwards."""
from __future__ import annotations

import ctypes
import time

from ..common.base import MAX_COPY_CHARS, BadArgument, ComputerUseError, Report
from .formats import cf_html
from .send_input import _send_key
from .win32 import TEXT, WM_PASTE, _STATE, _class
from . import win32


def _clipboard_text() -> str | None:
    user32, kernel32 = win32._user32(), _STATE["kernel32"]
    if not user32.OpenClipboard(0):
        return None
    try:
        kernel32.GlobalLock.restype = ctypes.c_void_p
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard(text: str, html: str | None) -> None:
    user32, kernel32 = win32._user32(), _STATE["kernel32"]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    def put(fmt: int, data: bytes) -> None:
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        pointer = kernel32.GlobalLock(ctypes.c_void_p(handle))
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        user32.SetClipboardData(fmt, handle)

    for _ in range(10):
        if user32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        raise ComputerUseError("the clipboard is held by another app; try again")
    try:
        user32.EmptyClipboard()
        put(13, text.encode("utf-16-le") + b"\x00\x00")
        if html is not None:
            put(user32.RegisterClipboardFormatW("HTML Format"), cf_html(html) + b"\x00")
    finally:
        user32.CloseClipboard()


def _strip_tags(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", html))


class Clipboard:
    """The paste and copy actions."""

    def _do_paste(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        kind = str(args.get("format") or "text").lower()
        kind = "md" if kind == "markdown" else kind
        if kind not in ("text", "md", "html"):
            raise BadArgument("format must be text, md or html")
        if args.get("element") is not None:
            el = self._element(state, args["element"])
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        html = None
        if kind == "md":
            from markdown_it import MarkdownIt
            html = MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)
        elif kind == "html":
            html = text
        plain = text if kind != "html" else _strip_tags(text)
        saved = _clipboard_text()
        _set_clipboard(plain, html)
        try:
            target = self._focus_hwnd(state)
            if not self._foreground_only(state) and _class(target).lower() in ("edit", "richedit20w", "richedit50w",
                                                                                "richeditd2dpt", "notepadtextbox"):
                win32._user32().SendMessageW(target, WM_PASTE, 0, 0)
            else:
                with self._forward(state):
                    _send_key(0x11, False)
                    _send_key(ord("V"), False)
                    _send_key(ord("V"), True)
                    _send_key(0x11, True)
            time.sleep(0.5)
        finally:
            if saved is not None:
                _set_clipboard(saved, None)
        return f"pasted {len(text)} character(s) as {'markdown' if kind == 'md' else kind}"

    def _do_copy(self, app, state, args) -> str:
        el = self._element(state, args["element"]) if args.get("element") is not None else self._focused(state)
        self._refuse_secure(el)
        pattern = self._pattern(el, TEXT) if el is not None else None
        if pattern is None:
            raise ComputerUseError("no text element has focus; pass element of the text to copy from")
        ranges = self._safe(lambda: pattern.GetSelection())
        parts = []
        for i in range(self._safe(lambda: ranges.Length, 0) or 0):
            piece = self._safe(lambda i=i: str(ranges.GetElement(i).GetText(-1) or ""), "")
            if piece:
                parts.append(piece)
        text = "\n".join(parts)
        if not text:
            raise ComputerUseError("nothing is selected; select text first (select_text, or press_key ctrl+a)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s) (read from the selection; the clipboard was not touched). "
                      f"Copied text:\n<<<\n{clipped}\n>>>")

"""Paste and copy through the pasteboard, which is restored afterwards."""
from __future__ import annotations

import time

from ..common import keys as keymap
from ..common.base import MAX_COPY_CHARS, BadArgument, ComputerUseError, Report
from . import frameworks


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def markdown_to_html(text: str) -> str:
    from markdown_it import MarkdownIt
    return MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)


def _strip_tags(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", html))


def _save_pasteboard(board) -> list[dict[str, bytes]]:
    saved = []
    for item in board.pasteboardItems() or []:
        entry = {}
        for kind in item.types() or []:
            data = item.dataForType_(kind)
            if data is not None:
                entry[str(kind)] = bytes(data)
        saved.append(entry)
    return saved


def _restore_pasteboard(board, saved) -> None:
    import AppKit
    board.clearContents()
    items = []
    for entry in saved:
        item = AppKit.NSPasteboardItem.alloc().init()
        for kind, data in entry.items():
            item.setData_forType_(AppKit.NSData.dataWithBytes_length_(data, len(data)), kind)
        items.append(item)
    if items:
        board.writeObjects_(items)


class Clipboard:
    """The paste and copy actions."""

    def _do_paste(self, app, pid, state, args) -> str:
        appkit, _, _, _ = frameworks._frameworks()
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        kind = str(args.get("format") or "text").lower()
        if kind == "markdown":
            kind = "md"
        if kind not in ("text", "md", "html"):
            raise BadArgument("format must be text, md or html")
        if args.get("element") is not None:
            element = self._element(state, args["element"])
            self._refuse_secure(self._node_at(state, args["element"]))
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            plain = text if kind != "html" else _strip_tags(text)
            if not self._insert(pid, plain):
                raise ComputerUseError(
                    "The window is on another Space, where macOS does not deliver the paste shortcut, and the "
                    "focused element takes no inserted text. Focus a text field first, or use element actions.")
            return (f"inserted {len(plain)} character(s) at the focused field as plain text (the window is on "
                    "another Space, where the paste shortcut does not arrive)")
        self._refuse_file_panel(app, pid)
        board = appkit.NSPasteboard.generalPasteboard()
        saved = _save_pasteboard(board)
        board.clearContents()
        if kind == "text":
            board.setString_forType_(text, appkit.NSPasteboardTypeString)
        else:
            html = markdown_to_html(text) if kind == "md" else text
            board.setString_forType_(html, appkit.NSPasteboardTypeHTML)
            board.setString_forType_(text if kind == "md" else _strip_tags(text), appkit.NSPasteboardTypeString)
        written = board.changeCount()
        try:
            self._post_chord(pid, keymap.parse_chord("cmd+v"))
            time.sleep(0.6)
        finally:
            # Leave a clipboard the user changed meanwhile alone.
            if board.changeCount() == written:
                _restore_pasteboard(board, saved)
        return f"pasted {len(text)} character(s) as {'markdown' if kind == 'md' else kind}"

    def _do_copy(self, app, pid, state, args) -> str:
        """The app's own Copy, read back from the clipboard, which is then restored."""
        appkit, _, _, _ = frameworks._frameworks()
        if args.get("element") is not None:
            element = self._element(state, args["element"])
            self._refuse_secure(self._node_at(state, args["element"]))
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        board = appkit.NSPasteboard.generalPasteboard()
        saved = _save_pasteboard(board)
        before = board.changeCount()
        item = self._shortcut_item(pid, "c", enabled_only=False)
        if item is not None and self._attr(item, "AXEnabled") is False:
            raise ComputerUseError(f"{app.localizedName()}'s Copy is disabled right now: nothing is selected, or "
                                   "focus is on a control. Select text first (select_text, or press_key cmd+a).")
        try:
            if item is None or self._perform(item, "AXPress") != 0:
                self._post_chord(pid, keymap.parse_chord("cmd+c"))
            deadline = time.monotonic() + 1.5
            while board.changeCount() == before and time.monotonic() < deadline:
                time.sleep(0.05)
            if board.changeCount() == before:
                raise ComputerUseError("Copy put nothing on the clipboard; select something first "
                                       "(select_text, or press_key cmd+a).")
            text = str(board.stringForType_(appkit.NSPasteboardTypeString) or "")
        finally:
            if board.changeCount() != before:
                _restore_pasteboard(board, saved)
        if not text:
            return Report("copied something that is not text (an image or a file)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s); the clipboard was restored. Copied text:\n<<<\n{clipped}\n>>>")

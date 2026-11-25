"""The application's menu bar: reading items, finding one by shortcut, and choosing one."""
from __future__ import annotations

from typing import Any

from ..common.base import ComputerUseError, Report, menu_key as _menu_key, menu_path
from . import frameworks


class Menus:
    """The menu action."""

    def _menu_children(self, item) -> list[Any]:
        """Items under a menu bar item or a submenu item."""
        for child in self._attr(item, "AXChildren") or []:
            if str(self._attr(child, "AXRole") or "") == "AXMenu":
                return [c for c in self._attr(child, "AXChildren") or []]
        return []

    def _menu_title(self, item) -> str:
        return str(self._attr(item, "AXTitle") or "")

    def _menu_line(self, item) -> str:
        title = self._menu_title(item)
        values = self._attrs(item, ["AXEnabled", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers",
                                    "AXMenuItemMarkChar"])
        parts = [title]
        char = values.get("AXMenuItemCmdChar")
        if isinstance(char, str) and char.strip():
            mods = values.get("AXMenuItemCmdModifiers")
            mods = int(mods) if isinstance(mods, (int, float)) else 0
            # kAXMenuItemModifierShift 1, Option 2, Control 4, NoCommand 8
            glyphs = ("⌃" if mods & 4 else "") + ("⌥" if mods & 2 else "") + ("⇧" if mods & 1 else "") \
                + ("" if mods & 8 else "⌘")
            parts.append(f"({glyphs}{char.strip().upper()})")
        if isinstance(values.get("AXMenuItemMarkChar"), str) and values["AXMenuItemMarkChar"].strip():
            parts.append("[checked]")
        if values.get("AXEnabled") is False:
            parts.append("[disabled]")
        if self._menu_children(item):
            parts.append("▸")
        return " ".join(parts)

    def _shortcut_item(self, pid: int, char: str, modifiers: int = 0, *, enabled_only: bool = True):
        """The menu item whose shortcut is ⌘<char>, in any locale (enabled ones only, by default)."""
        _, ax, _, _ = frameworks._frameworks()
        bar = self._attr(ax.AXUIElementCreateApplication(pid), "AXMenuBar")
        for top in self._attr(bar, "AXChildren") or [] if bar is not None else []:
            for item in self._menu_children(top):
                values = self._attrs(item, ["AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXEnabled"])
                if (str(values.get("AXMenuItemCmdChar") or "").lower() == char
                        and int(values.get("AXMenuItemCmdModifiers") or 0) == modifiers
                        and (values.get("AXEnabled") is not False or not enabled_only)):
                    return item
        return None

    def _do_menu(self, app, pid, state, args) -> str:
        """Walk the menu bar by title: list a menu, or press a leaf item, without opening anything."""
        _, ax, _, _ = frameworks._frameworks()
        path = menu_path(args)
        bar = self._attr(ax.AXUIElementCreateApplication(pid), "AXMenuBar")
        if bar is None:
            raise ComputerUseError(f"{app.localizedName()} has no menu bar")
        if not path:
            titles = [self._menu_title(item) for item in self._attr(bar, "AXChildren") or []]
            titles = [t for t in titles if t and t != "Apple"]
            return Report("menu bar: " + " | ".join(titles) + ". Pass menu_path to list a menu or press an item.")
        level = list(self._attr(bar, "AXChildren") or [])
        item, walked = None, []
        for step in path:
            wanted = _menu_key(step)
            item = next((i for i in level if _menu_key(self._menu_title(i)) == wanted), None)
            if item is None:
                titles = [self._menu_title(i) for i in level if self._menu_title(i)]
                where = " > ".join(walked) or "the menu bar"
                raise ComputerUseError(f"{step!r} is not in {where}; it has: " + " | ".join(titles))
            walked.append(self._menu_title(item))
            level = self._menu_children(item)
        if level:
            lines = [self._menu_line(i) for i in level if self._menu_title(i)]
            return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {line}" for line in lines))
        if self._attr(item, "AXEnabled") is False:
            raise ComputerUseError(" > ".join(walked) + " is disabled right now")
        code = self._perform(item, "AXPress")
        if code != 0:
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed (Accessibility error {code})")
        return "chose " + " > ".join(walked)

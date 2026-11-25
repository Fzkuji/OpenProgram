"""Menus: a window's Win32 menu bar and UI Automation menus."""
from __future__ import annotations

import ctypes
import time

from ..common.base import ComputerUseError, Report, menu_key, menu_path
from .win32 import EXPAND_COLLAPSE, INVOKE, WM_COMMAND
from . import win32


def _menu_items(user32, menu) -> list[tuple[str, int, int, int]]:
    if not menu:
        return []
    out = []
    for position in range(max(0, user32.GetMenuItemCount(menu))):
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetMenuStringW(menu, position, buffer, 256, 0x400)  # MF_BYPOSITION
        sub = user32.GetSubMenu(menu, position)
        out.append((buffer.value.replace("&", ""), int(user32.GetMenuItemID(menu, position)) & 0xFFFFFFFF,
                    int(sub or 0), int(user32.GetMenuState(menu, position, 0x400))))
    return out


class Menus:
    """The menu action."""

    def _win32_menu(self, hwnd: int) -> list[tuple[str, int, int, int]]:
        """Items of a classic menu: (title, id, submenu handle, state flags)."""
        user32 = win32._user32()
        return _menu_items(user32, user32.GetMenu(hwnd))

    def _do_menu(self, app, state, args) -> str:
        path = menu_path(args)
        state = state or self._ensure_window(app)
        user32 = win32._user32()
        items = self._win32_menu(state.window_id)
        if items:
            if not path:
                return Report("menu bar: " + " | ".join(t for t, *_ in items if t) + ". Pass menu_path to list a menu "
                              "or press an item.")
            walked, item = [], None
            level = items
            for step in path:
                wanted = menu_key(step)
                item = next((i for i in level if menu_key(i[0]) == wanted), None)
                if item is None:
                    raise ComputerUseError(f"{step!r} is not in {' > '.join(walked) or 'the menu bar'}; it has: "
                                           + " | ".join(t for t, *_ in level if t))
                walked.append(item[0])
                level = _menu_items(user32, item[2]) if item[2] else []
            if level:
                lines = []
                for title, _id, sub, flags in level:
                    if not title:
                        continue
                    line = title.replace("\t", " (") + (")" if "\t" in title else "")
                    if flags & 0x8:
                        line += " [checked]"
                    if flags & 0x3:
                        line += " [disabled]"
                    if sub:
                        line += " ▸"
                    lines.append(f"- {line}")
                return Report(" > ".join(walked) + ":\n" + "\n".join(lines))
            if item[3] & 0x3:
                raise ComputerUseError(" > ".join(walked) + " is disabled right now")
            user32.PostMessageW(state.window_id, WM_COMMAND, item[1] & 0xFFFF, 0)
            return "chose " + " > ".join(walked)
        # Ribbon and modern apps expose their menus through UI Automation only.
        automation, _ = win32._uia()
        root = self._safe(lambda: automation.ElementFromHandle(state.window_id))
        bar = next((el for el in self._walk(root, 4) if self._role(el) == "menu bar"), None)
        if bar is None:
            raise ComputerUseError(f"{app.name} has no menu bar (apps with a ribbon or a hamburger button keep "
                                   "their menu there; click it and read the state)")
        level = [el for el in self._children(bar) if self._role(el) == "menu item"]
        if not path:
            return Report("menu bar: " + " | ".join(self._name(el) for el in level) + ". Pass menu_path to list a "
                          "menu or press an item.")
        walked, item = [], None
        for step in path:
            wanted = menu_key(step)
            item = next((el for el in level if menu_key(self._name(el)) == wanted), None)
            if item is None:
                raise ComputerUseError(f"{step!r} is not in {' > '.join(walked) or 'the menu bar'}; it has: "
                                       + " | ".join(self._name(el) for el in level))
            walked.append(self._name(item))
            expand = self._pattern(item, EXPAND_COLLAPSE)
            if expand is not None and step is not path[-1]:
                expand.Expand()
                time.sleep(0.2)
                level = [el for el in self._walk(item, 3) if self._role(el) == "menu item"]
            else:
                level = []
        if expand := self._pattern(item, EXPAND_COLLAPSE):
            expand.Expand()
            time.sleep(0.2)
            children = [el for el in self._walk(item, 3) if self._role(el) == "menu item"]
            if children:
                return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {self._name(el)}" for el in children))
        invoke = self._pattern(item, INVOKE)
        if invoke is None or not self._safe(lambda: invoke.Invoke() or True, False):
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed")
        return "chose " + " > ".join(walked)

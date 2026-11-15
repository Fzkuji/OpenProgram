import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// Radix menus only mount their content once opened by real pointer input;
// this stand-in renders every row, keeps the content's onKeyDown and turns
// a row's onSelect into a click.
const DROPDOWN = `
import { createElement as h } from "react";
const pass = ({ children }) => h("div", null, children);
export const DropdownMenu = pass;
export const DropdownMenuTrigger = ({ children }) => children;
export const DropdownMenuContent = ({ children, onKeyDown }) => h("div", { role: "menu", tabIndex: -1, onKeyDown }, children);
export const DropdownMenuItem = ({ children, onSelect, disabled }) =>
  h("div", { role: "menuitem", "aria-disabled": disabled ? "true" : undefined, onClick: () => { if (!disabled) onSelect?.(); } }, children);
export const DropdownMenuSeparator = () => h("hr");
export const DropdownMenuSub = pass;
export const DropdownMenuSubTrigger = ({ children }) => h("div", { role: "menuitem" }, children);
export const DropdownMenuSubContent = ({ children }) => h("div", { role: "menu" }, children);
`;

installLoader({
  replace: { "/components/ui/dropdown-menu.tsx": DROPDOWN },
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/solar-icons": "export const SolarIcon = () => null;",
    "@/components/ui/tooltip": "export const HoverTip = ({ children }) => children; export const TipBody = () => null;",
    "@/lib/clipboard": "export const copyText = async (text) => { (globalThis.__copied ??= []).push(text); return true; };",
    "@/lib/chat/session-auto-rename": "export const autoRenameSession = () => {};",
    "@/components/center-tabs/pip/pip-dock-slot": "export const PipDockSlot = () => null;",
    "@/components/chat/pane/rename-dialog": "import { createElement as h } from 'react'; export const RenameDialog = () => h('div', { 'data-dialog': 'rename' });",
    "@/components/ui/confirm-dialog": "import { createElement as h } from 'react'; export const ConfirmDialog = () => h('div', { 'data-dialog': 'delete' });",
    "@/components/sidebar/sessions/helpers": "export const wsSend = (payload) => { globalThis.__sent.push(payload); return true; };",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
    "@/lib/runtime-bridge/state": "export const runtimeState = { conversations: {} };",
    "@/lib/tabs/navigation/open-session": "export const deleteSessionAndRefocus = (id) => globalThis.__deleted.push(id); export const openSession = () => {};",
    "@/lib/tabs/canvas/canvas-fullscreen": "export const canvasFullscreen = () => false; export const setCanvasFullscreen = () => {}; export const subscribeCanvasFullscreen = () => () => {};",
    "@/lib/prefs/transcript-view": "export const setDefaultTranscriptView = () => {}; export const setSessionTranscriptView = () => {}; export const useDefaultTranscriptView = () => 'normal'; export const useTranscriptView = () => 'normal';",
    "@/lib/tabs/center-tabs-store": [
      "import { create } from 'zustand';",
      "export const useCenterTabs = create(() => ({ activeId: 't1', tabs: [{ id: 't1', kind: 'session', sessionId: 's1', title: 'Plans' }], setTabDagView() {}, renameSessionTab() {} }));",
    ].join("\n"),
    "@/lib/session-store": [
      "import { create } from 'zustand';",
      "export const useSessionStore = create((set) => ({",
      "  conversations: { s1: { id: 's1', title: 'Plans', pinned: false, archived: false } },",
      "  upsertConversation: (conv) => set((s) => ({ conversations: { ...s.conversations, [conv.id]: conv } })),",
      "}));",
    ].join("\n"),
  },
});
installDom();

const { createElement } = await import("react");
const { formatMenuKeys, MenuRow } = await import("@/components/ui/menu/menu-parts");
const { ConvMenu } = await import("@/components/sidebar/sessions/conv-menu");
const { ViewControls } = await import("@/components/chat/pane/view-controls");

function press(target, key, { metaKey = false, ctrlKey = false, altKey = false } = {}) {
  const event = new Event("keydown", { bubbles: true, cancelable: true });
  Object.assign(event, { key, metaKey, ctrlKey, altKey, shiftKey: false });
  target.dispatchEvent(event);
}
const badges = (root) => [...root.querySelectorAll("kbd")].map((kbd) => kbd.textContent);

describe("key notation", () => {
  test("macOS writes modifiers as glyphs in ⌥⇧⌘ order before the key", () => {
    assert.equal(formatMenuKeys({ key: "t", mod: true }, true), "⌘T");
    assert.equal(formatMenuKeys({ key: "t", mod: true, shift: true }, true), "⇧⌘T");
    assert.equal(formatMenuKeys({ key: "t", mod: true, shift: true, alt: true }, true), "⌥⇧⌘T");
  });

  test("elsewhere modifiers are words joined by + in Ctrl, Alt, Shift order", () => {
    assert.equal(formatMenuKeys({ key: "t", mod: true }, false), "Ctrl+T");
    assert.equal(formatMenuKeys({ key: "t", mod: true, shift: true, alt: true }, false), "Ctrl+Alt+Shift+T");
    assert.equal(formatMenuKeys({ key: "C", mod: true, shift: true }, false), "Ctrl+Shift+C");
  });

  test("a bare menu letter is upper-cased and a named key is kept as written", () => {
    assert.equal(formatMenuKeys({ key: "r" }, true), "R");
    assert.equal(formatMenuKeys({ key: "r" }, false), "R");
    assert.equal(formatMenuKeys({ key: ",", mod: true }, true), "⌘,");
    assert.equal(formatMenuKeys({ key: "Enter", mod: true }, false), "Ctrl+Enter");
  });

  test("a row draws its key as a badge after the label, then the check", async () => {
    const view = await render(createElement("div", null, createElement(MenuRow, { glyph: "pen", label: "Rename", keys: { key: "r" }, checked: true })));
    const children = [...view.container.firstElementChild.children];
    assert.equal(children[0].textContent, "Rename");
    assert.equal(children[1].querySelector("kbd").textContent, "R");
    assert.equal(children[2].tagName.toLowerCase(), "svg", "the check comes after the key badge");
    await view.unmount();
  });
});

describe("chat menus answer their row letters while open", () => {
  test("the Recents menu shows R P C A D and runs the matching action, then closes", async () => {
    const calls = [];
    const record = (name) => () => calls.push(name);
    const view = await render(createElement(ConvMenu, {
      conv: { id: "s1", title: "Plans" },
      groups: [],
      onRename: record("rename"), onAutoRename: record("auto"), onTogglePin: record("pin"),
      onToggleArchive: record("archive"), onMoveToGroup: record("group"), onNewGroup: record("new-group"),
      onCopyLink: record("copy"), onExport: record("export"), onDelete: record("delete"), onClose: record("close"),
    }));
    assert.deepEqual(badges(view.container), ["R", "P", "C", "A", "D"]);
    const menu = view.container.querySelector("[role=menu]");
    for (const [key, action] of [["r", "rename"], ["P", "pin"], ["c", "copy"], ["a", "archive"], ["d", "delete"]]) {
      calls.length = 0;
      await view.act(() => press(menu, key));
      assert.deepEqual(calls, [action, "close"], `${key} runs ${action}`);
    }
    calls.length = 0;
    await view.act(() => {
      press(menu, "r", { metaKey: true });
      press(menu, "d", { ctrlKey: true });
      press(menu, "a", { altKey: true });
      press(menu, "x");
    });
    assert.deepEqual(calls, [], "a modifier or an unbound letter does nothing");
    await view.unmount();
  });

  test("the chat pane's session menu shows R P C A D and runs the matching action", async () => {
    globalThis.__sent = [];
    globalThis.__deleted = [];
    const view = await render(createElement(ViewControls));
    assert.deepEqual(badges(view.container), ["R", "P", "C", "A", "D"]);
    const menu = () => view.container.querySelector("[role=menu]");

    await view.act(() => press(menu(), "p"));
    assert.deepEqual(globalThis.__sent.at(-1), { action: "update_session_flags", session_id: "s1", pinned: true });
    await view.act(() => press(menu(), "a"));
    assert.deepEqual(globalThis.__sent.at(-1), { action: "update_session_flags", session_id: "s1", archived: true });
    await view.act(() => press(menu(), "r"));
    assert.ok(view.container.querySelector("[data-dialog=rename]"), "R opens the rename dialog");
    await view.act(() => press(menu(), "d"));
    assert.ok(view.container.querySelector("[data-dialog=delete]"), "D asks before deleting");

    const sent = globalThis.__sent.length;
    await view.act(() => press(menu(), "p", { metaKey: true }));
    assert.equal(globalThis.__sent.length, sent, "a modifier leaves the key to the system");
    await view.unmount();
  });
});

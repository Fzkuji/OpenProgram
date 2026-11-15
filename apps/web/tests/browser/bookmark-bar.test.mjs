import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, memoryStorage, render, resizeObservers, setRect } from "../_support/dom.mjs";

// Radix menus mount their content only after real pointer input; this
// stand-in renders every level, so a submenu is a nested role=menu.
const DROPDOWN = `
import { createElement as h } from "react";
const pass = ({ children }) => h("div", null, children);
export const DropdownMenu = pass;
export const DropdownMenuTrigger = ({ children }) => children;
export const DropdownMenuContent = ({ children }) => h("div", { role: "menu" }, children);
export const DropdownMenuItem = ({ children, onSelect, disabled }) =>
  h("div", { role: "menuitem", "aria-disabled": disabled ? "true" : undefined, onClick: () => { if (!disabled) onSelect?.(); } }, children);
export const DropdownMenuSeparator = () => h("hr");
export const DropdownMenuSub = ({ children }) => h("div", { "data-submenu": "" }, children);
export const DropdownMenuSubTrigger = ({ children }) => h("div", { role: "menuitem", "aria-haspopup": "menu" }, children);
export const DropdownMenuSubContent = ({ children }) => h("div", { role: "menu" }, children);
`;

// The desktop overlay page uses radix's primitives directly.
const RADIX_DROPDOWN = `
import { createElement as h } from "react";
const pass = ({ children }) => h("div", null, children);
export const Root = pass;
export const Trigger = ({ children }) => children;
export const Portal = pass;
export const Content = ({ children, style }) => h("div", { role: "menu", "data-width": style?.width }, children);
export const Sub = ({ children }) => h("div", { "data-submenu": "" }, children);
export const SubTrigger = ({ children, title }) => h("div", { role: "menuitem", "aria-haspopup": "menu", title }, children);
export const SubContent = ({ children }) => h("div", { role: "menu" }, children);
export const Item = ({ children, onSelect, disabled, role }) =>
  h("div", { role: role ?? "menuitem", "aria-disabled": disabled ? "true" : undefined, onClick: () => { if (!disabled) onSelect?.({ preventDefault() {} }); } }, children);
`;

installLoader({
  replace: {
    "/components/ui/dropdown-menu.tsx": DROPDOWN,
    "/@radix-ui/react-dropdown-menu/": RADIX_DROPDOWN,
    "/components/center-tabs/strip/tab-favicon.tsx": "import { createElement as h } from 'react'; export const TabFavicon = ({ url }) => h('i', { 'data-favicon': url ?? '' });",
    "/components/center-tabs/browser/toolbar/browser-chrome.tsx": "export const ToolbarTip = ({ children }) => children;",
  },
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/lib/desktop/desktop-bridge": "export const desktopBridge = () => globalThis.__bridge ?? null;",
    "@/lib/prefs/appearance/theme-pref": "export const activeThemeId = () => 'light'; export const isThemeId = () => false;",
    "@/components/solar-icons": "import { createElement as h } from 'react'; export const SolarIcon = ({ name }) => h('i', { 'data-glyph': name }); export const SOLAR_BODIES = { folder: 1, copy: 1, history: 1 };",
    "@/components/ui/search-input": "export const SearchInput = ({ onChange }) => { globalThis.__search = onChange; return null; };",
    "@/lib/tabs/center-tabs-store": [
      "const calls = (globalThis.__tabCalls ??= []);",
      "const state = { openBuiltinTab: (page) => calls.push(['builtin', page]), openWebTab: (url) => calls.push(['web', url]) };",
      "export const useCenterTabs = Object.assign((selector) => selector(state), { getState: () => state });",
    ].join("\n"),
  },
});
const window = installDom();
const { act, createElement } = await import("react");
const bookmarks = await import("@/lib/tabs/browser/bookmarks");
const prefs = await import("@/lib/browser/browser-prefs");
const { BookmarkBar } = await import("@/components/center-tabs/browser/bookmark-bar");
const { BookmarksPage } = await import("@/components/center-tabs/pages/library/bookmarks-page");
const { default: ContextMenuOverlay } = await import("@/app/menu-overlay/context-menu/page");

// Layout: every bar item is 100px wide; the bar is as wide as the test says.
let barWidth = 10_000;
Object.defineProperty(window.HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 100 });
Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { configurable: true, get: () => barWidth });
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.getComputedStyle = () => ({ columnGap: "0px", getPropertyValue: () => "" });

const ROOT = bookmarks.BOOKMARKS_ROOT_ID;
const leaf = (id, title, url, extra = {}) => ({ kind: "bookmark", id, title, url, ...extra });
const folder = (id, title, children) => ({ kind: "folder", id, title, children });

function saveTree(children) {
  localStorage.setItem(bookmarks.BOOKMARKS_STORAGE_KEY, JSON.stringify({
    version: bookmarks.BOOKMARKS_VERSION,
    root: folder(ROOT, "", children),
  }));
}

const BAR_TREE = [
  folder("bar", "Bookmarks bar", [
    leaf("gh", "GitHub", "https://github.com/"),
    folder("research", "Research", [
      leaf("paper", "Paper", "https://paper.example/", { faviconUrl: "https://paper.example/icon.png" }),
      folder("drafts", "Drafts", []),
    ]),
    leaf("docs", "Docs", "https://docs.example/"),
  ]),
];

function fakeMainMenu() {
  const menu = { opened: [], closed: 0, cancelled: 0, scheduled: [], actions: new Set() };
  menu.api = {
    open: (payload) => menu.opened.push(payload),
    close: () => { menu.closed += 1; },
    onAction: (cb) => { menu.actions.add(cb); return () => menu.actions.delete(cb); },
    onClosed: () => () => {},
    cancelClose: () => { menu.cancelled += 1; },
    scheduleClose: (delay) => menu.scheduled.push(delay),
  };
  menu.fire = (id) => { for (const cb of menu.actions) cb(id); };
  return menu;
}

const byLabel = (root, label) => root.querySelector(`button[aria-label="${label}"]`);
const barButtons = (root) => [...root.querySelectorAll("[aria-label='Bookmarks bar'] > div button")]
  .filter((button) => !button.closest("[role=menu]"));
function fire(element, type, init = {}) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, init);
  element.dispatchEvent(event);
}

beforeEach(() => {
  globalThis.localStorage = window.localStorage = memoryStorage({ agentic_locale: "en" });
  globalThis.__tabCalls.length = 0;
  barWidth = 10_000;
  saveTree(BAR_TREE);
});
afterEach(() => {
  delete globalThis.__bridge;
});

describe("bookmark bar", () => {
  test("it shows the Bookmarks bar folder's children; a bookmark navigates; All bookmarks opens the manager", async () => {
    const navigated = [];
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate: (url) => navigated.push(url) }));
    assert.deepEqual(barButtons(view.container).map((b) => b.textContent), ["GitHub", "Research", "Docs"]);
    await view.act(() => barButtons(view.container)[0].click());
    assert.deepEqual(navigated, ["https://github.com/"]);
    await view.act(() => byLabel(view.container, "All bookmarks").click());
    assert.deepEqual(globalThis.__tabCalls, [["builtin", "bookmarks"]]);
    await view.unmount();
  });

  test("it hides when the bookmarks bar is turned off and follows later bookmark changes", async () => {
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate() {} }));
    await view.act(() => bookmarks.toggleBookmark({ title: "Loose", url: "https://loose.example/" }));
    assert.ok(barButtons(view.container).some((b) => b.textContent === "Loose"));
    await view.act(() => prefs.setShowBookmarksBar(false));
    assert.equal(view.container.querySelector("[aria-label='Bookmarks bar']"), null);
    await view.unmount();
  });

  test("in the Web build a folder opens nested submenus that keep the tree, with favicons on bookmarks", async () => {
    const navigated = [];
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate: (url) => navigated.push(url) }));
    const research = view.container.querySelector("[role=menu]");
    const [paperRow, draftsTrigger] = [...research.children].map((child) => child.querySelector("[role=menuitem]") ?? child);
    assert.equal(paperRow.textContent, "Paper");
    assert.equal(paperRow.querySelector("[data-favicon]").getAttribute("data-favicon"), "https://paper.example/icon.png");
    assert.equal(draftsTrigger.getAttribute("aria-haspopup"), "menu", "a nested folder is a submenu, not a flattened path");
    assert.equal(draftsTrigger.textContent, "Drafts");
    const empty = research.querySelector("[role=menu] [role=menu] [role=menuitem]");
    assert.equal(empty.textContent, "Empty folder");
    assert.equal(empty.getAttribute("aria-disabled"), "true");
    await view.act(() => paperRow.click());
    assert.deepEqual(navigated, ["https://paper.example/"]);
    await view.unmount();
  });

  test("in the desktop app a folder opens the overlay menu with the folder tree, owned by this pane", async () => {
    const menu = fakeMainMenu();
    globalThis.__bridge = { mainMenu: menu.api };
    const navigated = [];
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate: (url) => navigated.push(url) }));
    const research = byLabel(view.container, "Research");
    await view.act(() => research.click());
    assert.equal(research.getAttribute("aria-expanded"), "true");
    const [payload] = menu.opened;
    assert.equal(payload.cascade, true);
    assert.equal(payload.width, 280);
    const prefix = "bookmarkfolder:pane-a:research:";
    assert.deepEqual(payload.items, [
      { id: `${prefix}bookmark:paper`, label: "Paper", iconUrl: "https://paper.example/icon.png", iconFallbackUrl: "https://paper.example/favicon.ico" },
      { id: `${prefix}folder:drafts`, label: "Drafts", icon: "folder", children: [{ id: `${prefix}empty:drafts`, label: "Empty folder", disabled: true }] },
    ]);

    await view.act(() => {
      menu.fire("bookmarkfolder:pane-b:research:bookmark:paper");
      menu.fire(`${prefix}folder:drafts`);
    });
    assert.deepEqual(navigated, [], "another pane's or a folder row's action is ignored");
    await view.act(() => menu.fire(`${prefix}bookmark:paper`));
    assert.deepEqual(navigated, ["https://paper.example/"]);
    await view.unmount();
  });

  test("in the desktop app hovering a folder keeps the menu open and leaving schedules its close", async () => {
    const menu = fakeMainMenu();
    globalThis.__bridge = { mainMenu: menu.api };
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate() {} }));
    const research = byLabel(view.container, "Research");
    await view.act(() => fire(research, "mouseover", { relatedTarget: null }));
    assert.equal(menu.cancelled, 1);
    assert.equal(menu.opened.length, 0, "hover alone opens nothing until a folder menu is open");
    await view.act(() => fire(research, "mouseout", { relatedTarget: null }));
    assert.deepEqual(menu.scheduled, [120]);
    await view.unmount();
  });

  test("items that do not fit are hidden from view and reachable through the overflow button", async () => {
    const menu = fakeMainMenu();
    globalThis.__bridge = { mainMenu: menu.api };
    barWidth = 250;
    const view = await render(createElement(BookmarkBar, { ownerId: "pane-a", onNavigate() {} }));
    await view.act(async () => {});
    const buttons = barButtons(view.container);
    assert.deepEqual(buttons.map((b) => b.getAttribute("aria-hidden")), [null, null, "true"]);
    assert.equal(buttons[2].tabIndex, -1);
    const more = byLabel(view.container, "Show hidden bookmarks");
    await view.act(() => more.click());
    assert.deepEqual(menu.opened[0].items.map((item) => item.label), ["Docs"]);

    barWidth = 10_000;
    await view.act(() => resizeObservers.trigger());
    assert.deepEqual(barButtons(view.container).map((b) => b.getAttribute("aria-hidden")), [null, null, null]);
    assert.equal(byLabel(view.container, "Show hidden bookmarks"), null);
    await view.unmount();
  });
});

describe("bookmark manager drop zones", () => {
  const MANAGER_TREE = [
    leaf("a", "Alpha", "https://a.example/"),
    leaf("b", "Beta", "https://b.example/"),
    folder("f", "Folder", [leaf("c", "Gamma", "https://c.example/")]),
  ];
  const zones = (root) => [...root.querySelectorAll("[class~=bookmarkManagerDropZone]")];
  const rowOf = (root, title) => [...root.querySelectorAll("[draggable]")].find((row) => row.textContent.includes(title));
  const rootTitles = () => bookmarks.readBookmarkTree().children.map((node) => node.title);
  const dataTransfer = () => ({ effectAllowed: "", dropEffect: "" });

  async function drag(view, title, target) {
    await view.act(() => fire(rowOf(view.container, title), "dragstart", { dataTransfer: dataTransfer() }));
    await view.act(() => fire(target, "dragover", { dataTransfer: dataTransfer() }));
    await view.act(() => fire(target, "drop", { dataTransfer: dataTransfer() }));
  }

  beforeEach(() => saveTree(MANAGER_TREE));

  test("there is a zone before every row and one after the last", async () => {
    const view = await render(createElement(BookmarksPage));
    assert.equal(zones(view.container).length, 4);
    await view.unmount();
  });

  test("dropping on the zone below the next row moves a bookmark down one place", async () => {
    const view = await render(createElement(BookmarksPage));
    await drag(view, "Alpha", zones(view.container)[2]);
    assert.deepEqual(rootTitles(), ["Beta", "Alpha", "Folder"]);
    await view.unmount();
  });

  test("dropping on the first zone moves a bookmark to the top, and on the last zone to the end", async () => {
    const view = await render(createElement(BookmarksPage));
    await drag(view, "Folder", zones(view.container)[0]);
    assert.deepEqual(rootTitles(), ["Folder", "Alpha", "Beta"]);
    await drag(view, "Folder", zones(view.container).at(-1));
    assert.deepEqual(rootTitles(), ["Alpha", "Beta", "Folder"]);
    await view.unmount();
  });

  test("a zone marks itself while a row is dragged over it", async () => {
    const view = await render(createElement(BookmarksPage));
    await view.act(() => fire(rowOf(view.container, "Alpha"), "dragstart", { dataTransfer: dataTransfer() }));
    await view.act(() => fire(zones(view.container)[2], "dragover", { dataTransfer: dataTransfer() }));
    assert.deepEqual(zones(view.container).map((zone) => zone.getAttribute("data-drop-target")), [null, null, "true", null]);
    await view.unmount();
  });

  test("dropping on a folder row moves the bookmark into it", async () => {
    const view = await render(createElement(BookmarksPage));
    await drag(view, "Alpha", rowOf(view.container, "Folder"));
    assert.deepEqual(rootTitles(), ["Beta", "Folder"]);
    assert.deepEqual(bookmarks.findNode(bookmarks.readBookmarkTree(), "f").children.map((node) => node.title), ["Gamma", "Alpha"]);
    await view.unmount();
  });

  test("a bookmark title opens a web tab; a folder title opens that folder", async () => {
    const view = await render(createElement(BookmarksPage));
    const title = (text) => [...view.container.querySelectorAll("main button")].find((button) => button.textContent === text);
    await view.act(() => title("Alpha").click());
    assert.deepEqual(globalThis.__tabCalls, [["web", "https://a.example/"]]);
    await view.act(() => title("Folder").click());
    assert.ok(rowOf(view.container, "Gamma"));
    assert.equal(rowOf(view.container, "Alpha"), undefined);
    await view.unmount();
  });

  test("New folder is created in the selected folder", async () => {
    const view = await render(createElement(BookmarksPage));
    const folderTitle = [...view.container.querySelectorAll("nav button")].find((button) => button.textContent === "Folder");
    await view.act(() => folderTitle.click());
    const newFolder = [...view.container.querySelectorAll("header [role=menuitem]")].find((row) => row.textContent === "New folder");
    await view.act(() => newFolder.click());
    assert.deepEqual(bookmarks.findNode(bookmarks.readBookmarkTree(), "f").children.map((node) => node.title), ["Gamma", "New folder"]);
    await view.unmount();
  });

  test("renaming a row edits in place with labelled Save and Cancel buttons; Delete removes the row", async () => {
    const view = await render(createElement(BookmarksPage));
    const rowMenu = (title, label) => [...rowOf(view.container, title).querySelectorAll("[role=menuitem]")].find((row) => row.textContent === label);
    await view.act(() => rowMenu("Alpha", "Edit bookmark title").click());
    const actions = [...view.container.querySelectorAll("[class~=bookmarkContentEditActions] button")];
    assert.deepEqual(actions.map((button) => [button.type, button.getAttribute("aria-label"), button.getAttribute("title")]), [
      ["button", "Save bookmark title", "Save bookmark title"],
      ["button", "Cancel editing", "Cancel editing"],
    ]);
    const input = view.container.querySelector("input[aria-label='Edit bookmark title']");
    input.type ||= "text";
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, "Alpha 2");
    await view.act(() => input.dispatchEvent(new Event("input", { bubbles: true })));
    await view.act(() => actions[0].click());
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), "a").title, "Alpha 2");
    await view.act(() => rowMenu("Beta", "Delete bookmark").click());
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), "b"), null);
    await view.unmount();
  });

  test("no button is nested inside another button", async () => {
    const view = await render(createElement(BookmarksPage));
    assert.equal(view.container.querySelector("button button"), null);
    await view.unmount();
  });

  test("a search lists matches from every folder and hides the zones, whose slots would be wrong", async () => {
    const view = await render(createElement(BookmarksPage));
    await act(async () => globalThis.__search("gam"));
    assert.equal(zones(view.container).length, 0);
    assert.ok(rowOf(view.container, "Gamma"), "a search recurses into folders");
    assert.equal(rowOf(view.container, "Alpha"), undefined);
    await act(async () => globalThis.__search("nothing"));
    assert.match(view.container.textContent, /No matching bookmarks/);
    await view.unmount();
  });
});

describe("desktop menu overlay", () => {
  function overlayBridge() {
    const bridge = { chosen: [], sizes: [], update: () => {} };
    window.openprogramDesktop = {
      mainMenu: {
        choose: (id) => bridge.chosen.push(id),
        close() {},
        resize: (size) => bridge.sizes.push(size),
        onUpdate: (cb) => { bridge.update = cb; return () => {}; },
      },
    };
    return bridge;
  }
  function openOverlay(params) {
    window.location.search = `?${new URLSearchParams(params)}`;
    return render(createElement(ContextMenuOverlay));
  }
  afterEach(() => {
    delete window.openprogramDesktop;
    window.location.search = "";
  });

  test("a bookmark folder menu cascades: folders are submenus with a folder icon, bookmarks show their favicon", async () => {
    const bridge = overlayBridge();
    const view = await openOverlay({
      cascade: "1",
      width: "280",
      items: JSON.stringify([
        { id: "p:bookmark:paper", label: "Paper", iconUrl: "https://paper.example/icon.png" },
        { id: "p:folder:drafts", label: "Drafts", icon: "folder", children: [{ id: "p:bookmark:draft", label: "Draft", iconUrl: "https://d.example/favicon.ico" }] },
      ]),
    });
    const menu = view.container.querySelector("[role=menu]");
    assert.equal(menu.getAttribute("data-width"), "280", "the opener's finite width is kept so titles truncate");
    const trigger = menu.querySelector("[aria-haspopup=menu]");
    assert.equal(trigger.textContent, "Drafts");
    assert.equal(trigger.querySelector("[data-glyph]").getAttribute("data-glyph"), "folder");
    const rows = [...menu.querySelectorAll("[role=menuitem]:not([aria-haspopup])")];
    assert.deepEqual(rows.map((row) => row.querySelector("[data-favicon]")?.getAttribute("data-favicon")), [
      "https://paper.example/icon.png", "https://d.example/favicon.ico",
    ]);
    await view.act(() => rows[1].click());
    assert.deepEqual(bridge.chosen, ["p:bookmark:draft"]);
    await view.unmount();
  });

  test("a flat menu keeps an icon slot only for rows with an icon, draws key then check at the end, and skips disabled rows", async () => {
    const bridge = overlayBridge();
    const view = await openOverlay({
      items: JSON.stringify([
        { id: "copy", label: "Copy", glyph: "copy", keys: { key: "C", mod: true } },
        { id: "plain", label: "Plain" },
        { id: "bar", label: "Show bar", checked: true, separatorBefore: true },
        { id: "off", label: "Off", disabled: true },
        { id: "unknown", label: "Bad glyph", glyph: "not-a-glyph" },
      ]),
    });
    const rows = [...view.container.querySelectorAll("[role=menu] > div > [role^=menuitem]")];
    assert.deepEqual(rows.map((row) => row.querySelector("[data-glyph]")?.getAttribute("data-glyph") ?? null), ["copy", null, null, null, null]);
    assert.equal(rows[0].lastElementChild.querySelector("kbd").textContent, "⌘C");
    assert.equal(rows[2].getAttribute("role"), "menuitemcheckbox");
    assert.equal(rows[2].getAttribute("aria-checked"), "true");
    assert.equal(rows[2].lastElementChild.tagName.toLowerCase(), "svg", "the check sits after the label, not in the icon slot");
    await view.act(() => { rows[3].click(); rows[1].click(); });
    assert.deepEqual(bridge.chosen, ["plain"]);
    await view.unmount();
  });

  test("a flat menu reports its intrinsic size, not the size its host currently clips it to", async () => {
    const bridge = overlayBridge();
    const view = await openOverlay({ items: JSON.stringify([{ id: "a", label: "A long label" }]) });
    const panel = view.container.querySelector("[role=menu]");
    setRect(panel, { width: 180, height: 100 });
    Object.defineProperties(panel, { scrollWidth: { value: 320 }, scrollHeight: { value: 640 } });
    resizeObservers.trigger(panel);
    assert.deepEqual(bridge.sizes.at(-1), { width: 320, height: 640 });
    await view.unmount();
  });

  test("the opener can replace the items of an open menu", async () => {
    const bridge = overlayBridge();
    const view = await openOverlay({ cascade: "1", items: JSON.stringify([{ id: "a", label: "Old" }]) });
    await view.act(() => bridge.update({ items: [{ id: "b", label: "New" }] }));
    assert.deepEqual([...view.container.querySelectorAll("[role=menuitem]")].map((row) => row.textContent), ["New"]);
    await view.unmount();
  });
});

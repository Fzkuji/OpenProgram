import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, memoryStorage, render } from "../_support/dom.mjs";

const DROPDOWN = `
import { createElement as h } from "react";
const pass = ({ children }) => h("div", null, children);
export const DropdownMenu = pass;
export const DropdownMenuTrigger = ({ children }) => children;
export const DropdownMenuContent = ({ children }) => h("div", { role: "menu" }, children);
export const DropdownMenuItem = ({ children, onSelect, disabled }) =>
  h("div", { role: "menuitem", "aria-disabled": disabled ? "true" : undefined, onClick: () => { if (!disabled) onSelect?.(); } }, children);
export const DropdownMenuSeparator = () => h("hr");
export const DropdownMenuSub = pass;
export const DropdownMenuSubTrigger = pass;
export const DropdownMenuSubContent = pass;
`;
// The source pickers are radix Selects; a native select keeps the value flow.
const SELECT = `
import { createElement as h } from "react";
export const SimpleSelect = ({ value, onValueChange, options, ...rest }) =>
  h("select", { value, "aria-label": rest["aria-label"], onChange: (e) => onValueChange(e.target.value) },
    options.map((o) => h("option", { key: o.value, value: o.value }, o.label)));
`;
const POPOVER = `
import { createElement as h } from "react";
export const Popover = ({ children }) => h("div", null, children);
export const PopoverTrigger = ({ children }) => children;
export const PopoverContent = () => null;
`;

installLoader({
  replace: {
    "/components/ui/dropdown-menu.tsx": DROPDOWN,
    "/components/ui/select.tsx": SELECT,
    "/components/ui/popover.tsx": POPOVER,
    "/components/center-tabs/strip/tab-favicon.tsx": "export const TabFavicon = () => null;",
  },
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/lib/desktop/desktop-bridge": "export const desktopBridge = () => globalThis.__bridge ?? null;",
    "@/lib/prefs/appearance/theme-pref": "export const activeThemeId = () => 'light';",
    "@/components/solar-icons": "export const SolarIcon = () => null;",
    "@/components/animated-icons": "export const ChromeIcon = () => null;",
    "@/components/ui/tooltip": "export const HoverTip = ({ children }) => children; export const TipBody = () => null;",
    // No site answers for its favicon here, so tiles fall back to their initial.
    "@/lib/browser/favicon-cache": "export const cachedFavicon = () => undefined; export const loadFavicon = async (url) => { (globalThis.__faviconRequests ??= []).push(url); return null; };",
    "@/lib/tabs/center-tabs-store": [
      "const calls = (globalThis.__tabCalls ??= []);",
      "const state = { openBuiltinTab: (page) => calls.push(['builtin', page]), openWebTab: (url) => calls.push(['web', url]) };",
      "export const useCenterTabs = Object.assign((selector) => selector(state), { getState: () => state });",
      "export const normalizeWebUrl = (value) => value.trim() ? (/^https?:/.test(value) ? value : `https://${value.trim()}`) : null;",
    ].join("\n"),
  },
});
const window = installDom();
globalThis.HTMLFormElement = window.HTMLFormElement;
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.getComputedStyle = () => ({ columnGap: "0px", getPropertyValue: () => "" });

const { createElement } = await import("react");
const prefs = await import("@/lib/browser/browser-prefs");
const { BrowserHomePage } = await import("@/components/center-tabs/browser/browser-home-page");

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const byLabel = (root, label) => root.querySelector(`button[aria-label="${label}"]`);
const byText = (root, text) => [...root.querySelectorAll("button")].find((button) => button.textContent === text);

const SOURCES = [{
  id: "chrome", name: "Google Chrome",
  profiles: [{ id: "p1", name: "Person 1", available: { history: true, bookmarks: true, cookies: false } }],
}];

function importApi() {
  const calls = { run: [], cancel: [] };
  let finish;
  return {
    calls,
    finish: (result) => finish(result),
    api: {
      listSources: async () => SOURCES,
      run: (request) => { calls.run.push(request); return new Promise((resolve) => { finish = resolve; }); },
      cancel: async (requestId) => { calls.cancel.push(requestId); return true; },
    },
  };
}

async function mount() {
  const view = await render(createElement(BrowserHomePage));
  await view.act(flush);
  return view;
}

beforeEach(() => {
  globalThis.localStorage = window.localStorage = memoryStorage({ agentic_locale: "en" });
  globalThis.sessionStorage = window.sessionStorage = memoryStorage();
  globalThis.__tabCalls.length = 0;
});
afterEach(() => {
  delete globalThis.__bridge;
});

describe("toolbar", () => {
  test("the page controls are shown but disabled, since there is no page yet", async () => {
    const view = await mount();
    for (const label of ["Back", "Forward", "Reload", "Home", "Bookmark", "Open in browser"]) {
      const button = byLabel(view.container, label);
      assert.ok(button, `${label} is shown`);
      assert.equal(button.disabled, true, `${label} is disabled`);
    }
    await view.unmount();
  });

  test("the browser menu on the home page greys out Home, Forward and Open in browser", async () => {
    const opened = [];
    globalThis.__bridge = { mainMenu: { open: (payload) => opened.push(payload), onAction: () => () => {}, onClosed: () => () => {}, close() {} } };
    const view = await mount();
    await view.act(() => byLabel(view.container, "Browser menu").click());
    const items = Object.fromEntries(opened[0].items.map((item) => [item.id.split(":").at(-1), item]));
    assert.equal(items.home.disabled, true);
    assert.equal(items.forward.disabled, true);
    assert.equal(items["open-external"].disabled, true);
    await view.unmount();
  });

  test("the library button opens the bookmarks manager", async () => {
    const view = await mount();
    await view.act(() => byLabel(view.container, "Bookmarks").click());
    assert.deepEqual(globalThis.__tabCalls, [["builtin", "bookmarks"]]);
    await view.unmount();
  });

  test("Enter in the address field opens the address in a web tab", async () => {
    const view = await mount();
    const input = view.container.querySelector("input[aria-label=Address]");
    input.type ||= "text"; // linkedom has no default type, and React only tracks typed inputs.
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, "example.com");
    await view.act(() => input.dispatchEvent(new Event("input", { bubbles: true })));
    await view.act(() => {
      const enter = new Event("keydown", { bubbles: true, cancelable: true });
      Object.assign(enter, { key: "Enter" });
      input.dispatchEvent(enter);
    });
    assert.deepEqual(globalThis.__tabCalls, [["web", "https://example.com"]]);
    await view.unmount();
  });
});

describe("shortcut tiles", () => {
  test("a tile without a site icon shows its initial, never a remote favicon image", async () => {
    globalThis.__faviconRequests = [];
    const view = await mount();
    assert.equal(view.container.querySelector("img"), null);
    assert.ok(globalThis.__faviconRequests.includes("https://github.com/favicon.ico"));
    assert.ok(
      globalThis.__faviconRequests.every((url) => new URL(url).pathname === "/favicon.ico"),
      "icons come from each site itself, never a third-party favicon service",
    );
    const tile = [...view.container.querySelectorAll("button")]
      .find((button) => button.querySelector("[class~=ntpTileLabel]")?.textContent === "GitHub");
    assert.equal(tile.querySelector("[class~=ntpTileInitial]").textContent, "G");
    await view.act(() => tile.click());
    assert.deepEqual(globalThis.__tabCalls, [["web", "https://github.com"]]);
    await view.unmount();
  });
});

describe("browser data import", () => {
  test("without the desktop import bridge there is no import prompt or button", async () => {
    const view = await mount();
    assert.equal(view.container.querySelector("section[aria-label='Import browser data']"), null);
    assert.equal(byLabel(view.container, "Import browser data"), null);
    await view.unmount();
  });

  test("the first visit offers the import; dismissing it is remembered", async () => {
    globalThis.__bridge = { browserImport: importApi().api };
    const view = await mount();
    assert.ok(view.container.querySelector("section[aria-label='Import browser data']"));
    await view.act(() => byLabel(view.container, "Dismiss").click());
    assert.equal(view.container.querySelector("section[aria-label='Import browser data']"), null);
    assert.equal(prefs.browserImportPromptFinished(), true);
    await view.unmount();

    const again = await mount();
    assert.equal(again.container.querySelector("section[aria-label='Import browser data']"), null);
    await again.unmount();
  });

  test("a manual import request shows the prompt again once", async () => {
    globalThis.__bridge = { browserImport: importApi().api };
    prefs.markBrowserImportPromptFinished();
    prefs.requestBrowserImport();
    const view = await mount();
    assert.ok(view.container.querySelector("section[aria-label='Import browser data']"));
    await view.unmount();
    assert.equal(prefs.consumeBrowserImportRequest(), false);
  });

  test("an import runs the available items, and Cancel cancels that very request", async () => {
    const browserImport = importApi();
    globalThis.__bridge = { browserImport: browserImport.api };
    const view = await mount();
    await view.act(() => byText(view.container, "Import").click());
    await view.act(flush);
    await view.act(() => byText(view.container, "Import").click());
    const [request] = browserImport.calls.run;
    assert.deepEqual({ ...request, requestId: typeof request.requestId }, {
      requestId: "string", browserId: "chrome", profileId: "p1", items: ["history", "bookmarks"],
    });
    assert.equal(byLabel(view.container, "Dismiss").disabled, true, "no dismissing while busy");
    await view.act(async () => { byText(view.container, "Cancel").click(); await flush(); });
    assert.deepEqual(browserImport.calls.cancel, [request.requestId]);
    assert.ok(byText(view.container, "Cancelling…"));
    await view.act(async () => { browserImport.finish({ ok: false, error: "import_cancelled" }); await flush(); });
    assert.equal(view.container.querySelector("[role=status]").textContent, "Import cancelled");
    assert.equal(prefs.browserImportPromptFinished(), false, "a cancelled import leaves the prompt open");
    await view.unmount();
  });

  test("a finished import reports the counts and marks the prompt finished", async () => {
    const browserImport = importApi();
    globalThis.__bridge = { browserImport: browserImport.api };
    const view = await mount();
    await view.act(() => byText(view.container, "Import").click());
    await view.act(flush);
    await view.act(() => byText(view.container, "Import").click());
    await view.act(async () => {
      browserImport.finish({
        ok: true, history: { imported: 12 }, cookies: { imported: 0 },
        bookmarks: [{ kind: "bookmark", title: "Docs", url: "https://docs.example" }],
        source: { label: "Google Chrome · Person 1" },
      });
      await flush();
    });
    assert.equal(view.container.querySelector("[role=status]").textContent, "Imported 12 history entries, 1 bookmarks, and 0 cookies.");
    assert.equal(prefs.browserImportPromptFinished(), true);
    await view.unmount();
  });
});

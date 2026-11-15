/**
 * The chat pane's two perspectives, transcript and session context graph:
 * the per-tab switch in the center-tab store, the ViewControls toggle and
 * session menu that drive it, and the CSS that swaps them without moving
 * the composer.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { afterEach, describe, test } from "node:test";
import postcss from "postcss";
import { installLoader, webRoot } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  replace: {
    // Plain stand-ins for the Radix tooltip and menu: every item is a button
    // that runs its onSelect, so the menu's actions can be clicked directly.
    "/components/ui/tooltip.tsx": [
      "import { createElement, Fragment } from 'react';",
      "export const HoverTip = ({ children }) => createElement(Fragment, null, children);",
      "export const TipBody = ({ title }) => title;",
    ].join("\n"),
    "/components/ui/dropdown-menu.tsx": [
      "import { createElement, Fragment } from 'react';",
      "const pass = ({ children }) => createElement(Fragment, null, children);",
      "export const DropdownMenu = pass, DropdownMenuTrigger = pass;",
      "export const DropdownMenuContent = ({ children, onKeyDown }) => createElement('div', { role: 'menu', onKeyDown }, children);",
      "export const DropdownMenuItem = ({ children, onSelect, disabled }) => createElement('button', { role: 'menuitem', disabled, onClick: () => onSelect?.() }, children);",
      "export const DropdownMenuSeparator = () => null;",
    ].join("\n"),
    "/components/center-tabs/pip/pip-dock-slot.tsx": "export const PipDockSlot = () => null;",
    "/components/chat/pane/rename-dialog.tsx": [
      "import { createElement } from 'react';",
      "export const RenameDialog = ({ onSubmit }) => createElement('button', { 'data-rename-submit': '', onClick: () => onSubmit('Renamed chat') });",
    ].join("\n"),
  },
});
installDom({ pathname: "/s/chat-1" });
globalThis.WebSocket ??= { OPEN: 1 };

const { createElement: h } = await import("react");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");
const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { ViewControls } = await import("../../components/chat/pane/view-controls.tsx");

const sessionTab = (id, extra = {}) => ({ id: `s:${id}`, kind: "session", title: id, sessionId: id, ...extra });

describe("the per-tab perspective in the center-tab store", () => {
  test("setTabDagView flips one tab and leaves the others on the transcript", () => {
    useCenterTabs.setState({ tabs: [sessionTab("one"), sessionTab("two")], groups: [], activeId: "s:one" });
    useCenterTabs.getState().setTabDagView("s:one", true);
    const [one, two] = useCenterTabs.getState().tabs;
    assert.equal(one.dagView, true);
    assert.equal(two.dagView, undefined);
  });

  test("setting the same perspective again is a no-op that keeps the tab objects", () => {
    useCenterTabs.setState({ tabs: [sessionTab("one", { dagView: true })], groups: [], activeId: "s:one" });
    const before = useCenterTabs.getState().tabs;
    useCenterTabs.getState().setTabDagView("s:one", true);
    useCenterTabs.getState().setTabDagView("s:missing", true);
    assert.equal(useCenterTabs.getState().tabs, before);
  });
});

describe("ViewControls", () => {
  let view;
  let sent;
  afterEach(async () => {
    await view?.unmount();
    view = undefined;
    setSocket(null);
  });
  async function mount(tab) {
    sent = [];
    setSocket({ readyState: 1, send: (data) => sent.push(JSON.parse(data)) });
    useCenterTabs.setState({ tabs: [tab], groups: [], activeId: tab.id });
    useSessionStore.setState({ conversations: { [tab.sessionId]: { id: tab.sessionId, title: "Chat one" } } });
    view = await render(h(ViewControls));
  }
  const toggle = () => view.container.querySelector("#sessionPerspectiveToggle");
  const item = (label) => [...view.container.querySelectorAll('[role="menuitem"]')].find((node) => node.textContent.includes(label));

  test("the toggle names the current perspective and what a click does, and flips the active tab", async () => {
    await mount(sessionTab("chat-1"));
    assert.equal(toggle().getAttribute("aria-label"), "Current view: conversation. Click to switch to the context graph view.");
    assert.equal(toggle().getAttribute("aria-pressed"), "false");
    await view.act(async () => toggle().click());
    assert.equal(useCenterTabs.getState().tabs[0].dagView, true);
    assert.equal(toggle().getAttribute("aria-label"), "Current view: context graph. Click to switch to the conversation view.");
    assert.equal(toggle().getAttribute("aria-pressed"), "true");
  });

  test("only a session tab has a perspective", async () => {
    sent = [];
    useCenterTabs.setState({ tabs: [{ id: "w:x", kind: "web", title: "X", url: "https://x.test/" }], groups: [], activeId: "w:x" });
    view = await render(h(ViewControls));
    assert.equal(view.container.innerHTML, "");
  });

  test("the session menu reuses the Recents row's websocket verbs", async () => {
    await mount(sessionTab("chat-1"));
    await view.act(async () => item("Pin").click());
    await view.act(async () => item("Archive").click());
    await view.act(async () => item("Rename").click());
    await view.act(async () => view.container.querySelector("[data-rename-submit]").click());
    assert.deepEqual(sent, [
      { action: "update_session_flags", session_id: "chat-1", pinned: true },
      { action: "update_session_flags", session_id: "chat-1", archived: true },
      { action: "rename_session", session_id: "chat-1", title: "Renamed chat" },
    ]);
    assert.equal(useCenterTabs.getState().tabs[0].title, "Renamed chat", "the tab is renamed with the chat");
  });

  test("Copy conversation copies up to HEAD into a new conversation and opens it", async () => {
    await mount(sessionTab("chat-1"));
    const fetches = [];
    globalThis.fetch = async (url, init) => {
      fetches.push({ url, body: JSON.parse(init.body) });
      return { ok: true, status: 200, json: async () => ({ ok: true, new_session_id: "chat-copy", title: "Chat one (copy)" }) };
    };
    await view.act(async () => item("Copy conversation").click());
    await view.act(async () => { for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0)); });
    assert.deepEqual(fetches, [{ url: "/api/chat/branch_session", body: { session_id: "chat-1" } }],
      "no message id: the server copies up to HEAD");
    assert.equal(useSessionStore.getState().conversations["chat-copy"].title, "Chat one (copy)");
    const active = useCenterTabs.getState().tabs.find((tab) => tab.id === useCenterTabs.getState().activeId);
    assert.equal(active.sessionId, "chat-copy", "the copy opens");
    assert.equal(sent.length, 0, "the source conversation is not touched");
  });

  test("a refused copy toasts and opens nothing", async () => {
    await mount(sessionTab("chat-1"));
    globalThis.fetch = async () => ({ ok: false, status: 400, statusText: "Bad Request",
      json: async () => ({ ok: false, error: "this conversation has no finished turn to copy yet" }) });
    await view.act(async () => item("Copy conversation").click());
    await view.act(async () => { for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0)); });
    assert.equal(useCenterTabs.getState().activeId, "s:chat-1");
    assert.equal(useSessionStore.getState().conversations["chat-copy"], undefined);
  });

  test("a draft chat keeps the toggle but has no session menu", async () => {
    await mount(sessionTab("chat-1", { draft: true }));
    assert.ok(toggle());
    assert.equal(view.container.querySelector('[role="menu"]'), null);
  });
});

describe("the perspective swap in CSS", () => {
  const dir = new URL("app/styles/dag/", webRoot);
  const css = readdirSync(dir).filter((name) => name.endsWith(".css")).map((name) => readFileSync(new URL(name, dir), "utf8")).join("\n");
  const root = postcss.parse(css);
  const declarations = (selector) => {
    const found = {};
    root.walkRules((rule) => {
      if (rule.selectors?.includes(selector)) rule.walkDecls((decl) => { found[decl.prop] = decl.value; });
    });
    return found;
  };

  test("the graph perspective hides only the transcript's scroll area, never #chatView and its composer", () => {
    assert.equal(declarations('.center-pane-chat[data-center-view="dag"] #chatArea').display, "none");
    assert.deepEqual(declarations('.center-pane-chat[data-center-view="dag"] #chatView'), {});
  });

  test("the composer keeps one centred column in both perspectives", () => {
    // The graph perspective once pushed the input, its chips, the controls
    // row and the queued cards against the right margin, so the same box
    // sat in two places depending on the view.
    const parts = [
      "components/chat/composer/composer.module.css",
      "components/chat/composer/environment-row/environment-row.module.css",
      "components/chat/messages/editing/queued-messages.module.css",
    ];
    for (const part of parts) {
      const sheet = postcss.parse(readFileSync(new URL(part, webRoot), "utf8"));
      sheet.walkRules((rule) => {
        assert.ok(!rule.selector.includes("data-center-view"), `${part}: ${rule.selector}`);
      });
    }
    const column = postcss.parse(readFileSync(new URL(parts[0], webRoot), "utf8"));
    let margin;
    column.walkRules((rule) => {
      if (rule.selectors?.includes(".inputWrapper") && rule.selectors.includes(".decisionColumn")) {
        rule.walkDecls("margin", (decl) => { margin = decl.value; });
      }
    });
    assert.equal(margin, "0 auto");
  });

  test("the graph covers the pane so the composer keeps its bottom anchor", () => {
    assert.equal(declarations('.center-pane-chat[data-center-view="dag"] .dag-view').position, "absolute");
  });
});

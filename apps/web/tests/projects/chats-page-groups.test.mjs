// The /chats list (and a project's Chats tab) folds a long history: each
// recency group is a disclosure, older groups start closed, and an open
// group shows a preview before "Show all".
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "next/link": "import { createElement } from 'react'; export default ({ href, children, prefetch, ...props }) => createElement('a', { href, ...props }, children);",
  },
  replace: {
    "/lib/session-store/index.ts": [
      "export const useSessionStore = (selector) => selector(globalThis.chatsStore);",
      "useSessionStore.getState = () => globalThis.chatsStore;",
    ].join("\n"),
    "/lib/tabs/center-tabs-store.ts": [
      "export const useCenterTabs = { getState: () => ({ openSessionTab: (...args) => globalThis.chatsOpened.push(args) }) };",
    ].join("\n"),
    "/lib/nav/shallow-nav.ts": "export const pushPath = (path) => globalThis.chatsPushed.push(path);",
    "/lib/runtime-bridge/session/session-load.ts": "export const sendRuntimeCommand = () => true;",
    "/components/ui/select.tsx": "export const SimpleSelect = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { ChatsPage } = await import("../../components/chats/chats-page.tsx");

const DAY = 86400;
const now = Math.floor(Date.now() / 1000);
const old = now - 400 * DAY; // A previous year, so never the newest group.

function conversations() {
  const out = {};
  // Created long ago, active today: must sit under Today.
  out.revived = { id: "revived", title: "Revived chat", created_at: old, updated_at: now };
  for (let i = 0; i < 12; i++) out[`old-${i}`] = { id: `old-${i}`, title: `Old chat ${i}`, created_at: old - i * 60 };
  return out;
}

const groupButton = (container, label) =>
  [...container.querySelectorAll("button[aria-expanded]")].find((button) => button.textContent.startsWith(label));

test("today's group starts open, an older group starts closed, and toggling it reveals a preview then all rows", async () => {
  globalThis.chatsStore = { conversations: conversations() };
  globalThis.chatsOpened = [];
  globalThis.chatsPushed = [];
  const view = await render(h(ChatsPage, { embedded: true, rail: false }));
  const { container } = view;

  const today = groupButton(container, "Today");
  assert.ok(today, "a Today group exists");
  assert.equal(today.getAttribute("aria-expanded"), "true");
  const todayPanel = container.querySelector(`#${today.getAttribute("aria-controls")}`);
  assert.ok(todayPanel, "aria-controls points at the open group's panel");
  assert.match(todayPanel.textContent, /Revived chat/, "a chat active today buckets by activity, not creation");

  const older = [...container.querySelectorAll("button[aria-expanded]")].find((button) => button !== today);
  assert.equal(older.getAttribute("aria-expanded"), "false");
  assert.equal(container.querySelector(`#${older.getAttribute("aria-controls")}`), null, "a closed group renders no panel");

  await view.act(() => older.click());
  assert.equal(older.getAttribute("aria-expanded"), "true");
  const panel = container.querySelector(`#${older.getAttribute("aria-controls")}`);
  const rows = () => [...panel.querySelectorAll("button")].filter((button) => /Old chat/.test(button.textContent));
  assert.equal(rows().length, 8, "an open group previews its first rows");
  const showAll = [...panel.querySelectorAll("button")].find((button) => button.textContent === "Show all 12");
  assert.ok(showAll, "a long group offers Show all");

  await view.act(() => showAll.click());
  assert.equal(rows().length, 12);
  assert.ok([...panel.querySelectorAll("button")].some((button) => button.textContent === "Show fewer"));

  await view.act(() => rows()[0].click());
  assert.deepEqual(globalThis.chatsOpened[0], ["old-0", "Old chat 0"]);
  assert.deepEqual(globalThis.chatsPushed, ["/s/old-0"]);
  await view.unmount();
});

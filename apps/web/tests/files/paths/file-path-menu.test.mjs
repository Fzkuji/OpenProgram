/**
 * The file menu every chat file shares: its items, in order, and what
 * each does with the file.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/chat/messages/editing/quote-to-chat": "export const appendToComposer = (...args) => { (globalThis.__composer ??= []).push(args); };",
    "@/lib/clipboard": "export const copyText = async (value) => { (globalThis.__copied ??= []).push(value); return true; };",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
    "@/lib/files/local-file-url": "export const canBrowseLocalFiles = () => globalThis.__local;",
    "@/lib/files/reveal-file": "export const canRevealFiles = () => globalThis.__local; export const revealLabel = () => 'Reveal in Finder'; export const revealAbsolutePath = async (path) => { (globalThis.__revealed ??= []).push(path); return true; };",
  },
});
installDom({ pathname: "/chat" });
globalThis.getComputedStyle = window.getComputedStyle = () => ({ getPropertyValue: () => "", display: "block", animationName: "none" });
window.HTMLElement.prototype.hasPointerCapture ??= () => false;
window.HTMLElement.prototype.releasePointerCapture ??= () => {};
globalThis.DOMRect ??= class { constructor(x = 0, y = 0, width = 0, height = 0) { Object.assign(this, { x, y, width, height, top: y, left: x, right: x + width, bottom: y + height }); } static fromRect(r = {}) { return new DOMRect(r.x, r.y, r.width, r.height); } };

const { createElement } = await import("react");
const { FilePathContextMenu, fileMention } = await import("../../../components/files/file-path-menu.tsx");

async function openMenu(local) {
  globalThis.__local = local;
  window.openprogramDesktop = local ? { openFileInDefaultApp: async (path) => { (globalThis.__defaultOpened ??= []).push(path); return true; } } : undefined;
  const opened = [];
  const view = await render(createElement(FilePathContextMenu, {
    path: "/work/repo/exports/plan.pptx", sessionId: "s1", project: { id: "p1", path: "/work/repo" }, onOpen: () => opened.push(1),
  }, createElement("figure", { id: "card" }, "plan.pptx")));
  await view.act(async () => {
    const event = new window.Event("contextmenu", { bubbles: true, cancelable: true });
    Object.assign(event, { clientX: 10, clientY: 10, button: 2 });
    view.container.querySelector("#card").dispatchEvent(event);
  });
  const labels = () => [...document.querySelectorAll('[data-slot="context-menu-item"], [data-slot="context-menu-sub-trigger"]')].map(item => item.textContent);
  return { view, labels, opened };
}

test("on this machine the menu offers preview, copy, reveal, default app and attach", async () => {
  const { view, labels } = await openMenu(true);
  assert.deepEqual(labels(), ["Open preview", "Copy", "Reveal in Finder", "Open in default app", "Attach as context"]);
  await view.unmount();
});

test("from another machine only what works there is offered", async () => {
  const { view, labels } = await openMenu(false);
  assert.deepEqual(labels(), ["Open preview", "Copy", "Attach as context"]);
  await view.unmount();
});

test("attach adds the file to the message box as a mention", async () => {
  const { view } = await openMenu(true);
  const attach = [...document.querySelectorAll('[data-slot="context-menu-item"]')].find(item => item.textContent === "Attach as context");
  await view.act(async () => { attach.click(); });
  assert.deepEqual(globalThis.__composer.at(-1), ["s1", "@exports/plan.pptx"]);
  assert.equal(fileMention("/a/Q3 deck.pptx"), '@"/a/Q3 deck.pptx"');
  await view.unmount();
});

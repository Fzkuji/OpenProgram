import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    // What the pane publishes about its native view lands in __bounds.
    "@/lib/desktop/desktop-bridge": [
      "const log = { push: (entry) => globalThis.__bounds.push(entry) };",
      "export const registerVisibleWebTabBounds = (bridge, id, bounds) => log.push(['register', id, bounds]);",
      "export const removeVisibleWebTabBounds = (bridge, id) => log.push(['remove', id]);",
      "export const setWebTabReady = () => {};",
      "export const ensureWebView = () => {};",
      "export const destroyStaleWebViews = () => {};",
      "export const installDesktopMenuHandlers = () => {};",
    ].join("\n"),
    // A pane always measures as a large, unoccluded, still rectangle.
    "@/lib/browser/web-tab/web-tab-bounds": [
      "export const measureWebTabBounds = () => globalThis.__measured;",
      "export const measureWebTabCorners = () => null;",
      "export const isWebTabOccluded = () => false;",
      "export const isMovedByAnimation = () => false;",
    ].join("\n"),
    "@/lib/format-utils/toast": "export const showToast = () => {};",
    "@/components/chat/messages/editing/quote-to-chat": "export const appendToComposer = () => {};",
    "@/lib/browser/capture/page-comment": "export const pageCommentText = () => ''; export const pickedElementName = () => '';",
    "@/lib/browser/capture/region-shot": "export const regionFile = () => null;",
    "@/lib/chat/composer-inbox": "export const queueComposerFiles = () => {};",
    "@/lib/chat/resources/target-chat": "export const targetChat = () => null;",
    "@/lib/tabs/center-tabs-store": [
      "const state = { tabs: [], updateWebTab() {}, setActive() {}, replaceWebTabWithNewTabPage() {} };",
      "export const useCenterTabs = Object.assign((selector) => selector(state), { getState: () => state });",
      "export const normalizeWebUrl = (value) => value;",
    ].join("\n"),
    "@/lib/browser/web-tab/web-tab-pip-store": "export const collapseWebTabToPip = () => {};",
    "@/lib/browser/control/browser-control": [
      "export const useBrowserControlStore = () => undefined;",
      "export const browserTakeoverKind = () => null; export const presentedControlState = () => 'idle';",
      "export const revealPendingApproval = () => {}; export const setControlNotice = () => {}; export const toggleShowActions = () => {};",
      "export const operationHistory = () => []; export const resumeErrorFor = () => undefined; export const showActionsEnabled = () => true;",
    ].join("\n"),
    "@/lib/chat/resources/session-resources": "export const useBrowserResourceStore = () => undefined;",
    "@/lib/browser/control/browser-action-cue": "export const controlSurfaceVisible = () => false;",
    "../toolbar/browser-controls": "export const BookmarksLibraryButton = () => null; export const BrowserMenu = () => null;",
    "../toolbar/view-source-button": "export const ViewSourceButton = () => null;",
    "../bookmark-bar": "export const BookmarkBar = () => null;",
    "../toolbar/browser-chrome": "export const AddressField = () => null; export const ToolbarTip = ({ children }) => children;",
    "./pane-controls": [
      "import { createElement as h } from 'react';",
      "export const BookmarkButton = () => null; export const findButton = () => null; export const resourceForLiveTab = () => null;",
      "export const usePipCollapseTarget = () => null; export const CollapseToPipButton = () => null; export const HomeButton = () => null;",
      "export const WebPaneStage = ({ children }) => h('div', null, children);",
    ].join("\n"),
  },
});
const window = installDom();
globalThis.MutationObserver = window.MutationObserver ?? class { observe() {} disconnect() {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });

const { createElement } = await import("react");
const motion = await import("@/lib/tabs/canvas/canvas-motion");
const { DesktopWebTabPane } = await import("@/components/center-tabs/browser/pane/desktop-web-tab-pane");

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const BIG = { x: 10, y: 50, width: 800, height: 600 };
const registered = () => globalThis.__bounds.filter(([kind]) => kind === "register");

function bridge() {
  const off = () => () => {};
  return {
    openExternal() {},
    webTab: {
      onState: off, onFindResult: off, onCommand: off,
      navigate() {}, reload() {}, stop() {}, goBack() {}, goForward() {},
      // The snapshot taken when the pane freezes.
      capture: async () => "data:image/png;base64,AAAA",
    },
  };
}

beforeEach(() => {
  globalThis.__bounds = [];
  globalThis.__measured = BIG;
  motion.setCanvasDragFlag(false);
  motion.setCanvasAnimating(false);
});

test("while canvas panes move the pane stops publishing native-view bounds, and resumes when they stop", async () => {
  const view = await render(createElement(DesktopWebTabPane, { suspended: false, bridge: bridge(), tabId: "w:https://a.test/", url: "https://a.test/", menuOwnerId: "pane-a" }));
  assert.deepEqual(registered().at(-1), ["register", "w:https://a.test/", BIG], "a still pane publishes its bounds");

  await view.act(async () => { motion.setCanvasDragFlag(true); await flush(); });
  const during = registered().length;
  globalThis.__measured = { ...BIG, x: 300 };
  await view.act(async () => { window.dispatchEvent(new Event("resize")); await flush(); });
  assert.equal(registered().length, during, "no bounds while the canvas is dragged");
  assert.deepEqual(globalThis.__bounds.at(-1), ["remove", "w:https://a.test/"], "the native view is taken off screen behind its snapshot");
  assert.ok(view.container.querySelector("img[alt='Frozen webpage']"), "the snapshot stands in for the page");

  await view.act(async () => { motion.setCanvasDragFlag(false); await flush(); });
  assert.deepEqual(registered().at(-1), ["register", "w:https://a.test/", { ...BIG, x: 300 }], "bounds follow the pane's new place");
  assert.equal(view.container.querySelector("img[alt='Frozen webpage']"), null);
  await view.unmount();
});

test("an animated layout change freezes the bounds the same way", async () => {
  const view = await render(createElement(DesktopWebTabPane, { suspended: false, bridge: bridge(), tabId: "w:b", url: "https://b.test/", menuOwnerId: "pane-b" }));
  const before = registered().length;
  await view.act(async () => { motion.setCanvasAnimating(true); await flush(); });
  await view.act(async () => { window.dispatchEvent(new Event("resize")); await flush(); });
  assert.equal(registered().length, before);
  await view.act(async () => { motion.setCanvasAnimating(false); await flush(); });
  assert.ok(registered().length > before, "bounds are published again once the animation ends");
  await view.unmount();
});

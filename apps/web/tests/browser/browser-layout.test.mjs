import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { installLoader } from "../_support/load.mjs";

installLoader();
const layout = await import("@/lib/browser/browser-layout");

describe("responsive browser menu", () => {
  test("a wide pane keeps Home, Forward and Open in browser on the toolbar", () => {
    assert.deepEqual(layout.browserResponsiveMenuItems(720, { forward: true }), { home: false, forward: false, openExternal: false });
  });

  test("a medium pane moves Home and Open in browser into the menu", () => {
    assert.deepEqual(layout.browserResponsiveMenuItems(600, { forward: true }), { home: true, forward: false, openExternal: true });
  });

  test("a narrow pane also moves Forward, only when the page can go forward", () => {
    assert.deepEqual(layout.browserResponsiveMenuItems(500, { forward: true }), { home: true, forward: true, openExternal: true });
    assert.deepEqual(layout.browserResponsiveMenuItems(500, { forward: false }), { home: true, forward: false, openExternal: true });
  });
});

describe("page shortcuts", () => {
  for (const [key, action] of [["f", "find"], ["+", "zoom-in"], ["=", "zoom-in"], ["-", "zoom-out"], ["0", "reset-zoom"], ["p", "print"]]) {
    test(`Cmd or Ctrl + ${key} is ${action}`, () => {
      assert.equal(layout.browserPageShortcut({ key, metaKey: true, ctrlKey: false }), action);
      assert.equal(layout.browserPageShortcut({ key, metaKey: false, ctrlKey: true }), action);
    });
  }

  test("a key without Cmd or Ctrl is not a page shortcut", () => {
    assert.equal(layout.browserPageShortcut({ key: "f", metaKey: false, ctrlKey: false }), null);
  });
});

describe("menu action ownership", () => {
  const tabId = "w:https://example.test/path?token=SECRET#fragment";

  test("a browser menu prefix is per pane and never carries the tab's url", () => {
    const mine = layout.browserActionPrefix("pane-a");
    const other = layout.browserActionPrefix("pane-b");
    assert.equal(mine.includes(tabId), false);
    assert.equal(layout.ownedActionId(`${mine}history`, mine), "history");
    assert.equal(layout.ownedActionId(`${other}history`, mine), null);
  });

  test("a bookmark folder prefix is per pane and per folder", () => {
    const mine = layout.bookmarkFolderActionPrefix("pane-a", "folder-1");
    const otherPane = layout.bookmarkFolderActionPrefix("pane-b", "folder-1");
    const otherFolder = layout.bookmarkFolderActionPrefix("pane-a", "folder-2");
    assert.equal(mine.includes(tabId), false);
    assert.equal(layout.ownedActionId(`${mine}bookmark:node-7`, mine), "bookmark:node-7");
    assert.equal(layout.ownedActionId(`${mine}folder:node-8`, mine), "folder:node-8");
    assert.equal(layout.ownedActionId(`${mine}empty:node-9`, mine), "empty:node-9");
    assert.equal(layout.ownedActionId(`${otherPane}bookmark:node-7`, mine), null);
    assert.equal(layout.ownedActionId(`${otherFolder}bookmark:node-7`, mine), null);
  });
});

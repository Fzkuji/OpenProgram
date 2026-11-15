import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { installLoader } from "../_support/load.mjs";
import { memoryStorage } from "../_support/dom.mjs";

installLoader();

globalThis.window = new EventTarget();
const prefs = await import("@/lib/browser/browser-prefs");

beforeEach(() => {
  globalThis.localStorage = memoryStorage();
  globalThis.sessionStorage = memoryStorage();
});

test("the bookmarks bar shows by default and follows the saved choice", () => {
  assert.equal(prefs.showBookmarksBar(), true);
  prefs.setShowBookmarksBar(false);
  assert.equal(prefs.showBookmarksBar(), false);
  prefs.setShowBookmarksBar(true);
  assert.equal(prefs.showBookmarksBar(), true);
});

test("changing the bookmarks bar notifies subscribers in this window and from other windows", () => {
  let calls = 0;
  const unsubscribe = prefs.subscribeBrowserPrefs(() => calls++);
  prefs.setShowBookmarksBar(false);
  assert.equal(calls, 1);
  const storageEvent = (key) => Object.defineProperty(new Event("storage"), "key", { value: key });
  window.dispatchEvent(storageEvent(prefs.SHOW_BOOKMARKS_BAR_KEY));
  assert.equal(calls, 2);
  window.dispatchEvent(storageEvent("something.else"));
  assert.equal(calls, 2);
  unsubscribe();
  prefs.setShowBookmarksBar(true);
  assert.equal(calls, 2);
});

test("the automatic import prompt is offered until it is marked finished", () => {
  assert.equal(prefs.browserImportPromptFinished(), false);
  prefs.markBrowserImportPromptFinished();
  assert.equal(prefs.browserImportPromptFinished(), true);
});

test("a manual import request is consumed exactly once", () => {
  assert.equal(prefs.consumeBrowserImportRequest(), false);
  prefs.requestBrowserImport();
  assert.equal(prefs.consumeBrowserImportRequest(), true);
  assert.equal(prefs.consumeBrowserImportRequest(), false);
});

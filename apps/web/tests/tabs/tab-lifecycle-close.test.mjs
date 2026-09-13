import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const lifecycle = readFileSync(new URL("../../components/center-tabs/use-tab-lifecycle.ts", import.meta.url), "utf8");
const menu = readFileSync(new URL("../../components/center-tabs/use-tab-menu.ts", import.meta.url), "utf8");
const strip = readFileSync(new URL("../../components/center-tabs/center-tab-strip.tsx", import.meta.url), "utf8");

test("every human tab close entry uses onTabsClose", () => {
  assert.match(lifecycle, /function onTabClose\(e: React\.SyntheticEvent, tab: CenterTab\) \{\s*onTabsClose\(e, \[tab\]\);/);
  assert.match(lifecycle, /window\.addEventListener\("op-desktop-close-tab", onDesktopClose\)/);
  assert.match(lifecycle, /onTabsCloseRef\.current\(/);
  assert.match(menu, /onTabsClose\(\s*\{ stopPropagation: \(\) => \{\} \} as React\.SyntheticEvent,\s*victims,/);
  assert.match(strip, /onClose=\{onTabsClose\}/);
  assert.match(strip, /onClose=\{onTabClose\}/);
});

test("human onTabsClose waits on the shared browser close helper before animating removal", () => {
  const close = lifecycle.slice(
    lifecycle.indexOf("async function onTabsClose"),
    lifecycle.indexOf("const onTabsCloseRef"),
  );
  assert.match(close, /selectTabsReadyForHumanClose\(tabsToClose, useCenterTabs\.getState\(\)\.tabs\)/);
  assert.ok(
    close.indexOf("window.confirm") < close.indexOf("selectTabsReadyForHumanClose"),
    "a cancelled dirty prompt must not pause or close a browser Page",
  );
  assert.match(close, /if \(ready\.length === 0\) return;/);
  assert.match(close, /for \(const tab of ready\) closingInstances\.current\.set\(tab\.id, tab\)/);
  assert.doesNotMatch(close, /for \(const tab of tabsToClose\) closingInstances/);
  const finish = lifecycle.slice(lifecycle.indexOf("function finishClose"));
  assert.match(finish, /closeTab\(tab\.id\)/);
  assert.doesNotMatch(finish, /selectTabsReadyForHumanClose|requestCloseBrowserPage/);
});

test("internal conversation reap prunes history without human-close side effects", () => {
  const reap = lifecycle.slice(
    lifecycle.indexOf("const prevConvIds"),
    lifecycle.indexOf("function activateSession"),
  );
  assert.match(reap, /useCenterTabs\.getState\(\)\.removeSessionFromHistory\(id\)/);
  assert.doesNotMatch(reap, /selectTabsReadyForHumanClose|requestCloseBrowserPage|onTabsClose/);
});

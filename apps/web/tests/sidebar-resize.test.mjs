import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sidebar = readFileSync(
  new URL("../components/sidebar/sidebar.tsx", import.meta.url),
  "utf8",
);
const rightSidebar = readFileSync(
  new URL("../components/right-sidebar/right-sidebar.tsx", import.meta.url),
  "utf8",
);
const primaryNav = readFileSync(
  new URL("../components/sidebar/sidebar-primary-nav.tsx", import.meta.url),
  "utf8",
);
const detailPanel = readFileSync(
  new URL("../components/right-sidebar/detail-panel.tsx", import.meta.url),
  "utf8",
);
const resizableRail = readFileSync(
  new URL("../components/layout/use-resizable-rail.ts", import.meta.url),
  "utf8",
);
const shell = readFileSync(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);

test("left and right sidebars share one resize implementation", () => {
  assert.match(sidebar, /useResizableRail\(\{[\s\S]*direction:\s*1/);
  assert.match(rightSidebar, /useResizableRail\(\{[\s\S]*direction:\s*-1/);
  assert.match(resizableRail, /const COLLAPSED_RAIL_WIDTH = 49/);
  assert.match(resizableRail, /setPointerCapture\(event\.pointerId\)/);
  assert.doesNotMatch(sidebar, /document\.addEventListener\("mousemove"/);
  assert.doesNotMatch(rightSidebar, /document\.addEventListener\("mousemove"/);
  assert.doesNotMatch(shell, /handleId:\s*"sidebarResize"/);
  assert.doesNotMatch(shell, /id="sidebarResize"/);
});

test("side rail shells delegate navigation and detail content", () => {
  assert.match(sidebar, /<SidebarPrimaryNav\s*\/>/);
  assert.doesNotMatch(sidebar, /refreshFunctionsList|href="\/programs"/);
  assert.match(primaryNav, /href="\/programs"/);
  assert.match(rightSidebar, /<DetailPanel\s*\/>/);
  assert.doesNotMatch(
    rightSidebar,
    /function DetailPanel|function DetailBlock/,
  );
  assert.match(detailPanel, /export function DetailPanel/);
});

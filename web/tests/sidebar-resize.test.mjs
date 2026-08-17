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
const resizableRail = readFileSync(
  new URL("../components/layout/use-resizable-rail.ts", import.meta.url),
  "utf8",
);
const shell = readFileSync(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);

test("left and right sidebars share one resize implementation", () => {
  assert.match(
    sidebar,
    /useResizableRail\(\{[\s\S]*direction:\s*1/,
  );
  assert.match(
    rightSidebar,
    /useResizableRail\(\{[\s\S]*direction:\s*-1/,
  );
  assert.match(resizableRail, /const COLLAPSED_RAIL_WIDTH = 49/);
  assert.match(resizableRail, /setPointerCapture\(event\.pointerId\)/);
  assert.doesNotMatch(sidebar, /document\.addEventListener\("mousemove"/);
  assert.doesNotMatch(rightSidebar, /document\.addEventListener\("mousemove"/);
  assert.doesNotMatch(shell, /handleId:\s*"sidebarResize"/);
  assert.doesNotMatch(shell, /id="sidebarResize"/);
});

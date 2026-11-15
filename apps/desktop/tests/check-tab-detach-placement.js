// Drop-to-place tab tear-off in the main process: the new window is created
// hidden at release, placed at the drop point clamped to the work area, kept
// ephemeral and size-capped, revealed at commit, and closed again when the
// transfer is rejected or rolled back. (The renderer side is covered by
// apps/web/tests/tabs/tab-pointer-drag.test.mjs.)
const assert = require("node:assert/strict");

const { readMainSource } = require("../scripts/main-source");
const { DETACHED_MAX_WIDTH, DETACHED_MAX_HEIGHT } = require("../main/windows/window-state");

const main = readMainSource();
function slice(from, to) {
  const start = main.indexOf(from);
  assert.ok(start >= 0, `${from} not found`);
  return main.slice(start, to ? main.indexOf(to, start) : undefined);
}

// Placement: centred on the cursor, clamped so it never spawns offscreen.
assert.match(slice("function centerHiddenWindowOnCursor", "function showWindowSmoothly"), /clamp\s*\?[\s\S]*?Math\.max\(rawX/,
  "the drop-point placement must clamp to the work area");
assert.match(main, /centerHiddenWindowOnCursor\(destination\.win\);/,
  "the torn-off window is positioned at the drop point (clamped by default)");

// Release can race the window-at-cursor hit test: concurrent detaches share one boot.
assert.match(main, /if \(transaction\.detachPromise\) return transaction\.detachPromise;/,
  "concurrent detaches must share one in-flight window boot");

// Revealed at commit, once the destination renderer has staged the tab.
assert.match(main, /const detached = liveContext\(transaction\.detachedWindowId\);[\s\S]*?if \(detached\) showWindowSmoothly\(detached\.win\)/,
  "the torn-off window is revealed at commit, so it never flashes empty");

// Detached windows: the shared size plan with its modest cap, and no persisted window state.
assert.equal(DETACHED_MAX_WIDTH, 1100);
assert.equal(DETACHED_MAX_HEIGHT, 720);
assert.match(main, /browserWindowOptionsForPlan\(state, \{ detached \}\)/,
  "detached windows use the shared window-state size plan");
assert.match(main, /if \(!detached\) \{[\s\S]*?attachWindowStatePersistence/,
  "detached windows stay ephemeral and do not persist window state");

// A rejected or rolled-back transfer closes its staged window: no orphans.
assert.match(main, /function clearActive\([^)]*\) \{[\s\S]*?if \(closeHidden\) closeDetached\(transaction\.detachedWindowId\)/,
  "a rejected/rolled-back transfer must close its staged window");

console.log("tab detach placement checks passed");

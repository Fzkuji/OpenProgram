const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { cascadeMenuGeometry } = require("../main/windows/menu-geometry.js");

const source = require("../scripts/main-source").readMainSource();
const paneSource = fs.readFileSync(
  require.resolve("../../web/components/center-tabs/browser/pane/desktop-web-tab-pane.tsx"),
  "utf8",
);

function declaration(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is missing`);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  assert.fail(`${name} is incomplete`);
}

const listeners = new Map();
const visibleCalls = [];
const cornerSyncCalls = [];
const setBoundsCalls = [];
const pipZoomCalls = [];
const menuOpenCalls = [];
const menuResizeCalls = [];
const terminalRegistrations = [];
const clipboardRegistrations = [];
const context = {};
const unexpectedTerminalEffect = () => assert.fail("zoom checks must not launch or control a terminal");
const sandbox = {
  // This harness extracts just the geometry and registration functions. The
  // shared terminal module has its own production-IPC tests; record its
  // registration here without starting a PTY, timers, or an HTTP connection.
  require(name) {
    if (name === "./main/web-views/clipboard") {
      return { registerClipboardIpc: (...args) => clipboardRegistrations.push(args) };
    }
    assert.equal(name, "./main/terminal/terminal-resource-ipc");
    return { registerTerminalResourceIpc: options => terminalRegistrations.push(options) };
  },
  app: {},
  clipboard: { writeText: () => assert.fail("zoom checks must not write the clipboard") },
  nativeMenuOwner: () => assert.fail("zoom checks must not invoke clipboard authorization"),
  UI_ORIGIN: "http://127.0.0.1:18100",
  process: { platform: "linux", env: {} },
  fs: {},
  killWindowsProcessTree: unexpectedTerminalEffect,
  waitForTerminalPid: unexpectedTerminalEffect,
  setTimeout: unexpectedTerminalEffect,
  clearTimeout: unexpectedTerminalEffect,
  setInterval: unexpectedTerminalEffect,
  clearInterval: unexpectedTerminalEffect,
  ipcMain: {
    on: (channel, handler) => listeners.set(channel, handler),
    handle: () => {},
  },
  contextForSender: () => context,
  contextForMenuSender: () => context,
  openMainMenu: (receivedContext, options, zoom) => {
    menuOpenCalls.push([receivedContext, options, zoom]);
  },
  resizeMenuOverlay: (receivedContext, size) => {
    menuResizeCalls.push([receivedContext, size]);
  },
  setPipZoom: (...args) => pipZoomCalls.push(args),
  syncVisibleViews: (receivedContext, items) => {
    visibleCalls.push([receivedContext, items]);
    return true;
  },
  currentVisibleItems: () => [{ id: "zoomed", bounds: setBoundsCalls.at(-1), corners: null }],
  recordFor: (receivedContext, id) => ({ id }),
  syncWebCorners: (receivedContext, items) => cornerSyncCalls.push(items),
  withView: (receivedContext, id, callback) => {
    assert.equal(receivedContext, context);
    assert.equal(id, "zoomed");
    callback({ view: { setBounds: (bounds) => setBoundsCalls.push(bounds) } });
  },
};
vm.runInNewContext(
  `${declaration("normalizedBounds")}\n${declaration("rendererZoomFactor")}\n`
    + `${declaration("normalizedRendererBounds")}\n`
    + `${declaration("normalizedRendererMenuOptions")}\n`
    + `${declaration("registerWebTabIpc")}\n`
    + "this.normalizedRendererBounds = normalizedRendererBounds;"
    + "this.normalizedRendererMenuOptions = normalizedRendererMenuOptions;"
    + "this.registerWebTabIpc = registerWebTabIpc;",
  sandbox,
);
const plain = (value) => JSON.parse(JSON.stringify(value));

assert.deepEqual(
  plain(cascadeMenuGeometry(
    { x: 250, y: 80 },
    1250,
    875,
    1.25,
  )),
  {
    bounds: { x: 0, y: 80, width: 1250, height: 795 },
    anchor: { x: 200, y: 0 },
  },
  "cascading menu hosts must begin below the bookmark bar and use a view-local anchor",
);
assert.deepEqual(
  plain(cascadeMenuGeometry({ x: -50, y: -20 }, 800, 600, 1)),
  {
    bounds: { x: 0, y: 0, width: 800, height: 600 },
    anchor: { x: 0, y: 0 },
  },
  "top and left overflow must fall back to a usable host and clamp the local anchor",
);
assert.deepEqual(
  plain(cascadeMenuGeometry({ x: 900, y: 590 }, 800, 600, 1)),
  {
    bounds: { x: 0, y: 0, width: 800, height: 600 },
    anchor: { x: 799, y: 590 },
  },
  "an anchor near the bottom must use the full usable window instead of a clipped strip",
);
assert.deepEqual(
  plain(cascadeMenuGeometry({ x: 100, y: 700 }, 800, 600, 1)),
  {
    bounds: { x: 0, y: 0, width: 800, height: 600 },
    anchor: { x: 100, y: 599 },
  },
  "an anchor below the window must stay inside a usable host viewport",
);
assert.deepEqual(
  plain(cascadeMenuGeometry({ x: 1500, y: 850 }, 1250, 875, 1.25)),
  {
    bounds: { x: 0, y: 0, width: 1250, height: 875 },
    anchor: { x: 999, y: 680 },
  },
  "edge fallback must clamp in CSS pixels after applying a non-unit zoom",
);

const screenshotBounds = {
  x: 812.6333618164062,
  y: 79.99031066894531,
  width: 518.6390380859375,
  height: 696.86279296875,
};

assert.deepEqual(
  plain(sandbox.normalizedRendererBounds(
    { sender: { getZoomFactor: () => 1.095445115 } },
    screenshotBounds,
  )),
  { x: 890, y: 88, width: 568, height: 763 },
  "renderer CSS pixels must be converted to native DIP at the sender zoom",
);
assert.deepEqual(
  plain(sandbox.normalizedRendererBounds(
    { sender: { getZoomFactor: () => 1 } },
    { x: 10.4, y: 20.6, width: 300.2, height: 400.8 },
  )),
  { x: 10, y: 21, width: 300, height: 401 },
  "100% zoom must preserve the existing bounds behavior",
);
assert.deepEqual(
  plain(sandbox.normalizedRendererBounds(
    { sender: { getZoomFactor: () => Number.NaN } },
    { x: 10, y: 20, width: 30, height: 40 },
  )),
  { x: 10, y: 20, width: 30, height: 40 },
  "an invalid zoom factor must safely fall back to 1",
);

assert.deepEqual(
  plain(sandbox.normalizedRendererMenuOptions(
    { sender: { getZoomFactor: () => 1.25 } },
    {
      anchor: { x: 100, right: 900, y: 64, rightInset: 10, top: 44, vw: 1000, vh: 700 },
      width: 200,
      height: 100,
      items: [{ id: "settings", label: "Settings" }],
    },
  )),
  {
    anchor: { x: 125, right: 1125, y: 80, rightInset: 12.5, top: 55, vw: 1250, vh: 875 },
    width: 250,
    height: 125,
    items: [{ id: "settings", label: "Settings" }],
  },
  "menu anchors and optional dimensions must convert renderer CSS pixels to Electron DIP",
);

sandbox.registerWebTabIpc();
assert.equal(clipboardRegistrations.length, 1, "clipboard remains wired into Desktop registration");
assert.equal(clipboardRegistrations[0][0], sandbox.ipcMain);
assert.equal(clipboardRegistrations[0][1], sandbox.clipboard);
assert.equal(clipboardRegistrations[0][2], sandbox.nativeMenuOwner);
assert.equal(terminalRegistrations.length, 1, "shared terminals remain wired into Desktop registration");
assert.equal(terminalRegistrations[0].ipcMain, sandbox.ipcMain);
assert.equal(terminalRegistrations[0].contextForSender, sandbox.contextForSender);
assert.equal(terminalRegistrations[0].origin, sandbox.UI_ORIGIN);
const event = { sender: { getZoomFactor: () => 1.095445115 } };
listeners.get("main-menu:open")({ sender: { getZoomFactor: () => 1.25 } }, {
  anchor: { rightInset: 10, top: 44, vw: 1000, vh: 700 },
});
assert.deepEqual(plain(menuOpenCalls), [[context, {
  anchor: {
    rightInset: 12.5,
    top: 55,
    vw: 1250,
    vh: 700 * 1.25,
  },
}, 1.25]]);
listeners.get("main-menu:resize")(
  { sender: { getZoomFactor: () => 1.25 } },
  { width: 200, height: 100 },
);
assert.deepEqual(plain(menuResizeCalls), [[context, {
  x: 0,
  y: 0,
  width: 250,
  height: 125,
}]]);
listeners.get("webtab:sync-visible")(event, [{ id: "zoomed", bounds: screenshotBounds }]);
assert.equal(visibleCalls.length, 1);
assert.equal(visibleCalls[0][0], context);
assert.deepEqual(plain(visibleCalls[0][1]), [{
  id: "zoomed",
  bounds: { x: 890, y: 88, width: 568, height: 763 },
  corners: null,
}]);
const corners = { radius: 12, outside: "rgb(1, 2, 3)", shadow: "" };
listeners.get("webtab:sync-visible")(event, [{ id: "zoomed", bounds: screenshotBounds, corners }]);
assert.deepEqual(plain(visibleCalls[1][1][0].corners), { ...corners, zoom: 1.095445115 },
  "corner masks carry the sender zoom so their CSS px match the page's DIPs");

listeners.get("webtab:set-pip-zoom")(event, "zoomed", screenshotBounds.width, screenshotBounds.height);
assert.deepEqual(plain(pipZoomCalls[0]), [context, "zoomed", 568, 763],
  "preview scale and native bounds use the same rounded DIP at non-integral app zoom");

listeners.get("webtab:set-bounds")(event, "zoomed", screenshotBounds);
assert.deepEqual(plain(setBoundsCalls), [
  { x: 890, y: 88, width: 568, height: 763 },
]);
assert.deepEqual(plain(cornerSyncCalls), [[{
  id: "zoomed",
  bounds: { x: 890, y: 88, width: 568, height: 763 },
  corners: null,
  record: { id: "zoomed" },
}]], "a page that moves takes its corner masks along");

assert.match(
  paneSource,
  /window\.addEventListener\("resize", report(AndFollow)?\)/,
  "shell zoom and window resize must trigger a fresh DOM bounds report",
);

const openMainMenuSource = declaration("openMainMenu");
assert.match(openMainMenuSource, /MAIN_MENU_WIDTH \* menuZoom/);
assert.match(openMainMenuSource, /MAIN_MENU_GUTTER \* menuZoom/);
assert.match(openMainMenuSource, /CONTEXT_MENU_WIDTH \* menuZoom/);
assert.match(openMainMenuSource, /view\.webContents\.setZoomFactor\(menuZoom\)/);
assert.match(
  openMainMenuSource,
  /overlayAnchor = \{\s*x: \(Number\(anchor\.x\) \|\| 0\) \/ menuZoom,\s*y: \(Number\(anchor\.y\) \|\| 0\) \/ menuZoom,/,
  "nested-menu CSS anchors must be converted back after renderer DIP normalization",
);
assert.match(
  openMainMenuSource,
  /const cascadeMenu = Boolean\(opts && opts\.cascade\)/,
  "bookmark folders must request the cascading host even without child folders",
);
assert.match(openMainMenuSource, /view\.setBounds\(cascadeGeometry\.bounds\)/);
const resizeMenuOverlaySource = declaration("resizeMenuOverlay");
assert.match(resizeMenuOverlaySource, /MAIN_MENU_GUTTER \* zoom/);

// The App at 90%, 100% and 110%: the renderer reports CSS pixels, and the
// page, its preview scale and the menu anchors all land in window pixels at
// the App's zoom, rounded the same way, so a page sits exactly in its frame.
const pipBox = { x: 827.28, y: 93.5, width: 280, height: 156.25 };
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-9, `${message}: ${actual} != ${expected}`);
for (const [zoom, expected] of [
  [0.9, { x: 745, y: 84, width: 252, height: 141 }],
  [1, { x: 827, y: 94, width: 280, height: 156 }],
  [1.1, { x: 910, y: 103, width: 308, height: 172 }],
]) {
  const atZoom = { sender: { getZoomFactor: () => zoom } };
  const synced = visibleCalls.length;
  listeners.get("webtab:sync-visible")(atZoom, [{ id: "zoomed", bounds: pipBox, corners }]);
  assert.deepEqual(plain(visibleCalls[synced][1]), [{ id: "zoomed", bounds: expected, corners: { ...corners, zoom } }],
    `the page's bounds at ${zoom * 100}% are its frame's CSS box in window pixels`);
  const zoomed = pipZoomCalls.length;
  listeners.get("webtab:set-pip-zoom")(atZoom, "zoomed", pipBox.width, pipBox.height);
  assert.deepEqual(plain(pipZoomCalls[zoomed]), [context, "zoomed", expected.width, expected.height],
    `at ${zoom * 100}% the preview lays its page out at the size its bounds give it`);
  const placed = setBoundsCalls.length;
  listeners.get("webtab:set-bounds")(atZoom, "zoomed", pipBox);
  assert.deepEqual(plain(setBoundsCalls[placed]), expected, `a page laid out ahead of showing goes where it will show at ${zoom * 100}%`);
  const menu = sandbox.normalizedRendererMenuOptions(atZoom, {
    anchor: { x: 100, right: 900, y: 64, rightInset: 10, top: 44, vw: 1000, vh: 700, side: "bottom" },
    width: 200,
    height: 100,
  });
  for (const [key, css] of Object.entries({ x: 100, right: 900, y: 64, rightInset: 10, top: 44, vw: 1000, vh: 700 })) {
    near(menu.anchor[key], css * zoom, `menu anchor ${key} at ${zoom * 100}%`);
  }
  assert.equal(menu.anchor.side, "bottom", "anchor fields that are not lengths pass through");
  near(menu.width, 200 * zoom, `menu width at ${zoom * 100}%`);
  near(menu.height, 100 * zoom, `menu height at ${zoom * 100}%`);
}

console.log("webtab zoom bounds checks passed");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("../main.js"), "utf8");
const paneSource = fs.readFileSync(
  require.resolve("../../web/components/center-tabs/web-tab-pane.tsx"),
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
const setBoundsCalls = [];
const menuOpenCalls = [];
const menuResizeCalls = [];
const context = {};
const sandbox = {
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
  syncVisibleViews: (receivedContext, items) => {
    visibleCalls.push([receivedContext, items]);
    return true;
  },
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
const event = { sender: { getZoomFactor: () => 1.095445115 } };
listeners.get("main-menu:open")({ sender: { getZoomFactor: () => 1.25 } }, {
  anchor: { rightInset: 10, top: 44, vw: 1000, vh: 700 },
});
assert.deepEqual(plain(menuOpenCalls), [[context, {
  anchor: {
    rightInset: 12.5,
    top: 55,
    vw: 1250,
    vh: 875,
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
}]);

listeners.get("webtab:set-bounds")(event, "zoomed", screenshotBounds);
assert.deepEqual(plain(setBoundsCalls), [
  { x: 890, y: 88, width: 568, height: 763 },
]);

assert.match(
  paneSource,
  /window\.addEventListener\("resize", report\)/,
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
  /if \(nestedItems\) \{\s*view\.setBounds\(\{ x: 0, y: 0, width: Math\.round\(winW\), height: Math\.round\(winH\) \}\);/,
  "nested submenus need a full-window overlay so portals are not clipped",
);
const resizeMenuOverlaySource = declaration("resizeMenuOverlay");
assert.match(resizeMenuOverlaySource, /MAIN_MENU_GUTTER \* zoom/);

console.log("webtab zoom bounds checks passed");

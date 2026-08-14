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
const context = {};
const sandbox = {
  ipcMain: {
    on: (channel, handler) => listeners.set(channel, handler),
    handle: () => {},
  },
  contextForSender: () => context,
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
  `${declaration("normalizedBounds")}\n${declaration("normalizedRendererBounds")}\n`
    + `${declaration("registerWebTabIpc")}\n`
    + "this.normalizedRendererBounds = normalizedRendererBounds;"
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

sandbox.registerWebTabIpc();
const event = { sender: { getZoomFactor: () => 1.095445115 } };
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

console.log("webtab zoom bounds checks passed");

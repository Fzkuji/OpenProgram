"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { recordOpenWindows, takeOpenWindows } = require("../main/windows/session-windows");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "op-session-windows-"));
const file = path.join(dir, "session-windows.json");

recordOpenWindows(file, [
  { windowId: "window-abc", bounds: { x: 10, y: 20, width: 900, height: 600 } },
  { windowId: "main", bounds: { x: 0, y: 0, width: 1, height: 1 } },
  { windowId: "window-../x" },
  { windowId: "window-def" },
]);
assert.deepEqual(takeOpenWindows(file), [
  { windowId: "window-abc", bounds: { x: 10, y: 20, width: 900, height: 600 } },
  { windowId: "window-def", bounds: null },
], "only torn-off window ids are kept");
assert.deepEqual(takeOpenWindows(file), [], "the list is used once");

recordOpenWindows(file, []);
assert.equal(fs.existsSync(file), false, "no windows leaves no file");
fs.writeFileSync(file, "{broken");
assert.deepEqual(takeOpenWindows(file), [], "a broken file reopens nothing");
assert.equal(fs.existsSync(file), false);

console.log("session windows checks passed");

// Rule: the desktop preload exposes Electron's webUtils.getPathForFile and the
// web bridge types the same accessor, so a dropped file keeps its disk path.
// (The marker, envelope and path capture are tested in tests/chat/send/attachment-marker.test.mjs.)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const preload = read("../../../desktop/main/preload.js");
const bridgeApi = read("../../lib/desktop/bridge/bridge-api.ts");

assert.match(preload, /getPathForFile:\s*\(file\)\s*=>\s*webUtils\.getPathForFile\(file\)/,
  "the preload must expose Electron's webUtils.getPathForFile");
assert.match(bridgeApi, /getPathForFile\?\(file:\s*File\):\s*string/,
  "the desktop bridge must type the path accessor the preload exposes");

console.log("local attachment path wiring check passed");

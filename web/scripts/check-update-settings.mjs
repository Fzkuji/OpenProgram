import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const general = fs.readFileSync(path.join(root, "components/settings/general-section.tsx"), "utf8");
const bridge = fs.readFileSync(path.join(root, "lib/desktop-bridge.ts"), "utf8");

assert.doesNotMatch(general, />0\.1\.0</);
assert.match(general, /desktopBridge\(\)/);
assert.match(general, /updates\.getState/);
assert.match(general, /updates\.check/);
assert.match(general, /updates\.download/);
assert.match(general, /\/api\/system\/version/);
assert.match(bridge, /DesktopUpdateApi/);
assert.match(bridge, /updates:\s*DesktopUpdateApi/);

console.log("update settings checks passed");

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const { hasPrimaryModifier, isMacPlatform, platformKeyCaps, platformKeys } = await import("../../lib/shortcut-keys.ts");

const root = fileURLToPath(new URL("../../", import.meta.url));
const read = (path) => readFileSync(join(root, path), "utf8");

test("the shortcut key is ⌘ on macOS and Ctrl on Windows and Linux", () => {
  assert.equal(hasPrimaryModifier({ metaKey: true, ctrlKey: false }, true), true);
  assert.equal(hasPrimaryModifier({ metaKey: false, ctrlKey: true }, true), false, "Ctrl+click is a right click on macOS");
  assert.equal(hasPrimaryModifier({ metaKey: false, ctrlKey: true }, false), true);
  assert.equal(hasPrimaryModifier({ metaKey: true, ctrlKey: false }, false), false, "the Windows key belongs to the system");
});

test("a hint is written with the keys the platform has", () => {
  assert.equal(platformKeyCaps("⌘ Z", true), "⌘ Z");
  assert.equal(platformKeyCaps("⌘ Z", false), "Ctrl Z");
  assert.equal(platformKeyCaps("⇧ 2", false), "⇧ 2", "Shift is drawn the same on every keyboard");
  assert.equal(platformKeys("⌘", true) + "Enter", "⌘Enter");
  assert.equal(platformKeys("⌘", false) + "Enter", "Ctrl+Enter");
  assert.equal(`hold ${platformKeys("⌥", true)}Space`, "hold ⌥Space");
  assert.equal(`hold ${platformKeys("⌥", false)}Space`, "hold Alt+Space");
});

test("the platform is read from the browser, and a page with none is not macOS", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  try {
    for (const [platform, mac] of [["MacIntel", true], ["iPad", true], ["Win32", false], ["Linux x86_64", false]]) {
      Object.defineProperty(globalThis, "navigator", { value: { platform }, configurable: true });
      assert.equal(isMacPlatform(), mac, platform);
    }
  } finally {
    if (saved) Object.defineProperty(globalThis, "navigator", saved);
    else delete globalThis.navigator;
  }
});

function sources(directory) {
  return readdirSync(join(root, directory), { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sources(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

const code = (text) => text
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split("\n").filter((line) => !/^\s*(\/\/|\*)/.test(line)).join("\n")
  .replace(/\s\/\/ .*$/gm, "");

test("no file decides the platform or writes a Mac-only key by itself", () => {
  const offenders = [];
  for (const path of ["app", "components", "lib"].flatMap(sources)) {
    if (path === "lib/shortcut-keys.ts") continue;
    const text = code(read(path));
    if (/navigator\.platform/.test(text)) offenders.push(`${path}: reads navigator.platform`);
    // A literal ⌘/⌥/⌃ outside a platform branch reaches Windows and Linux as is.
    for (const line of text.split("\n")) {
      if (!/[⌘⌥⌃]/.test(line)) continue;
      if (/mac|platformKey|ELSEWHERE|tilingKeys/i.test(line)) continue;
      // The tiling table's keys are shown through `describe`, on macOS only.
      if (path.endsWith("canvas-controls.tsx") && /keys: "⌃⌥/.test(line)) continue;
      // A sort caret, not a key.
      if (path.endsWith("file-tree-columns.tsx") && line.includes('"⌃" : "⌄"')) continue;
      offenders.push(`${path}: ${line.trim().slice(0, 80)}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("selecting several graph nodes follows the platform's key", () => {
  const nodes = read("lib/runtime-bridge/dag/interaction/node/nodes.ts");
  const gestures = read("lib/runtime-bridge/dag/interaction/view/gestures.ts");
  assert.match(nodes, /if \(e\.shiftKey \|\| hasPrimaryModifier\(e\)\) \{\n\s+select\(\[id\], "toggle"\)/);
  assert.match(gestures, /add: e\.shiftKey \|\| hasPrimaryModifier\(e\)/);
  assert.doesNotMatch(code(nodes) + code(gestures), /shiftKey \|\| e\.metaKey/);
});

test("the tiling menu names its macOS keys only on macOS", () => {
  const canvas = read("components/center-tabs/canvas/canvas-controls.tsx");
  assert.match(canvas, /if \(mac && e\.ctrlKey && e\.altKey && !e\.metaKey\)/, "the keys are handled on macOS only");
  assert.match(canvas, /keys && tilingKeys \?/);
  assert.match(canvas, /Hold \$\{tilingKeys \? "Option" : "Alt"\} for quarters/, "the key is called Alt off macOS");
  assert.match(canvas, /tilingKeys \? <span[^>]*>⌃⌥R<\/span> : null/);
});

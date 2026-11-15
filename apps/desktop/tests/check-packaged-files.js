// Every local module the shell loads must be packed by electron-builder's
// "files" patterns. A module left out works from the checkout and stops the
// packaged App before its first window opens.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const packaged = require("./packaged-files").packagedFiles();
const entries = ["shell/loader.js", "main.js", "main/preload.js"];

function localRequires(file) {
  const source = fs.readFileSync(path.join(root, file), "utf8");
  // Composed modules under main/ that receive main.js's require resolve
  // relative to main.js, not to their own folder.
  const base = /^\s*require,\s*$/m.test(source) ? root : path.dirname(path.join(root, file));
  const found = [];
  for (const match of source.matchAll(/require\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/g)) {
    let target = path.relative(root, path.resolve(base, match[1]));
    if (!target.endsWith(".js") && !target.endsWith(".cjs") && !target.endsWith(".json")) target += ".js";
    found.push(target.split(path.sep).join("/"));
  }
  return found;
}

const seen = new Set();
const missing = [];
const queue = [...entries];
while (queue.length) {
  const file = queue.shift();
  if (seen.has(file)) continue;
  seen.add(file);
  if (!packaged.has(file)) missing.push(file);
  if (fs.existsSync(path.join(root, file))) queue.push(...localRequires(file));
}

assert.deepEqual(missing, [], `not in package.json build.files: ${missing.join(", ")}`);

// Load the entry against a stand-in Electron: a module that throws while
// loading leaves the App running with no window. The app never becomes
// ready here, so no window, worker or terminal starts.
const Module = require("node:module");
function standIn() {
  return new Proxy(function () { return standIn(); }, {
    get(_target, key) {
      if (key === Symbol.toPrimitive) return () => "";
      if (key === "then") return undefined;
      if (key === "whenReady") return () => new Promise(() => {});
      if (key === "requestSingleInstanceLock") return () => true;
      if (key === "getPath") return () => require("node:os").tmpdir();
      return standIn();
    },
    construct() { return standIn(); },
  });
}
const load = Module._load;
Module._load = function (request, ...rest) {
  if (request === "electron" || request === "node-pty") return standIn();
  return load.call(this, request, ...rest);
};
require(path.join(root, "main.js"));
Module._load = load;
console.log(`packaged files: ${seen.size} local modules are all packaged and main.js loads`);
process.exit(0);

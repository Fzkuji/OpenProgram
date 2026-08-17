#!/usr/bin/env node

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.resolve(__dirname, "..");
const repositoryRoot = path.resolve(desktopRoot, "..", "..");

function read(relativePath) {
  return fs.readFileSync(path.join(repositoryRoot, relativePath), "utf8");
}

function assertAbsent(source, pattern, label) {
  assert.doesNotMatch(source, pattern, label);
}

const desktopMain = read("apps/desktop/main.js");
const preload = read("apps/desktop/preload.js");
const packageJson = JSON.parse(read("apps/desktop/package.json"));
const desktopBridge = read("apps/web/lib/desktop-bridge.ts");
const builtinPane = read("apps/web/components/center-tabs/builtin-tab-pane.tsx");
const browserControls = read("apps/web/components/center-tabs/browser-controls.tsx");
const webTabPane = read("apps/web/components/center-tabs/web-tab-pane.tsx");
const builtinIds = read("apps/web/lib/state/center-tab-ids.ts");

assert.equal(
  fs.existsSync(path.join(desktopRoot, "browser-extension-manager.js")),
  false,
  "the desktop extension manager must not ship",
);
assert.equal(
  fs.existsSync(path.join(repositoryRoot, "apps/web/lib/browser-extension-store.ts")),
  false,
  "the renderer must not retain extension-store installation logic",
);

assertAbsent(desktopMain, /browser-extension-manager|extensions:(?:list|install|set-enabled|reload|remove)/, "desktop main must not load or expose browser extensions");
assertAbsent(preload, /extensions:\s*\{|extensions:(?:list|install|set-enabled|reload|remove)/, "preload must not expose browser extension IPC");
assertAbsent(desktopBridge, /DesktopBrowserExtension|DesktopBrowserExtensionsApi|extensions\?:/, "the public renderer bridge must not advertise extension support");
assertAbsent(builtinPane, /ExtensionsPage|DesktopBrowserExtension/, "the built-in page must not render extension management");
assertAbsent(browserControls, /openBuiltinTab\("extensions"\)|item\("extensions"|row\("extensions"/, "the browser menu must not offer an Extensions destination");
assertAbsent(webTabPane, /InstallExtensionButton|isExtensionStoreListing|installCurrentPage/, "store pages must not inject an OpenProgram install button");
assertAbsent(builtinIds, /"extensions"/, "Extensions must not be a built-in tab type");

assert.equal(packageJson.build.files.includes("browser-extension-manager.js"), false);
assert.equal(Object.hasOwn(packageJson.dependencies, "extract-zip"), false);

console.log("browser extension removal contract: PASS");

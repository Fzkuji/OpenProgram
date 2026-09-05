"use strict";

const fs = require("node:fs");
const path = require("node:path");

const packageRoot = path.dirname(require.resolve("node-pty/package.json"));
const platformRoot = path.join(
  packageRoot,
  "prebuilds",
  `${process.platform}-${process.arch}`,
);
const nativeModule = process.platform === "win32" ? "conpty.node" : "pty.node";
const nativePath = path.join(platformRoot, nativeModule);

if (!fs.existsSync(nativePath)) {
  throw new Error(
    `node-pty has no prebuilt ${process.platform}/${process.arch} module: ${nativePath}`,
  );
}
const nodePty = require("node-pty");
if (typeof nodePty.spawn !== "function") {
  throw new Error("node-pty prebuilt module did not load");
}

console.log(`node-pty prebuilt dependency ready: ${process.platform}/${process.arch}`);

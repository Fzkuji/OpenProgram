"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { openableFile, registerOpenFileIpc } = require("../main/files/open-default");

const files = {
  "/work/plan.pptx": { file: true, mode: 0o644 },
  "/work/run.sh": { file: true, mode: 0o644 },
  "/work/tool": { file: true, mode: 0o755 },
  "/work/folder": { file: false, mode: 0o755 },
};
const fs = { statSync: (p) => {
  const entry = files[p];
  if (!entry) throw new Error("ENOENT");
  return { isFile: () => entry.file, mode: entry.mode };
} };

test("only a plain, non-runnable file at an absolute path opens", () => {
  const env = { fs, path: path.posix, platform: "darwin" };
  assert.equal(openableFile("/work/plan.pptx", env), true);
  assert.equal(openableFile("work/plan.pptx", env), false, "relative");
  assert.equal(openableFile("/work/run.sh", env), false, "a script runs");
  assert.equal(openableFile("/work/tool", env), false, "an executable runs");
  assert.equal(openableFile("/work/folder", env), false, "a folder");
  assert.equal(openableFile("/work/missing.pdf", env), false, "missing");
  assert.equal(openableFile(42, env), false);
});

test("the handler answers only the window's own page", async () => {
  const handlers = new Map();
  const opened = [];
  const page = {};
  registerOpenFileIpc({
    ipcMain: { handle: (name, fn) => handlers.set(name, fn) },
    shell: { openPath: async (p) => { opened.push(p); return ""; } },
    fs, path: path.posix, platform: "darwin",
    contextForSender: (event) => (event.sender === page || event.sender === "site" ? { win: { webContents: page } } : null),
  });
  const open = handlers.get("files:open-default");
  assert.equal(await open({ sender: page }, "/work/plan.pptx"), true);
  assert.equal(await open({ sender: "site" }, "/work/plan.pptx"), false);
  assert.equal(await open({ sender: page }, "/work/run.sh"), false);
  assert.deepEqual(opened, ["/work/plan.pptx"]);
});
